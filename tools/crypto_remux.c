/* OpenDownloader local-only authorized-key demux/remux helper.
 * Linked against the exact private FFmpeg shared libraries. Keys enter only
 * through bounded stdin records, never command arguments or persisted files.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <fcntl.h>
#include <io.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include <libavformat/avformat.h>
#include <libavcodec/avcodec.h>
#include <libavutil/avutil.h>
#include <libavutil/dict.h>
#include <libavutil/error.h>
#include <libavutil/frame.h>
#include <libavutil/mem.h>
#include "sample_aes_packet.h"

#if LIBAVFORMAT_VERSION_MAJOR != 63
#error The helper requires the pinned private libavformat 63 ABI.
#endif
#define KEY_LIMIT 16
#define BYTE_LIMIT ((int64_t)64 * 1024 * 1024 * 1024)
#define IO_BUFFER 65536

typedef struct { char id[33]; uint8_t value[16]; } Key;
typedef struct { wchar_t root[32768]; Key keys[KEY_LIMIT]; int count; } State;
typedef struct { HANDLE file; } LocalIO;
typedef struct { char filename[64]; const uint8_t *key; uint8_t iv[16]; } SampleRow;

static int hex(char c) {
    if(c >= '0' && c <= '9') return c-'0';
    if(c >= 'a' && c <= 'f') return c-'a'+10;
    if(c >= 'A' && c <= 'F') return c-'A'+10;
    return -1;
}
static int load_keys(State *s) {
    char line[69];
    _setmode(_fileno(stdin), _O_BINARY);
    while(fgets(line, sizeof(line), stdin)) {
        size_t n=strlen(line);
        if(!n || line[n-1]!='\n') return -1;
        line[--n]=0;
        if(n && line[n-1]=='\r') line[--n]=0;
        if(n!=65 || line[32]!=':' || s->count==KEY_LIMIT) return -1;
        Key *k=&s->keys[s->count];
        for(int i=0;i<32;i++) {
            int v=hex(line[i]); if(v<0) return -1;
            k->id[i]="0123456789abcdef"[v];
        }
        for(int i=0;i<16;i++) {
            int a=hex(line[33+i*2]),b=hex(line[34+i*2]);
            if(a<0 || b<0) return -1;
            k->value[i]=(uint8_t)((a<<4)|b);
        }
        for(int i=0;i<s->count;i++) if(!strcmp(s->keys[i].id,k->id)) return -1;
        s->count++;
        SecureZeroMemory(line,sizeof(line));
    }
    SecureZeroMemory(line,sizeof(line));
    return ferror(stdin) || !s->count ? -1 : 0;
}
static char *utf8(const wchar_t *path) {
    int n=WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,path,-1,NULL,0,NULL,NULL);
    if(n<=0) return NULL;
    char *out=av_malloc(n);
    if(!out || !WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,path,-1,out,n,NULL,NULL)) {av_free(out);return NULL;}
    for(int i=0;i<n;i++) if(out[i]=='\\') out[i]='/';
    return out;
}
static int input_path(State *s,const wchar_t *path) {
    wchar_t full[32768];
    DWORD n=GetFullPathNameW(path,32768,full,NULL);
    if(!n || n>=32768 || wcsncmp(full,L"\\\\",2)==0 || wcslen(full)<4 || full[1]!=':') return -1;
    wchar_t *slash=wcsrchr(full,L'\\');
    if(!slash) return -1;
    *slash=0;
    if(wcslen(full)+1>=32768) return -1;
    wcscpy(s->root,full);
    return 0;
}
static int read_io(void *opaque,uint8_t *buffer,int size) {
    LocalIO *io=opaque;
    DWORD got=0;
    if(!ReadFile(io->file,buffer,(DWORD)size,&got,NULL)) return AVERROR(EIO);
    return got ? (int)got : AVERROR_EOF;
}
static int write_io(void *opaque,const uint8_t *buffer,int size) {
    LocalIO *io=opaque;
    DWORD wrote=0;
    if(!WriteFile(io->file,buffer,(DWORD)size,&wrote,NULL) || wrote!=(DWORD)size) return AVERROR(EIO);
    return size;
}
static int64_t seek_io(void *opaque,int64_t offset,int whence) {
    LocalIO *io=opaque;
    LARGE_INTEGER pos,size;
    if(whence==AVSEEK_SIZE) return GetFileSizeEx(io->file,&size) ? size.QuadPart : AVERROR(EIO);
    pos.QuadPart=offset;
    whence &= ~AVSEEK_FORCE;
    DWORD origin=whence==SEEK_SET ? FILE_BEGIN : whence==SEEK_CUR ? FILE_CURRENT : whence==SEEK_END ? FILE_END : (DWORD)-1;
    if(origin==(DWORD)-1 || !SetFilePointerEx(io->file,pos,&pos,origin) || pos.QuadPart<0 || pos.QuadPart>BYTE_LIMIT) return AVERROR(EIO);
    return pos.QuadPart;
}
static int close_io(AVFormatContext *ctx,AVIOContext *pb) {
    (void)ctx;
    if(!pb) return 0;
    LocalIO *io=pb->opaque;
    if(io && io->file && io->file!=INVALID_HANDLE_VALUE) CloseHandle(io->file);
    av_free(io);
    av_freep(&pb->buffer);
    avio_context_free(&pb);
    return 0;
}
static int make_io(AVIOContext **out,HANDLE file,int writing) {
    LocalIO *io=av_mallocz(sizeof(*io));
    uint8_t *buffer=av_malloc(IO_BUFFER);
    if(!io || !buffer) {av_free(io);av_free(buffer);if(file!=INVALID_HANDLE_VALUE) CloseHandle(file);return AVERROR(ENOMEM);}
    io->file=file;
    AVIOContext *pb=avio_alloc_context(buffer,IO_BUFFER,writing,io,writing?NULL:read_io,writing?write_io:NULL,seek_io);
    if(!pb) {av_free(io);av_free(buffer);if(file!=INVALID_HANDLE_VALUE) CloseHandle(file);return AVERROR(ENOMEM);}
    *out=pb;return 0;
}
static int open_file(AVIOContext **out,const wchar_t *path,int writing) {
    HANDLE file=CreateFileW(path,writing?GENERIC_WRITE:GENERIC_READ,writing?0:FILE_SHARE_READ,NULL,writing?CREATE_NEW:OPEN_EXISTING,FILE_FLAG_OPEN_REPARSE_POINT,NULL);
    if(file==INVALID_HANDLE_VALUE) return AVERROR(EACCES);
    BY_HANDLE_FILE_INFORMATION info;
    LARGE_INTEGER size;
    if(!GetFileInformationByHandle(file,&info) || (info.dwFileAttributes&(FILE_ATTRIBUTE_REPARSE_POINT|FILE_ATTRIBUTE_DIRECTORY)) || !GetFileSizeEx(file,&size) || size.QuadPart>BYTE_LIMIT) {CloseHandle(file);return AVERROR(EACCES);}
    return make_io(out,file,writing);
}
static int segment_name(const wchar_t *name) {
    const wchar_t *p=name;
    if(wcsncmp(p,L"streaming-seg-",14)) return 0;
    p+=14;
    if(*p<L'0'||*p>L'9') return 0;
    while(*p>=L'0'&&*p<=L'9') p++;
    if(*p++!=L'-' || *p<L'0'||*p>L'9') return 0;
    while(*p>=L'0'&&*p<=L'9') p++;
    return !wcscmp(p,L".bin");
}
static int sample_rows(State *s,const wchar_t *source,SampleRow **out,int *count) {
    AVIOContext *io=NULL;
    int ret=open_file(&io,source,0);
    if(ret<0) return ret;
    int64_t size=avio_size(io);
    if(size<=0 || size>1024*1024) {close_io(NULL,io);return AVERROR_INVALIDDATA;}
    char *data=av_malloc((size_t)size+1);
    SampleRow *rows=av_calloc(10000,sizeof(*rows));
    if(!data || !rows) {av_free(data);av_free(rows);close_io(NULL,io);return AVERROR(ENOMEM);}
    ret=avio_read(io,(uint8_t*)data,(int)size);close_io(NULL,io);
    if(ret!=size || memchr(data,0,(size_t)size)) {ret=AVERROR_INVALIDDATA;goto fail;}
    data[size]=0;
    char *cursor=data;int used=0;
    while(*cursor) {
        char *end=strchr(cursor,'\n');
        if(!end || used==10000 || end-cursor>130) {ret=AVERROR_INVALIDDATA;goto fail;}
        *end=0;if(end>cursor && end[-1]=='\r') end[-1]=0;
        char *kid=strchr(cursor,'\t');
        if(!kid) {ret=AVERROR_INVALIDDATA;goto fail;}
        *kid++=0;
        char *iv=strchr(kid,'\t');
        if(!iv || strchr(iv+1,'\t') || strlen(cursor)>=sizeof(rows[used].filename)) {ret=AVERROR_INVALIDDATA;goto fail;}
        *iv++=0;
        wchar_t filename[64];
        for(size_t i=0;i<=strlen(cursor);i++) {
            if((unsigned char)cursor[i]>127) {ret=AVERROR_INVALIDDATA;goto fail;}
            filename[i]=(wchar_t)cursor[i];
        }
        if(!segment_name(filename)) {ret=AVERROR_INVALIDDATA;goto fail;}
        strcpy(rows[used].filename,cursor);
        if(!strcmp(kid,"-") && !strcmp(iv,"-")) rows[used].key=NULL;
        else {
            if(strlen(kid)!=32 || strlen(iv)!=32) {ret=AVERROR_INVALIDDATA;goto fail;}
            char normalized[33];
            for(int i=0;i<32;i++) {
                int x=hex(kid[i]);if(x<0) {ret=AVERROR_INVALIDDATA;goto fail;}
                normalized[i]="0123456789abcdef"[x];
            }
            normalized[32]=0;
            for(int i=0;i<s->count;i++) if(!strcmp(normalized,s->keys[i].id)) rows[used].key=s->keys[i].value;
            if(!rows[used].key) {ret=AVERROR_INVALIDDATA;goto fail;}
            for(int i=0;i<16;i++) {
                int a=hex(iv[i*2]),b=hex(iv[i*2+1]);if(a<0||b<0) {ret=AVERROR_INVALIDDATA;goto fail;}
                rows[used].iv[i]=(uint8_t)((a<<4)|b);
            }
        }
        used++;cursor=end+1;
    }
    if(!used) {ret=AVERROR_INVALIDDATA;goto fail;}
    av_free(data);*out=rows;*count=used;return 0;
fail:
    av_free(data);av_free(rows);return ret;
}
static int deny_open(AVFormatContext *ctx,AVIOContext **pb,const char *url,int flags,AVDictionary **options) {
    (void)ctx;(void)pb;(void)url;(void)flags;(void)options;
    return AVERROR(EACCES);
}

typedef struct PacketNode {
    AVPacket *packet;
    AVRational time_base;
    struct PacketNode *next;
} PacketNode;
typedef struct {
    AVFormatContext *out;
    AVIOContext *output;
    AVCodecContext *decoders[2];
    AVCodecParameters *parameters[2];
    AVFrame *frame;
    int streams;
    unsigned int ready;
    uint64_t frames[2];
    PacketNode *head,*tail;
    unsigned int queued;
    int64_t queue_bytes,total_bytes;
} Pipeline;

static int prepare_decoders(Pipeline *p,AVFormatContext *in) {
    if(!in->nb_streams || in->nb_streams>2) return AVERROR_INVALIDDATA;
    p->streams=(int)in->nb_streams;
    for(int i=0;i<p->streams;i++) {
        AVCodecParameters *par=in->streams[i]->codecpar;
        if(par->codec_type!=AVMEDIA_TYPE_VIDEO && par->codec_type!=AVMEDIA_TYPE_AUDIO) return AVERROR_INVALIDDATA;
        const AVCodec *codec=avcodec_find_decoder(par->codec_id);
        if(!codec) return AVERROR_DECODER_NOT_FOUND;
        p->decoders[i]=avcodec_alloc_context3(codec);
        p->parameters[i]=avcodec_parameters_alloc();
        if(!p->decoders[i] || !p->parameters[i]) return AVERROR(ENOMEM);
        int ret=avcodec_parameters_to_context(p->decoders[i],par);
        if(ret<0) return ret;
        p->decoders[i]->thread_count=1;
        p->decoders[i]->max_pixels=8192LL*4320;
        p->decoders[i]->max_samples=32LL*1024*1024;
        p->decoders[i]->err_recognition=AV_EF_CRCCHECK|AV_EF_BITSTREAM|AV_EF_BUFFER|AV_EF_EXPLODE|AV_EF_CAREFUL;
        ret=avcodec_open2(p->decoders[i],codec,NULL);
        if(ret<0) return ret;
    }
    return 0;
}
static int validate_frames(Pipeline *p,int index) {
    int ret;
    AVFrame *frame=p->frame;
    while((ret=avcodec_receive_frame(p->decoders[index],frame))>=0) {
        if((frame->flags & AV_FRAME_FLAG_CORRUPT) || frame->decode_error_flags) {av_frame_unref(frame);return AVERROR_INVALIDDATA;}
        AVCodecParameters *parameters=p->parameters[index];
        if(!(p->ready & (1u<<index))) {
            ret=avcodec_parameters_from_context(parameters,p->decoders[index]);
            if(ret<0) return ret;
            p->ready|=1u<<index;
        } else if((parameters->codec_type==AVMEDIA_TYPE_VIDEO && (frame->width!=parameters->width || frame->height!=parameters->height)) ||
                  (parameters->codec_type==AVMEDIA_TYPE_AUDIO && (frame->sample_rate!=parameters->sample_rate || frame->ch_layout.nb_channels!=parameters->ch_layout.nb_channels))) {
            av_frame_unref(frame);return AVERROR_INVALIDDATA;
        }
        p->frames[index]++;av_frame_unref(frame);
    }
    return ret==AVERROR(EAGAIN) || ret==AVERROR_EOF ? 0 : ret;
}
static int write_packet(Pipeline *p,AVPacket *packet,AVRational time_base) {
    av_packet_rescale_ts(packet,time_base,p->out->streams[packet->stream_index]->time_base);
    packet->pos=-1;
    return av_interleaved_write_frame(p->out,packet);
}
static int initialize_output(Pipeline *p,const wchar_t *destination,AVFormatContext *in) {
    int ret=avformat_alloc_output_context2(&p->out,NULL,"mp4",NULL);
    if(ret<0 || !p->out) return ret<0?ret:AVERROR(ENOMEM);
    p->out->io_open=deny_open;
    for(int i=0;i<p->streams;i++) {
        AVStream *stream=avformat_new_stream(p->out,NULL);
        if(!stream) return AVERROR(ENOMEM);
        ret=avcodec_parameters_copy(stream->codecpar,p->parameters[i]);if(ret<0) return ret;
        stream->codecpar->codec_tag=0;
        stream->time_base=in->streams[i]->time_base;
    }
    ret=open_file(&p->output,destination,1);if(ret<0) return ret;
    p->out->pb=p->output;p->out->flags|=AVFMT_FLAG_CUSTOM_IO;
    ret=avformat_write_header(p->out,NULL);if(ret<0) return ret;
    while(p->head) {
        PacketNode *node=p->head;
        p->head=node->next;
        ret=write_packet(p,node->packet,node->time_base);
        av_packet_free(&node->packet);av_free(node);
        if(ret<0) return ret;
    }
    p->tail=NULL;p->queued=0;p->queue_bytes=0;
    return 0;
}
static int process_packet(Pipeline *p,AVFormatContext *in,AVPacket *packet,const wchar_t *destination) {
    if(!p->streams) {
        int ret=prepare_decoders(p,in);if(ret<0) return ret;
    }
    if(in->nb_streams!=(unsigned)p->streams || packet->stream_index<0 || packet->stream_index>=p->streams ||
       packet->size<0 || packet->size>BYTE_LIMIT-p->total_bytes) return AVERROR_INVALIDDATA;
    for(int i=0;i<p->streams;i++) {
        if(in->streams[i]->codecpar->codec_id!=p->decoders[i]->codec_id || in->streams[i]->codecpar->codec_type!=p->decoders[i]->codec_type)
            return AVERROR_INVALIDDATA;
    }
    p->total_bytes+=packet->size;
    int ret=avcodec_send_packet(p->decoders[packet->stream_index],packet);if(ret<0) return ret;
    ret=validate_frames(p,packet->stream_index);if(ret<0) return ret;
    if(p->out) return write_packet(p,packet,in->streams[packet->stream_index]->time_base);
    if(p->queued>=4096 || packet->size>8*1024*1024-p->queue_bytes) return AVERROR(ENOBUFS);
    PacketNode *node=av_mallocz(sizeof(*node));if(!node) return AVERROR(ENOMEM);
    node->packet=av_packet_clone(packet);
    if(!node->packet) {av_free(node);return AVERROR(ENOMEM);}
    node->time_base=in->streams[packet->stream_index]->time_base;
    if(p->tail) p->tail->next=node;else p->head=node;
    p->tail=node;p->queued++;p->queue_bytes+=packet->size;
    if(p->ready==((1u<<p->streams)-1)) return initialize_output(p,destination,in);
    return 0;
}
static int open_input(State *s,const wchar_t *path,int sample,AVFormatContext **in,AVIOContext **input) {
    char *name=utf8(path);
    AVDictionary *options=NULL;
    AVDictionary *decoder_options[2]={NULL,NULL};
    if(!name) return AVERROR_INVALIDDATA;
    int ret=open_file(input,path,0);if(ret<0) goto done;
    *in=avformat_alloc_context();if(!*in) {ret=AVERROR(ENOMEM);goto done;}
    (*in)->pb=*input;(*in)->flags|=AVFMT_FLAG_CUSTOM_IO;
    (*in)->io_open=deny_open;(*in)->io_close2=close_io;
    (*in)->max_streams=2;(*in)->probesize=8*1024*1024;(*in)->max_analyze_duration=10*AV_TIME_BASE;
    av_dict_set(&options,"protocol_whitelist","file",0);
    av_dict_set(&options,"enable_drefs","0",0);
    if(!sample) {
        char dictionary[KEY_LIMIT*66];int used=0;
        for(int i=0;i<s->count;i++) {
            if(i) dictionary[used++]=':';
            memcpy(dictionary+used,s->keys[i].id,32);used+=32;dictionary[used++]='=';
            for(int j=0;j<16;j++) {dictionary[used++]="0123456789abcdef"[s->keys[i].value[j]>>4];dictionary[used++]="0123456789abcdef"[s->keys[i].value[j]&15];}
        }
        dictionary[used]=0;
        ret=av_dict_set(&options,"decryption_keys",dictionary,0);
        SecureZeroMemory(dictionary,sizeof(dictionary));if(ret<0) goto done;
    }
    ret=avformat_open_input(in,name,av_find_input_format(sample?"mpegts":"mov"),&options);if(ret<0) goto done;
    if(!sample) {
        for(int i=0;i<2;i++) {
            av_dict_set(&decoder_options[i],"threads","1",0);
            av_dict_set(&decoder_options[i],"max_pixels","35389440",0);
            av_dict_set(&decoder_options[i],"max_samples","33554432",0);
        }
        ret=avformat_find_stream_info(*in,decoder_options);
    }
done:
    av_dict_free(&options);for(int i=0;i<2;i++) av_dict_free(&decoder_options[i]);
    av_free(name);return ret;
}
static int remux(State *s,int sample,const wchar_t *source,const wchar_t *destination) {
    int ret=AVERROR(EINVAL),rows_count=1;
    SampleRow *rows=NULL;
    AVFormatContext *in=NULL;
    AVIOContext *input=NULL;
    AVPacket *packet=av_packet_alloc();
    Pipeline p={0};p.frame=av_frame_alloc();
    if(!packet || !p.frame) {ret=AVERROR(ENOMEM);goto done;}
    if(input_path(s,source)<0) goto done;
    if(sample) {ret=sample_rows(s,source,&rows,&rows_count);if(ret<0) goto done;}
    for(int row=0;row<rows_count;row++) {
        wchar_t segment[32768];const wchar_t *path=source;
        if(sample) {
            if(wcslen(s->root)+strlen(rows[row].filename)+2>=32768) {ret=AVERROR_INVALIDDATA;goto done;}
            wcscpy(segment,s->root);wcscat(segment,L"\\");
            size_t start=wcslen(segment);
            for(size_t i=0;i<=strlen(rows[row].filename);i++) segment[start+i]=(wchar_t)rows[row].filename[i];
            path=segment;
        }
        ret=open_input(s,path,sample,&in,&input);if(ret<0) goto done;
        unsigned int packets=0;
        while((ret=av_read_frame(in,packet))>=0) {
            if(packet->stream_index<0 || (unsigned)packet->stream_index>=in->nb_streams) {ret=AVERROR_INVALIDDATA;goto done;}
            if(sample && rows[row].key) {
                ret=od_sample_decrypt(in->streams[packet->stream_index]->codecpar->codec_id,packet,rows[row].key,rows[row].iv);
                if(ret<0) goto done;
            }
            ret=process_packet(&p,in,packet,destination);if(ret<0) goto done;
            av_packet_unref(packet);packets++;
        }
        if(ret!=AVERROR_EOF || !packets || (p.streams && in->nb_streams!=(unsigned)p.streams)) {ret=AVERROR_INVALIDDATA;goto done;}
        avformat_close_input(&in);close_io(NULL,input);input=NULL;
    }
    if(!p.out || p.head) {ret=AVERROR_INVALIDDATA;goto done;}
    for(int i=0;i<p.streams;i++) {
        ret=avcodec_send_packet(p.decoders[i],NULL);if(ret<0) goto done;
        ret=validate_frames(&p,i);if(ret<0 || !p.frames[i]) {ret=AVERROR_INVALIDDATA;goto done;}
    }
    ret=av_write_trailer(p.out);if(ret<0) goto done;
    avio_flush(p.output);
    if(p.output->error<0 || !FlushFileBuffers(((LocalIO*)p.output->opaque)->file)) {ret=AVERROR(EIO);goto done;}
    ret=0;
done:
    av_packet_free(&packet);av_frame_free(&p.frame);
    for(int i=0;i<2;i++) {avcodec_free_context(&p.decoders[i]);avcodec_parameters_free(&p.parameters[i]);}
    while(p.head) {PacketNode *node=p.head;p.head=node->next;av_packet_free(&node->packet);av_free(node);}
    if(in) avformat_close_input(&in);
    if(input) close_io(NULL,input);
    if(p.out) avformat_free_context(p.out);
    if(p.output) close_io(NULL,p.output);
    av_free(rows);return ret;
}
int wmain(int argc,wchar_t **argv) {
    /* No FFmpeg log callback may print option values or key material. */
    av_log_set_level(AV_LOG_QUIET);
    av_max_alloc(512*1024*1024);
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    State state={0};
    int ret=-1;
    if(argc!=4 || (wcscmp(argv[1],L"mov") && wcscmp(argv[1],L"sample"))) {fputs("crypto_arguments\n",stderr);return 2;}
    if(avformat_version()>>16 != LIBAVFORMAT_VERSION_MAJOR) {fputs("crypto_runtime\n",stderr);return 2;}
    if(load_keys(&state)<0) {SecureZeroMemory(&state,sizeof(state));fputs("crypto_keys\n",stderr);return 2;}
    ret=remux(&state,!wcscmp(argv[1],L"sample"),argv[2],argv[3]);
    SecureZeroMemory(&state,sizeof(state));
    if(ret<0) {fprintf(stderr,"crypto_media:%d\n",ret);return 1;}
    fputs("crypto_ready\n",stdout);return 0;
}
