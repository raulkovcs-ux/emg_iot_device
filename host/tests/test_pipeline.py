"""End-to-end: firmware pipeline (C) vs reference pipeline (Python) on synthetic
fatiguing sEMG; Tier B gating, causality (leakage checklist) and session reset."""
import numpy as np
import pytest

from famaband import cdsp, pipeline_ref
from famaband.net_ref import NetState, build_fama_net, forward
from famaband.simulator import synthesize

DUR = 100.0


def fatigue_curve(t):
    return 0.0 if t < 64 else min(1.0, (t - 64) / 12.0)


@pytest.fixture(scope="module")
def lib():
    return cdsp.load()


@pytest.fixture(scope="module")
def graph():
    g = build_fama_net(np.random.default_rng(11))
    # make the random head confident and the fatigue output positive, so Tier B's
    # pseudo-label and s_hat checks can pass; the SampEn check is then what gates it
    d2 = g.nodes[g.tb_dense2].p
    d2["w"] = (d2["w"] * 6).astype(np.float32)
    fat = g.nodes[[i for i, n in enumerate(g.nodes) if n.out == g.out_fatigue][0]].p
    fat["b"] = (fat["b"] + 0.6).astype(np.float32)
    return g


@pytest.fixture(scope="module")
def recording():
    x, labels, fat = synthesize(DUR, gesture_seq=[1, 2, 3, 4, 5, 6, 7, 0], fatigue_curve=fatigue_curve, seed=3)
    return x


@pytest.fixture(scope="module")
def runs(lib, graph, recording):
    net = cdsp.CNet(lib, graph.to_blob())
    c = cdsp.run_pipeline(lib, recording, net, refractory=150)
    py = pipeline_ref.run(recording, graph, refractory_windows=150)
    return c, py, net


def test_window_count_and_rate(runs):
    c, py, _ = runs
    # 1000 Hz output, first window at 200 ms then every 100 ms
    assert len(c["shat"]) == len(py) == int((DUR * 1000 - 200) // 100) + 1


def test_sampen_and_baseline_match(runs):
    c, py, _ = runs
    np.testing.assert_allclose(c["sampen"], [r["sampen"] for r in py], rtol=2e-3, atol=2e-3)
    np.testing.assert_allclose(c["baseline"], [r["baseline"] for r in py], rtol=2e-3, atol=2e-3)


def test_fatigue_marker_drops(runs):
    _, py, _ = runs
    base = py[-1]["baseline"]
    late = np.mean([r["sampen"] for r in py[-100:]])
    assert late < 0.8 * base, (late, base)


def test_gesture_outputs_match(runs):
    c, py, _ = runs
    pp = np.array([r["probs"] for r in py])
    np.testing.assert_allclose(c["probs"][:, :8], pp, atol=5e-3)
    assert np.mean(c["gesture"] == np.array([r["gesture"] for r in py])) > 0.99


def test_tier_b_fires_only_when_fatigued(runs):
    c, py, _ = runs
    upd_c = np.flatnonzero(c["tb_updated"])
    upd_p = np.flatnonzero([r["tb_updated"] for r in py])
    assert len(upd_c) >= 1
    np.testing.assert_array_equal(upd_c, upd_p)
    first_fatigue_window = int((64 * 1000 - 200) // 100)
    assert upd_c.min() > first_fatigue_window          # never during the fresh part
    assert np.all(np.diff(upd_c) >= 150)                # refractory period respected


def test_session_end_restores_pretrained_weights(lib, graph, runs):
    c, _, net = runs
    assert c["n_updates"] >= 1
    lib.fh_pipeline_session_end(cdsp.C.cast(c["_mem"], cdsp.C.c_void_p))
    net.set_alpha(0.0)
    x = np.random.default_rng(12).normal(size=(8, 33, 9))
    got, = net.forward(x, [graph.out_gesture])
    want = forward(NetState(graph), x)[graph.out_gesture]
    np.testing.assert_allclose(got, want, rtol=1e-4, atol=1e-4)


def test_pipeline_is_causal(lib, graph, recording):
    """Leakage checklist: outputs up to time t never depend on samples after t."""
    cut = 30 * 2000
    x2 = recording.copy()
    x2[:, cut:] = np.random.default_rng(13).normal(0, 1e-3, x2[:, cut:].shape)
    a = cdsp.run_pipeline(lib, recording[:, :cut + 20000], cdsp.CNet(lib, graph.to_blob()))
    b = cdsp.run_pipeline(lib, x2[:, :cut + 20000], cdsp.CNet(lib, graph.to_blob()))
    n_safe = int((cut // 2 - 200) // 100) + 1     # windows that end before the cut (1 kHz index)
    np.testing.assert_array_equal(a["probs"][:n_safe], b["probs"][:n_safe])
    assert not np.array_equal(a["probs"][n_safe + 2:], b["probs"][n_safe + 2:])
