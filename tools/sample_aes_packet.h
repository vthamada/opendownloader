/* OpenDownloader authorized SAMPLE-AES for H.264/AAC MPEG-TS packets.
 *
 * Independent implementation of Apple's "MPEG-2 Stream Encryption Format
 * for HTTP Live Streaming", 2015-10-29, sections 2.1, 2.2 and 2.3.1.1:
 * https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/HLS_Sample_Encryption/Encryption/Encryption.html
 *
 * The caller owns segment/key/IV identity. This routine sees one compressed
 * packet from that segment, before decoding. It deliberately does not consult
 * the HLS demuxer's advancing/read-ahead segment cursor. No FFmpeg private ABI
 * is used; AVPacket and libavutil AES are public interfaces.
 */
#ifndef OPENDOWNLOADER_SAMPLE_AES_PACKET_H
#define OPENDOWNLOADER_SAMPLE_AES_PACKET_H

#include <errno.h>
#include <stdint.h>
#include <string.h>
#include <libavcodec/packet.h>
#include <libavcodec/codec_id.h>
#include <libavutil/aes.h>
#include <libavutil/error.h>
#include <libavutil/mem.h>

static void od_sample_wipe(void *value, size_t size) {
    volatile uint8_t *bytes = value;
    while(size--) *bytes++ = 0;
}

static int od_sample_start(const uint8_t *data, int size, int at, int *length) {
    for(int i=at;i+2<size;i++) {
        if(data[i] || data[i+1]) continue;
        if(i+3<size && !data[i+2] && data[i+3]==1) { *length=4;return i; }
        if(data[i+2]==1) { *length=3;return i; }
    }
    return -1;
}

static int od_sample_h264(struct AVAES *aes, AVPacket *packet, const uint8_t iv[16]) {
    int cursor=0, written=0;
    while(cursor<packet->size) {
        int prefix=0;
        int start=od_sample_start(packet->data,packet->size,cursor,&prefix);
        if(start!=cursor) return AVERROR_INVALIDDATA;
        int next_prefix=0;
        int next=od_sample_start(packet->data,packet->size,start+prefix,&next_prefix);
        if(next<0) next=packet->size;
        int length=next-start-prefix;
        if(length<=0) return AVERROR_INVALIDDATA;
        uint8_t *unit=packet->data+start+prefix;
        int kind=unit[0]&31;
        if((kind==1 || kind==5) && length>48) {
            /* Remove the extra emulation-prevention layer applied after
             * encryption, including clear leading/skipped/trailing bytes.
             * The original pre-encryption prevention layer remains intact.
             */
            int out=0;
            for(int i=0;i<length;i++) {
                if(i+3<length && !unit[i] && !unit[i+1] && unit[i+2]==3) {
                    unit[out++]=unit[i++];unit[out++]=unit[i++];
                } else unit[out++]=unit[i];
            }
            length=out;
            if(length<=48) return AVERROR_INVALIDDATA;
            uint8_t chain[16];memcpy(chain,iv,16);
            /* A protected block of exactly sixteen remaining bytes is clear.
             * CBC chaining spans encrypted blocks, excluding nine skipped
             * blocks, and resets at each protected NAL unit.
             */
            for(int position=32;length-position>16;position+=160)
                av_aes_crypt(aes,unit+position,unit+position,1,chain,1);
            od_sample_wipe(chain,sizeof(chain));
        }
        memmove(packet->data+written,packet->data+start,(size_t)prefix);
        written+=prefix;
        memmove(packet->data+written,unit,(size_t)length);
        written+=length;
        cursor=next;
    }
    av_shrink_packet(packet,written);
    return 0;
}

static int od_sample_aac(struct AVAES *aes, AVPacket *packet, const uint8_t iv[16]) {
    int cursor=0;
    while(cursor<packet->size) {
        uint8_t *frame=packet->data+cursor;
        int available=packet->size-cursor;
        if(available<7 || frame[0]!=0xff || (frame[1]&0xf6)!=0xf0)
            return AVERROR_INVALIDDATA;
        int header=(frame[1]&1) ? 7 : 9;
        int length=((frame[3]&3)<<11) | (frame[4]<<3) | (frame[5]>>5);
        if(length<header || length>available) return AVERROR_INVALIDDATA;
        int count=(length-header-16)/16;
        if(count>0) {
            uint8_t chain[16];memcpy(chain,iv,16);
            av_aes_crypt(aes,frame+header+16,frame+header+16,count,chain,1);
            od_sample_wipe(chain,sizeof(chain));
        }
        cursor+=length;
    }
    return 0;
}

static int od_sample_decrypt(enum AVCodecID codec, AVPacket *packet,
                             const uint8_t key[16], const uint8_t iv[16]) {
    if(!packet || !packet->data || packet->size<=0 || !key || !iv)
        return AVERROR(EINVAL);
    if(codec!=AV_CODEC_ID_H264 && codec!=AV_CODEC_ID_AAC)
        return AVERROR(ENOSYS);
    int ret=av_packet_make_writable(packet);
    if(ret<0) return ret;
    struct AVAES *aes=av_aes_alloc();
    if(!aes) return AVERROR(ENOMEM);
    ret=av_aes_init(aes,key,128,1);
    if(ret>=0) ret=codec==AV_CODEC_ID_H264 ? od_sample_h264(aes,packet,iv) : od_sample_aac(aes,packet,iv);
    /* Reinitialize the active AES-128 schedule before freeing. Do not read
     * av_aes_size: this helper links DLL functions through import libraries,
     * and importing that exported data symbol requires a separate ABI rule.
     * This is best effort replacement, not a general secure-erasure claim.
     */
    const uint8_t empty_key[16]={0};
    (void)av_aes_init(aes,empty_key,128,1);
    av_free(aes);
    return ret;
}

#endif
