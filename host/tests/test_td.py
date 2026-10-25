"""Terminal-device (prosthetic hand) output mapping in the firmware core."""
import ctypes as C

import numpy as np
import pytest

from famaband import cdsp

OPEN, CLOSE, NC = 2, 1, 8


@pytest.fixture(scope="module")
def run():
    lib = cdsp.load()
    f = np.ctypeslib.ndpointer(np.float32, flags="C_CONTIGUOUS")
    lib.fh_td_run.argtypes = [C.c_int, C.c_int, f, C.c_int, C.c_int, f]

    def _run(probs):
        p = np.ascontiguousarray(probs, np.float32)
        out = np.zeros((len(p), 2), np.float32)
        lib.fh_td_run(OPEN, CLOSE, p, len(p), NC, out)
        return out
    return _run


def probs_for(cls, p, n):
    a = np.full((n, NC), (1 - p) / (NC - 1), np.float32)
    a[:, cls] = p
    return a


def test_low_confidence_never_drives(run):
    out = run(probs_for(OPEN, 0.65, 50))       # below the 0.7 on-threshold
    assert np.all(out == 0)


def test_confident_open_drives_output0_only_and_is_bounded(run):
    out = run(probs_for(OPEN, 0.95, 50))
    assert np.all(out[:, 1] == 0)
    assert 0.85 < out[-1, 0] <= 1.0
    assert np.all(np.diff(out[:, 0]) >= -1e-7)  # monotone rise, no overshoot
    assert np.all((out >= 0) & (out <= 1))


def test_hysteresis(run):
    seq = np.concatenate([probs_for(OPEN, 0.9, 20), probs_for(OPEN, 0.6, 20), probs_for(OPEN, 0.45, 30)])
    out = run(seq)
    assert out[39, 0] > 0.1                      # 0.6 is between thresholds: stays active
    assert out[-1, 0] == 0                       # below 0.5: released and decays to exactly 0


def test_never_both_outputs(run):
    rng = np.random.default_rng(0)
    p = rng.dirichlet(np.full(NC, 0.3), size=2000).astype(np.float32)
    out = run(p)
    # only one output can be active; the other may still be decaying, but never rising
    rising = np.diff(out, axis=0) > 1e-6
    assert not np.any(rising[:, 0] & rising[:, 1])


def test_switch_open_to_close(run):
    out = run(np.concatenate([probs_for(OPEN, 0.95, 30), probs_for(CLOSE, 0.95, 30)]))
    assert out[29, 0] > 0.8 and out[29, 1] == 0
    assert out[-1, 1] > 0.8 and out[-1, 0] < 0.01
