"""Independent synthetic HLS SAMPLE-AES producer; never uses production crypto.

Implements Apple's MPEG-2 Stream Encryption Format for HLS (2015-10-29),
sections 2.2 and 2.3.1.1, for the small H.264/AAC MPEG-TS fixture from
``tests.test_streaming.generate_media``. This is intentionally a bounded test
producer, not an MPEG-TS reader for arbitrary user files.
"""
import re

from Cryptodome.Cipher import AES


def _crc(data):
    value = 0xffffffff
    for byte in data:
        value ^= byte << 24
        for _ in range(8):
            value = ((value << 1) ^ (0x04c11db7 if value & 0x80000000 else 0)) & 0xffffffff
    return value.to_bytes(4, 'big')


def _payload(packet):
    assert len(packet) == 188 and packet[0] == 0x47
    control = (packet[3] >> 4) & 3
    if not control & 1:
        return len(packet)
    return 4 + (1+packet[4] if control & 2 else 0)


def _prevent_emulation(data):
    out = bytearray()
    zeros = 0
    for byte in data:
        if zeros >= 2 and byte <= 3:
            out.append(3)
            zeros = 0
        out.append(byte)
        zeros = zeros+1 if byte == 0 else 0
    return bytes(out)


def encrypt_h264(data, key, iv):
    """Encrypt 1:9 CBC pattern after byte 32; preserve Annex-B start codes."""
    starts = list(re.finditer(b'\x00\x00\x00\x01|\x00\x00\x01', data))
    assert starts and starts[0].start() == 0
    output = bytearray()
    for index, start in enumerate(starts):
        end = starts[index+1].start() if index+1 < len(starts) else len(data)
        unit = bytearray(data[start.end():end])
        if unit[0] & 31 in (1, 5) and len(unit) > 48:
            cipher = AES.new(key, AES.MODE_CBC, iv)
            cursor = 32
            while len(unit)-cursor > 16:
                unit[cursor:cursor+16] = cipher.encrypt(bytes(unit[cursor:cursor+16]))
                cursor += 160
            unit = _prevent_emulation(unit)
        output += start.group()+unit
    return bytes(output)


def encrypt_aac(data, key, iv):
    """ADTS plus 16 bytes clear; full remaining blocks CBC, IV per frame."""
    output = bytearray()
    cursor = 0
    while cursor < len(data):
        assert data[cursor] == 0xff and data[cursor+1] & 0xf6 == 0xf0
        length = ((data[cursor+3] & 3) << 11) | (data[cursor+4] << 3) | (data[cursor+5] >> 5)
        assert 7 <= length <= len(data)-cursor
        frame = bytearray(data[cursor:cursor+length])
        header = 7 if frame[1] & 1 else 9
        begin = header+16
        end = begin+max(0, (length-begin)//16)*16
        if end > begin:
            frame[begin:end] = AES.new(key, AES.MODE_CBC, iv).encrypt(bytes(frame[begin:end]))
        output += frame
        cursor += length
    return bytes(output)


def _packetize(pid, pes, first, counters):
    output = bytearray()
    first_adaptation = first[4:5+first[4]] if first[3] & 0x20 else b''
    first_packet = True
    while pes:
        adaptation = first_adaptation if first_packet else b''
        capacity = 184-len(adaptation)
        count = min(len(pes), capacity)
        if count < capacity:
            target = 184-count
            if adaptation:
                adaptation = bytes([target-1])+adaptation[1:]+b'\xff'*(target-len(adaptation))
            elif target == 1:
                adaptation = b'\x00'
            else:
                adaptation = bytes([target-1, 0])+b'\xff'*(target-2)
        cc = counters.get(pid, first[3] & 15)
        counters[pid] = (cc+1) & 15
        output += bytes([0x47, (pid >> 8) | (0x40 if first_packet else 0), pid & 255,
                         (0x30 if adaptation else 0x10) | cc])+adaptation+pes[:count]
        pes = pes[count:]
        first_packet = False
    return bytes(output)


def _aac_setup(packets, pid):
    for packet in packets:
        if (((packet[1] & 31) << 8) | packet[2]) != pid or not packet[1] & 0x40:
            continue
        pes = packet[_payload(packet):]
        frame = pes[9+pes[8]:]
        assert len(frame) >= 7 and frame[:2] == b'\xff\xf1'
        object_type = (frame[2] >> 6)+1
        frequency = (frame[2] >> 2) & 15
        channels = ((frame[2] & 1) << 2) | (frame[3] >> 6)
        config = ((object_type << 11) | (frequency << 7) | (channels << 3)).to_bytes(2, 'big')
        # Apple transport audio setup registration descriptor, section2.3.3.1.
        setup = b'apadzaac\x00\x00\x01\x02'+config
        return bytes([5, len(setup)])+setup
    raise AssertionError('AAC PES absent from generated fixture')


def sample_aes_ts(data, key, iv):
    """Protect generated H.264/AAC TS with encrypted PMT stream types and CRC.

    Original timestamps, PCR and elementary compressed bytes are preserved.
    PES packetization may grow when H.264 emulation prevention inserts bytes.
    """
    assert len(key) == len(iv) == 16 and len(data) % 188 == 0
    packets = [data[start:start+188] for start in range(0, len(data), 188)]
    stream_types = {}
    rewritten = {}
    pmt_pid = None
    for index, original in enumerate(packets):
        offset = _payload(original)
        pid = ((original[1] & 31) << 8) | original[2]
        if offset == 188 or not original[1] & 0x40:
            continue
        start = offset+1+original[offset]
        if pid == 0:
            section = original[start:]
            assert section[0] == 0
            pmt_pid = ((section[10] & 31) << 8) | section[11]
        elif pid == pmt_pid:
            size = 3+(((original[start+1] & 15) << 8) | original[start+2])
            section = bytearray(original[start:start+size])
            assert section[0] == 2 and _crc(section) == b'\x00'*4
            position = 12+(((section[10] & 15) << 8) | section[11])
            replacement = section[:position]
            while position < len(section)-4:
                kind = section[position]
                elementary_pid = ((section[position+1] & 31) << 8) | section[position+2]
                stream_types[elementary_pid] = kind
                entry_size = 5+(((section[position+3] & 15) << 8) | section[position+4])
                entry = bytearray(section[position:position+entry_size])
                if kind in (0x1b, 0x0f):
                    entry[0] = {0x1b: 0xdb, 0x0f: 0xcf}[kind]
                if kind == 0x0f:
                    entry += _aac_setup(packets, elementary_pid)
                    entry[3:5] = (0xf000 | (len(entry)-5)).to_bytes(2, 'big')
                replacement += entry
                position += entry_size
            replacement[1:3] = (0xb000 | (len(replacement)+4-3)).to_bytes(2, 'big')
            replacement += _crc(replacement)
            assert start+len(replacement) <= 188
            rewritten[index] = original[:start]+replacement+b'\xff'*(188-start-len(replacement))
    assert set(stream_types.values()) == {0x1b, 0x0f}
    groups = []
    current = {}
    for index, packet in enumerate(packets):
        pid = ((packet[1] & 31) << 8) | packet[2]
        if pid not in stream_types:
            continue
        offset = _payload(packet)
        if offset == 188:
            continue
        if packet[1] & 0x40:
            group = [pid, [], bytearray()]
            groups.append(group)
            current[pid] = group
        assert pid in current
        current[pid][1].append(index)
        current[pid][2] += packet[offset:]
    skipped = set()
    counters = {}
    for pid, indices, pes in groups:
        assert pes[:3] == b'\x00\x00\x01'
        header = 9+pes[8]
        content = bytes(pes[header:])
        declared = int.from_bytes(pes[4:6], 'big')
        if declared:
            assert declared+6 == len(pes)
        protected = (encrypt_h264 if stream_types[pid] == 0x1b else encrypt_aac)(content, key, iv)
        pes = pes[:header]+protected
        if declared:
            pes[4:6] = (len(pes)-6).to_bytes(2, 'big')
        rewritten[indices[0]] = _packetize(pid, bytes(pes), packets[indices[0]], counters)
        skipped.update(indices[1:])
    return b''.join(rewritten.get(index, packet) for index, packet in enumerate(packets) if index not in skipped)


def sample_aes_resources(resources, *, key=b'authorized-key01', iv=bytes.fromhex('00'*15+'a5'), prefix='/sample'):
    """Return an independent HTTP resource set with explicit IV and 3 segments."""
    result = {prefix+'/key': key}
    lines = ['#EXTM3U', '#EXT-X-VERSION:5', '#EXT-X-TARGETDURATION:1',
             '#EXT-X-MEDIA-SEQUENCE:0', '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="key",IV=0x'+iv.hex()]
    for index in range(3):
        result[f'{prefix}/seg-{index:02d}.ts'] = sample_aes_ts(resources[f'/hls/seg-{index:02d}.ts'], key, iv)
        lines += ['#EXTINF:1,', f'seg-{index:02d}.ts']
    result[prefix+'/index.m3u8'] = '\n'.join(lines+['#EXT-X-ENDLIST', ''])
    return result
