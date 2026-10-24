"""Packet framing: firmware C packer <-> Python parser, CRC, resync, .fbr files."""
import struct

import numpy as np
import pytest

from famaband import cdsp, protocol as P


@pytest.fixture(scope="module")
def lib():
    return cdsp.load()


def test_crc_matches_c_and_known_vector(lib):
    assert P.crc16(b"123456789") == 0x29B1           # CRC-16/CCITT-FALSE check value
    data = bytes(range(200))
    assert lib.fb_crc16(data, len(data)) == P.crc16(data)


def test_c_packed_emg_parses_in_python(lib):
    rng = np.random.default_rng(0)
    codes = rng.integers(-2 ** 23, 2 ** 23, size=(25, 8), dtype=np.int32)
    raw = cdsp.pack_emg(lib, P.T_EMG_1K, 7, 12345, codes)
    assert raw == P.pack_emg(P.T_EMG_1K, 7, 12345, codes)
    assert lib.fb_check(raw, len(raw)) == len(raw)
    pkts, used, bad = P.parse(raw)
    assert used == len(raw) and bad == 0 and len(pkts) == 1
    p = pkts[0]
    assert (p.type, p.seq, p.frame, p.n) == (P.T_EMG_1K, 7, 12345, 25)
    np.testing.assert_array_equal(p.emg(), codes)


def test_parser_resyncs_after_corruption():
    a = P.pack(P.T_MARKER, 1, 100, 3, struct.pack("<H", 9) + b"MVC")
    b = P.pack(P.T_MARKER, 2, 200, 4, struct.pack("<H", 10) + b"FIST")
    broken = bytearray(a)
    broken[20] ^= 0x55
    pkts, used, bad = P.parse(b"junk" + bytes(broken) + b + a[:5])
    assert bad == 1
    assert [p.seq for p in pkts] == [2]
    assert pkts[0].marker() == (10, "FIST")


def test_result_and_status_roundtrip():
    probs = (np.linspace(0, 1, 16) * 255).astype(np.uint8).tobytes()
    r = P.Packet(P.T_RESULT, 0, 0, 1, P.RESULT.pack(42, 3, 8, 0b101, probs, 0.31, 1.2, 1.6)).result()
    assert r["window"] == 42 and r["gesture"] == 3 and r["tierb_updated"] and r["sampen_valid"] and not r["warmup"]
    assert len(r["probs"]) == 8
    s = P.Packet(P.T_STATUS, 0, 0, 1, P.STATUS.pack(87, 3950, 0b10110, 3, 600, b"S07_D1")).status()
    assert s["battery_pct"] == 87 and s["recording"] and s["sd_ok"] and s["afe_on"] and not s["usb"]
    assert s["session"] == "S07_D1"


def test_fbr_file_roundtrip(tmp_path):
    from famaband import io
    rng = np.random.default_rng(1)
    codes = rng.integers(-2 ** 20, 2 ** 20, size=(4000, 8), dtype=np.int32)
    path = tmp_path / "s.fbr"
    with io.FbrWriter(path, subject="S01", session="D1") as w:
        for i in range(0, 4000, 50):
            w.write(P.pack_emg(P.T_EMG_RAW, i // 50, i, codes[i:i + 50]))
        w.write(P.pack(P.T_MARKER, 0, 1000, 4, struct.pack("<H", 1) + b"FIST"))
    rec = io.read_fbr(path)
    assert rec["header"]["subject"] == "S01" and rec["fs_raw"] == 2000
    np.testing.assert_array_equal(rec["raw_codes"], codes.T)
    np.testing.assert_allclose(rec["raw_volts"], codes.T * P.LSB_V)
    assert rec["markers"] == [(1000, 1, "FIST")]
    assert rec["lost_raw_frames"] == 0
