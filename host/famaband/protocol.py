"""FAMA-Band packet format (mirror of firmware/components/fama_dsp/include/fama_proto.h)."""
import struct

import numpy as np

MAGIC = 0xFA4E
VERSION = 1
HDR = struct.Struct("<HBBIIHH")  # magic, version, type, seq, frame, n, payload_len
CRC_LEN = 2

T_EMG_1K, T_IMU, T_RESULT, T_STATUS, T_MARKER, T_SESSION, T_EMG_RAW, T_CMD = 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x11, 0x80
CMD_SESSION_START, CMD_SESSION_END, CMD_MARKER, CMD_SET_MODE, CMD_PING, CMD_TD_ENABLE = 1, 2, 3, 4, 5, 6
MODE_STREAM, MODE_INFER, MODE_BOTH = 1, 2, 3
LSB_V = 2 * 2.4 / 6 / 2 ** 24

RESULT = struct.Struct("<IBBB16sfff")
STATUS = struct.Struct("<BHBII24s")
GESTURES = ["rest", "fist", "open palm", "pinch", "wrist flexion", "wrist extension", "pronation", "supination"]


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def pack(ptype, seq, frame, n, payload=b""):
    head = HDR.pack(MAGIC, VERSION, ptype, seq & 0xFFFFFFFF, frame & 0xFFFFFFFF, n, len(payload))
    body = head + payload
    return body + struct.pack("<H", crc16(body))


def pack_emg(ptype, seq, frame, codes):
    """codes: int array [n, 8] (24-bit values)."""
    c = np.ascontiguousarray(codes, np.int64).reshape(-1, 8)
    u = (c & 0xFFFFFF).astype("<u4").view(np.uint8).reshape(-1, 4)[:, :3]
    return pack(ptype, seq, frame, c.shape[0], u.tobytes())


def pack_cmd(cmd, args=b"", seq=0):
    return pack(T_CMD, seq, 0, len(args), bytes([cmd]) + args)


def decode_int24(payload, n):
    b = np.frombuffer(payload, np.uint8).reshape(n * 8, 3).astype(np.int32)
    v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
    v = np.where(v & 0x800000, v - 0x1000000, v)
    return v.reshape(n, 8)


class Packet:
    __slots__ = ("type", "seq", "frame", "n", "payload")

    def __init__(self, ptype, seq, frame, n, payload):
        self.type, self.seq, self.frame, self.n, self.payload = ptype, seq, frame, n, payload

    def emg(self):
        return decode_int24(self.payload, self.n)

    def imu(self):
        return np.frombuffer(self.payload, "<i2").reshape(self.n, 6)

    def result(self):
        w, g, nc, fl, probs, shat, se, base = RESULT.unpack(self.payload)
        return dict(window=w, gesture=g, n_classes=nc, tierb_updated=bool(fl & 1), warmup=bool(fl & 2),
                    sampen_valid=bool(fl & 4), probs=np.frombuffer(probs, np.uint8)[:nc] / 255.0,
                    shat=shat, sampen=se, sampen_baseline=base)

    def status(self):
        pct, mv, fl, dropped, up, sess = STATUS.unpack(self.payload)
        return dict(battery_pct=pct, battery_mv=mv, usb=bool(fl & 1), recording=bool(fl & 2), sd_ok=bool(fl & 4),
                    link=bool(fl & 8), afe_on=bool(fl & 16), dropped_frames=dropped, uptime_s=up,
                    session=sess.split(b"\0")[0].decode())

    def marker(self):
        code, = struct.unpack_from("<H", self.payload)
        return code, self.payload[2:].decode("utf-8", "replace")


def parse(buf: bytes):
    """Parse every valid packet in buf; resynchronises on the magic after corruption.
    Returns (packets, bytes_consumed, n_crc_errors)."""
    out, i, bad = [], 0, 0
    n = len(buf)
    while i + HDR.size + CRC_LEN <= n:
        if buf[i] != 0x4E or buf[i + 1] != 0xFA:
            i += 1
            continue
        magic, ver, ptype, seq, frame, cnt, plen = HDR.unpack_from(buf, i)
        total = HDR.size + plen + CRC_LEN
        if ver != VERSION:
            i += 1
            continue
        if i + total > n:
            break
        body = buf[i:i + HDR.size + plen]
        crc, = struct.unpack_from("<H", buf, i + HDR.size + plen)
        if crc16(body) != crc:
            bad += 1
            i += 1
            continue
        out.append(Packet(ptype, seq, frame, cnt, bytes(buf[i + HDR.size:i + HDR.size + plen])))
        i += total
    return out, i, bad
