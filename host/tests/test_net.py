"""FAMA-Net runtime: C graph engine vs NumPy reference (inference, Tier A AdaBN, reset)
and PyTorch training model -> device graph export."""
import numpy as np
import pytest

from famaband import cdsp
from famaband.net_ref import NetState, build_fama_net, forward, softmax


@pytest.fixture(scope="module")
def lib():
    return cdsp.load()


@pytest.fixture(scope="module")
def graph():
    return build_fama_net(np.random.default_rng(7))


def test_reference_topology(graph):
    # input [8 x 33 x 9] as in the thesis, 8 gestures, scalar fatigue output
    assert graph.bufs[0] == (8, 33, 9)
    assert graph.bufs[graph.out_gesture] == (8, 1, 1)
    assert graph.bufs[graph.out_fatigue] == (1, 1, 1)
    assert 40_000 < graph.n_params() < 120_000


def test_c_forward_matches_numpy(lib, graph):
    net = cdsp.CNet(lib, graph.to_blob())
    rng = np.random.default_rng(8)
    for _ in range(3):
        x = rng.normal(size=(8, 33, 9))
        lc, fc = net.forward(x, [graph.out_gesture, graph.out_fatigue])
        b = forward(NetState(graph), x)
        np.testing.assert_allclose(lc, b[graph.out_gesture], rtol=1e-4, atol=1e-4)
        np.testing.assert_allclose(fc, b[graph.out_fatigue], rtol=1e-4, atol=1e-4)


def test_tier_a_adabn_matches_numpy_and_resets(lib, graph):
    net = cdsp.CNet(lib, graph.to_blob())
    st = NetState(graph)
    rng = np.random.default_rng(9)
    xs = [rng.normal(0.5, 1.5, size=(8, 33, 9)) for _ in range(12)]   # shifted input distribution
    net.set_alpha(0.1)
    for x in xs:
        lc, = net.forward(x, [graph.out_gesture])
        lp = forward(st, x, adapt_alpha=0.1)[graph.out_gesture]
    np.testing.assert_allclose(lc, lp, rtol=2e-4, atol=2e-4)
    # adaptation changed the output relative to the frozen network
    frozen = forward(NetState(graph), xs[-1])[graph.out_gesture]
    assert np.max(np.abs(lp - frozen)) > 1e-3
    # session reset restores the pre-trained statistics exactly
    net.reset(); net.set_alpha(0.0)
    lr, = net.forward(xs[-1], [graph.out_gesture])
    np.testing.assert_allclose(lr, frozen, rtol=1e-4, atol=1e-4)


def test_blob_rejects_garbage(lib, graph):
    blob = bytearray(graph.to_blob())
    blob[0] ^= 0xFF
    with pytest.raises(ValueError):
        cdsp.CNet(lib, bytes(blob))
    with pytest.raises(ValueError):
        cdsp.CNet(lib, graph.to_blob()[:100])


def test_torch_model_exports_to_identical_graph():
    torch = pytest.importorskip("torch")
    from famaband.model import FamaNet, to_graph
    torch.manual_seed(0)
    m = FamaNet()
    # give BN layers non-trivial statistics, then switch to inference mode
    m.train()
    with torch.no_grad():
        for _ in range(5):
            m(torch.randn(16, 8, 33, 9) * 1.3 + 0.2)
    m.eval()
    x = torch.randn(4, 8, 33, 9)
    with torch.no_grad():
        logits, shat = m(x)
    g = to_graph(m)
    for i in range(4):
        b = forward(NetState(g), x[i].numpy())
        np.testing.assert_allclose(b[g.out_gesture], logits[i].numpy(), rtol=1e-4, atol=1e-4)
        np.testing.assert_allclose(b[g.out_fatigue], shat[i].numpy(), rtol=1e-4, atol=1e-4)


def test_softmax_sums_to_one():
    p = softmax(np.array([1.0, 2.0, -3.0, 50.0]))
    assert abs(p.sum() - 1) < 1e-12 and p.argmax() == 3
