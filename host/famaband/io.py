"""FAMA-Band session files (.fbr) and converters.

An .fbr file starts with a header (b"FAMABAND", u16 version, u16 json_len,
JSON metadata, zero padding up to a multiple of 512 bytes; the device always
writes exactly 512) followed by packets in the format of protocol.py. The device writes raw 2 kSPS EMG (T_EMG_RAW), IMU, markers,
FAMA-Net results and status. The host receiver writes the same format with
1 kHz stream packets.

    python -m famaband.io session.fbr --npz out.npz      # or --csv out.csv
"""
import argparse
import json
import struct
import time

import numpy as np

from . import protocol as P

HEADER_SIZE = 512
FILE_MAGIC = b"FAMABAND"
FILE_VERSION = 1


def make_header(**meta):
    meta.setdefault("created_unix", time.time())
    meta.setdefault("fs_raw_hz", 2000)
    meta.setdefault("fs_stream_hz", 1000)
    meta.setdefault("lsb_volts", P.LSB_V)
    meta.setdefault("gain", 6)
    meta.setdefault("vref", 2.4)
    meta.setdefault("channels", ["ch%d" % i for i in range(1, 9)])
    js = json.dumps(meta).encode()
    if len(js) > 0xFFFF:
        raise ValueError("header metadata too long")
    size = header_size(len(js))
    return FILE_MAGIC + struct.pack("<HH", FILE_VERSION, len(js)) + js + b"\0" * (size - 12 - len(js))


def header_size(json_len):
    return max(HEADER_SIZE, -(-(12 + json_len) // HEADER_SIZE) * HEADER_SIZE)


class FbrWriter:
    def __init__(self, path, **meta):
        self.f = open(path, "wb")
        self.f.write(make_header(**meta))

    def write(self, packet_bytes):
        self.f.write(packet_bytes)

    def close(self):
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def read_fbr(path):
    data = open(path, "rb").read()
    if data[:8] != FILE_MAGIC:
        raise ValueError("not an .fbr file")
    ver, jl = struct.unpack_from("<HH", data, 8)
    header = json.loads(data[12:12 + jl])
    pkts, used, bad = P.parse(data[header_size(jl):])
    raw, raw_frames, s1k, s1k_frames = [], [], [], []
    imu, imu_frames, markers, results, status, sessions = [], [], [], [], [], []
    for p in pkts:
        if p.type == P.T_EMG_RAW:
            raw.append(p.emg()); raw_frames.append((p.frame, p.n))
        elif p.type == P.T_EMG_1K:
            s1k.append(p.emg()); s1k_frames.append((p.frame, p.n))
        elif p.type == P.T_IMU:
            imu.append(p.imu()); imu_frames.append(p.frame)
        elif p.type == P.T_MARKER:
            code, label = p.marker()
            markers.append((p.frame, code, label))
        elif p.type == P.T_RESULT:
            r = p.result(); r["frame"] = p.frame; results.append(r)
        elif p.type == P.T_STATUS:
            status.append(p.status())
        elif p.type == P.T_SESSION:
            sessions.append((p.frame, p.payload[0], p.payload[1:].decode("utf-8", "replace")))

    def assemble(chunks, frames):
        if not chunks:
            return np.zeros((8, 0), np.int32), 0
        start = frames[0][0]
        end = frames[-1][0] + frames[-1][1]
        out = np.zeros((end - start, 8), np.int32)
        have = np.zeros(end - start, bool)
        for c, (f0, n) in zip(chunks, frames):
            out[f0 - start:f0 - start + n] = c
            have[f0 - start:f0 - start + n] = True
        return out.T, int((~have).sum())

    raw_codes, lost_raw = assemble(raw, raw_frames)
    s_codes, lost_s = assemble(s1k, s1k_frames)
    lsb = header.get("lsb_volts", P.LSB_V)
    return dict(header=header, fs_raw=header.get("fs_raw_hz", 2000), fs_stream=header.get("fs_stream_hz", 1000),
                raw_codes=raw_codes, raw_volts=raw_codes * lsb, raw_start_frame=raw_frames[0][0] if raw_frames else 0,
                stream_codes=s_codes, stream_volts=s_codes * lsb, lost_raw_frames=lost_raw, lost_stream_frames=lost_s,
                imu=np.concatenate(imu) if imu else np.zeros((0, 6), np.int16), markers=markers, results=results,
                status=status, sessions=sessions, crc_errors=bad)


def main():
    ap = argparse.ArgumentParser(description="Convert a FAMA-Band .fbr session file")
    ap.add_argument("fbr")
    ap.add_argument("--npz")
    ap.add_argument("--csv", help="1 row per sample: frame, ch1..ch8 [V] (raw 2 kSPS if present, else stream)")
    a = ap.parse_args()
    rec = read_fbr(a.fbr)
    print(json.dumps(rec["header"], indent=1))
    print(f"raw frames: {rec['raw_codes'].shape[1]} (lost {rec['lost_raw_frames']}), "
          f"stream frames: {rec['stream_codes'].shape[1]} (lost {rec['lost_stream_frames']}), "
          f"markers: {len(rec['markers'])}, results: {len(rec['results'])}, CRC errors: {rec['crc_errors']}")
    if a.npz:
        np.savez_compressed(a.npz, raw_volts=rec["raw_volts"], stream_volts=rec["stream_volts"], imu=rec["imu"],
                            markers=np.array(rec["markers"], dtype=object), header=json.dumps(rec["header"]))
    if a.csv:
        v = rec["raw_volts"] if rec["raw_volts"].shape[1] else rec["stream_volts"]
        start = rec["raw_start_frame"]
        np.savetxt(a.csv, np.column_stack([np.arange(v.shape[1]) + start, v.T]), delimiter=",",
                   header="frame," + ",".join(rec["header"]["channels"]), comments="", fmt=["%d"] + ["%.9e"] * 8)


if __name__ == "__main__":
    main()
