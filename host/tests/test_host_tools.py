"""Host tools end to end against the software device over real UDP sockets."""
import time

import numpy as np

from famaband import io, protocol as P
from famaband.link import DeviceLink
from famaband.protocol_runner import DEFAULT, Runner
from famaband.simulator_device import SimDevice


def test_record_stream_with_markers(tmp_path):
    dev = SimDevice(port=15005, duration_s=30)
    dev.start(seconds=6)
    link = DeviceLink("127.0.0.1", 15005)
    path = tmp_path / "rec.fbr"
    link.record(path, subject="T")
    link.session_start("T_D1")
    time.sleep(1.0)
    link.marker(0x0101, "G_fist")
    time.sleep(2.0)
    link.session_end()
    time.sleep(0.5)
    link.close()
    dev.stop()
    rec = io.read_fbr(path)
    n = rec["stream_codes"].shape[1]
    assert 2500 < n < 4500, n                       # ~3.5 s at 1 kHz
    assert rec["lost_stream_frames"] == 0 and rec["crc_errors"] == 0
    assert [m[1:] for m in rec["markers"]] == [(0x0101, "G_fist")]
    assert [s[1] for s in rec["sessions"]] == [1, 2]
    # stream content is the decimated synthetic EMG: tens to hundreds of microvolts around a DC offset
    v = rec["stream_volts"] - rec["stream_volts"].mean(axis=1, keepdims=True)
    assert 1e-6 < np.std(v) < 1e-3


def test_protocol_runner_dry_run(tmp_path):
    dev = SimDevice(port=15007, duration_s=30)
    dev.start(seconds=20)
    link = DeviceLink("127.0.0.1", 15007)
    cfg = dict(DEFAULT, cycles=2, gestures=["rest", "fist"],
               mvc=dict(trials=2, hold_s=5, rest_s=10),
               gesture_block=dict(reps=1, hold_s=5, rest_s=3),
               fatigue_block=dict(contractions=2, hold_s=20, rest_s=10, level_start=0.5, level_end=0.75),
               transition_rest_s=10)

    class Quiet:
        def write(self, *_): pass
        def flush(self): pass
        def isatty(self): return False

    path = Runner(link, cfg, str(tmp_path), "T01", 1, time_scale=0.01, out=Quiet()).run()
    link.close()
    dev.stop()
    rec = io.read_fbr(path)
    labels = [m[2] for m in rec["markers"]]
    assert labels[:2] == ["MVC_pre_1_START", "MVC_pre_1_END"]
    assert "CYCLE_2" in labels and "F_75_START" in labels and "F_50_START" in labels
    assert labels[-1] == "MVC_post_2_END"
    frames = [m[0] for m in rec["markers"]]
    assert frames == sorted(frames)                 # markers are in the device timebase, monotonic
    assert rec["header"]["subject"] == "T01"


def test_contact_check_flags_bad_channels():
    from famaband.contact import assess
    rng = np.random.default_rng(0)
    t = np.arange(2000) / 1000.0
    v = 20e-6 * rng.normal(size=(8, 2000))
    v[2] += 0.32                                   # 320 mV electrode offset -> outside window
    v[5] += 200e-6 * np.sin(2 * np.pi * 50 * t)    # lifted electrode -> strong mains pickup
    res = assess(v)
    assert [r["ok"] for r in res] == [True, True, False, True, True, False, True, True]
    assert abs(res[5]["mains_uv"] - 200) < 30
