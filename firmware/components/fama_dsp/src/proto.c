/* FAMA-Band packet framing (see fama_proto.h). */
#include <string.h>

#include "fama_proto.h"

uint16_t fb_crc16(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)(data[i] << 8);
        for (int b = 0; b < 8; b++)
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
    return crc;
}

static void put16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void put32(uint8_t *p, uint32_t v) { put16(p, (uint16_t)v); put16(p + 2, (uint16_t)(v >> 16)); }

static size_t finish(uint8_t *out, uint8_t type, uint32_t seq, uint32_t frame, uint16_t n, uint16_t plen)
{
    put16(out, FB_MAGIC);
    out[2] = FB_VERSION;
    out[3] = type;
    put32(out + 4, seq);
    put32(out + 8, frame);
    put16(out + 12, n);
    put16(out + 14, plen);
    put16(out + FB_HDR + plen, fb_crc16(out, FB_HDR + plen));
    return FB_HDR + plen + FB_CRC;
}

size_t fb_pack(uint8_t *out, size_t cap, uint8_t type, uint32_t seq, uint32_t frame, uint16_t n,
               const void *payload, uint16_t payload_len)
{
    if ((size_t)FB_HDR + payload_len + FB_CRC > cap) return 0;
    if (payload_len) memcpy(out + FB_HDR, payload, payload_len);
    return finish(out, type, seq, frame, n, payload_len);
}

size_t fb_pack_emg(uint8_t *out, size_t cap, uint8_t type, uint32_t seq, uint32_t frame, const int32_t *frames8, uint16_t n)
{
    size_t plen = (size_t)n * 8u * 3u;
    if (FB_HDR + plen + FB_CRC > cap || plen > 0xFFFFu) return 0;
    uint8_t *p = out + FB_HDR;
    for (size_t i = 0; i < (size_t)n * 8u; i++) {
        uint32_t v = (uint32_t)frames8[i];
        p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16);
        p += 3;
    }
    return finish(out, type, seq, frame, n, (uint16_t)plen);
}

size_t fb_check(const uint8_t *buf, size_t len)
{
    if (len < FB_HDR + FB_CRC) return 0;
    if (buf[0] != (FB_MAGIC & 0xFF) || buf[1] != (FB_MAGIC >> 8) || buf[2] != FB_VERSION) return 0;
    uint16_t plen = (uint16_t)(buf[14] | buf[15] << 8);
    size_t total = (size_t)FB_HDR + plen + FB_CRC;
    if (len < total) return 0;
    uint16_t crc = (uint16_t)(buf[FB_HDR + plen] | buf[FB_HDR + plen + 1] << 8);
    return fb_crc16(buf, FB_HDR + plen) == crc ? total : 0;
}
