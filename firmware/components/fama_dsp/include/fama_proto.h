/*
 * FAMA-Band packet format: one framing for the live link (BLE / UDP) and the
 * microSD log (.fbr). Parsed by host/famaband/protocol.py.
 *
 *   offset size field
 *   0      2    magic 0xFA4E (bytes 4E FA)
 *   2      1    version (1)
 *   3      1    type (FB_T_*)
 *   4      4    seq           packet counter per stream (gaps = lost packets)
 *   8      4    frame         index of the first sample in the ADC/1 kHz timebase
 *   12     2    n             items in the payload (frames, samples, bytes)
 *   14     2    payload_len   bytes
 *   16     ...  payload
 *   16+len 2    CRC-16/CCITT-FALSE over bytes [0, 16+len)
 *
 * All integers little endian. EMG samples are 24-bit two's complement,
 * 3 bytes each, channel-interleaved, LSB = 2*VREF/gain/2^24 = 47.68 nV.
 */
#pragma once
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FB_MAGIC 0xFA4Eu
#define FB_VERSION 1u
#define FB_HDR 16u
#define FB_CRC 2u

enum {
    FB_T_EMG_1K = 0x01,   /* stream: decimated 1000 Hz, n frames x 8 ch x int24 */
    FB_T_IMU = 0x02,      /* n samples x (ax ay az gx gy gz) int16, frame = 1 kHz index of first sample */
    FB_T_RESULT = 0x03,   /* FAMA-Net window result (fb_result_t) */
    FB_T_STATUS = 0x04,   /* fb_status_t */
    FB_T_MARKER = 0x05,   /* u16 code + UTF-8 label (n = label bytes); frame = 1 kHz index */
    FB_T_SESSION = 0x06,  /* u8 event (1 start, 2 end) + UTF-8 session id */
    FB_T_EMG_RAW = 0x11,  /* log: raw ADS1298 2000 SPS, n frames x 8 ch x int24, frame = 2 kHz index */
    FB_T_CMD = 0x80,      /* host -> device: u8 cmd + args */
};

enum { FB_CMD_SESSION_START = 1, FB_CMD_SESSION_END = 2, FB_CMD_MARKER = 3, FB_CMD_SET_MODE = 4, FB_CMD_PING = 5 };
enum { FB_MODE_STREAM = 1, FB_MODE_INFER = 2, FB_MODE_BOTH = 3 };

#pragma pack(push, 1)
typedef struct {
    uint32_t window;
    uint8_t gesture, n_classes, flags; /* flags: bit0 Tier B updated, bit1 Tier A warm-up, bit2 SampEn valid */
    uint8_t probs[16];                 /* softmax * 255 */
    float shat, sampen, sampen_baseline;
} fb_result_t;

typedef struct {
    uint8_t battery_pct;
    uint16_t battery_mv;
    uint8_t flags;        /* bit0 USB present (AFE locked out), bit1 recording, bit2 SD ok, bit3 link up, bit4 AFE on */
    uint32_t dropped_frames;
    uint32_t uptime_s;
    char session[24];
} fb_status_t;
#pragma pack(pop)

uint16_t fb_crc16(const uint8_t *data, size_t len);
/* Write a complete packet into out (capacity cap). Returns total length or 0 if it does not fit. */
size_t fb_pack(uint8_t *out, size_t cap, uint8_t type, uint32_t seq, uint32_t frame, uint16_t n,
               const void *payload, uint16_t payload_len);
/* Pack n frames of 8 int32 samples (low 24 bits used) as int24. */
size_t fb_pack_emg(uint8_t *out, size_t cap, uint8_t type, uint32_t seq, uint32_t frame, const int32_t *frames8, uint16_t n);
/* Validate a packet at buf; returns its total length, or 0 if incomplete/invalid. */
size_t fb_check(const uint8_t *buf, size_t len);

#ifdef __cplusplus
}
#endif
