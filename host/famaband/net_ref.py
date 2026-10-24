"""FAMA-Net graph format, NumPy reference forward pass (with Tier A AdaBN),
Tier B reference, and the weight-blob writer used by the firmware.

The graph is the device-side representation of the network: a list of
buffers (C, H, W) and a topologically ordered list of nodes. BatchNorm stays
a separate node (never folded into the convolutions) so Tier A can adapt
its running statistics at inference time.

Blob layout (little endian), version 1:
    u32 magic 'FAMA', u32 version, u32 n_bufs, u32 n_nodes,
    u32 out_gesture, u32 out_fatigue, i32 tb_dense1, i32 tb_dense2
    n_bufs  x (u32 c, u32 h, u32 w)
    n_nodes x (u32 type, u32 n_in, u32 in[3], u32 out, <params>)
      CONV2D: u32 out_ch,in_ch,kh,kw,sh,sw,ph,pw,groups; f32 w[out*(in/g)*kh*kw]; f32 b[out]
      BN:     u32 ch; f32 eps; f32 gamma[ch], beta[ch], mean[ch], var[ch]
      DENSE:  u32 n_in, n_out; f32 w[n_out*n_in] (row-major); f32 b[n_out]
      RELU, CONCAT, GAP: no parameters
"""
import struct
from dataclasses import dataclass, field

import numpy as np

CONV2D, BN, RELU, CONCAT, GAP, DENSE = 1, 2, 3, 4, 5, 6
MAGIC = 0x414D4146
VERSION = 1


@dataclass
class Node:
    type: int
    inputs: list
    out: int
    p: dict = field(default_factory=dict)


@dataclass
class Graph:
    bufs: list = field(default_factory=list)     # (c, h, w)
    nodes: list = field(default_factory=list)
    out_gesture: int = -1
    out_fatigue: int = -1
    tb_dense1: int = -1
    tb_dense2: int = -1

    # -------------------------------------------------- building helpers
    def buf(self, c, h=1, w=1):
        self.bufs.append((c, h, w))
        return len(self.bufs) - 1

    def conv(self, x, out_ch, k, stride=(1, 1), pad=None, groups=1, rng=None, w=None, b=None):
        c, h, wd = self.bufs[x]
        kh, kw = (k, k) if isinstance(k, int) else k
        ph, pw = (kh // 2, kw // 2) if pad is None else pad
        ho, wo = (h + 2 * ph - kh) // stride[0] + 1, (wd + 2 * pw - kw) // stride[1] + 1
        y = self.buf(out_ch, ho, wo)
        if w is None:
            fan_in = (c // groups) * kh * kw
            w = rng.normal(0, np.sqrt(2.0 / fan_in), (out_ch, c // groups, kh, kw))
            b = np.zeros(out_ch)
        self.nodes.append(Node(CONV2D, [x], y, dict(out_ch=out_ch, in_ch=c, kh=kh, kw=kw, sh=stride[0], sw=stride[1],
                                                    ph=ph, pw=pw, groups=groups, w=np.asarray(w, np.float32),
                                                    b=np.asarray(b, np.float32))))
        return y

    def bn(self, x, rng=None, gamma=None, beta=None, mean=None, var=None, eps=1e-5):
        c = self.bufs[x][0]
        y = self.buf(*self.bufs[x])
        if gamma is None:
            gamma, beta = 1 + 0.1 * rng.normal(size=c), 0.1 * rng.normal(size=c)
            mean, var = 0.1 * rng.normal(size=c), 1 + 0.2 * rng.random(c)
        self.nodes.append(Node(BN, [x], y, dict(ch=c, eps=eps, gamma=np.asarray(gamma, np.float32), beta=np.asarray(beta, np.float32),
                                                mean=np.asarray(mean, np.float32), var=np.asarray(var, np.float32))))
        return y

    def relu(self, x):
        y = self.buf(*self.bufs[x])
        self.nodes.append(Node(RELU, [x], y))
        return y

    def concat(self, xs):
        c = sum(self.bufs[x][0] for x in xs)
        _, h, w = self.bufs[xs[0]]
        y = self.buf(c, h, w)
        self.nodes.append(Node(CONCAT, list(xs), y))
        return y

    def gap(self, x):
        y = self.buf(self.bufs[x][0])
        self.nodes.append(Node(GAP, [x], y))
        return y

    def dense(self, x, n_out, rng=None, w=None, b=None):
        n_in = int(np.prod(self.bufs[x]))
        y = self.buf(n_out)
        if w is None:
            w = rng.normal(0, np.sqrt(2.0 / n_in), (n_out, n_in))
            b = 0.01 * rng.normal(size=n_out)
        self.nodes.append(Node(DENSE, [x], y, dict(n_in=n_in, n_out=n_out, w=np.asarray(w, np.float32),
                                                   b=np.asarray(b, np.float32))))
        return y, len(self.nodes) - 1

    def n_params(self):
        n = 0
        for nd in self.nodes:
            for k in ("w", "b", "gamma", "beta"):
                if k in nd.p:
                    n += nd.p[k].size
        return n

    # -------------------------------------------------- serialisation
    def to_blob(self):
        out = [struct.pack("<IIIIIIii", MAGIC, VERSION, len(self.bufs), len(self.nodes),
                           self.out_gesture, self.out_fatigue, self.tb_dense1, self.tb_dense2)]
        for c, h, w in self.bufs:
            out.append(struct.pack("<III", c, h, w))
        for nd in self.nodes:
            ins = list(nd.inputs) + [0] * (3 - len(nd.inputs))
            out.append(struct.pack("<IIIIII", nd.type, len(nd.inputs), *ins, nd.out))
            p = nd.p
            if nd.type == CONV2D:
                out.append(struct.pack("<9I", p["out_ch"], p["in_ch"], p["kh"], p["kw"], p["sh"], p["sw"], p["ph"], p["pw"], p["groups"]))
                out += [p["w"].astype("<f4").tobytes(), p["b"].astype("<f4").tobytes()]
            elif nd.type == BN:
                out.append(struct.pack("<If", p["ch"], p["eps"]))
                out += [p[k].astype("<f4").tobytes() for k in ("gamma", "beta", "mean", "var")]
            elif nd.type == DENSE:
                out.append(struct.pack("<II", p["n_in"], p["n_out"]))
                out += [p["w"].astype("<f4").tobytes(), p["b"].astype("<f4").tobytes()]
        return b"".join(out)


# ------------------------------------------------------------------ architecture
def build_fama_net(rng=None, n_classes=8, in_shape=(8, 33, 9), ms_ch=16, pw1=96, pw2=128, shared=128,
                   head_hidden=64, fat_hidden=32, weights=None):
    """Reference FAMA-Net topology (model figure 1):

    multi-scale block (3x3 | 5x5 | 7x7 conv, each BN+ReLU) -> concat
    -> 2 x depthwise-separable conv (DW 3x3 stride (2,1) + BN + ReLU, PW 1x1 + BN + ReLU)
    -> GAP -> shared linear + ReLU
       -> gesture head: dense(head_hidden)+ReLU -> dense(n_classes)   [Tier B trains these two]
       -> fatigue head: dense(fat_hidden)+ReLU -> dense(1)  = s_hat

    `weights` (optional) is a callable(kind, index) -> dict of arrays that
    supplies trained parameters (see model.py::to_graph); otherwise random.
    """
    rng = rng or np.random.default_rng(0)
    g = Graph()
    W = weights or (lambda kind, i: {})
    ci = [0]

    def nxt():
        ci[0] += 1
        return ci[0] - 1

    x = g.buf(*in_shape)
    branches = []
    for k in (3, 5, 7):
        y = g.conv(x, ms_ch, k, rng=rng, **W("conv", nxt()))
        y = g.relu(g.bn(y, rng=rng, **W("bn", nxt())))
        branches.append(y)
    y = g.concat(branches)
    for out_ch in (pw1, pw2):
        c = g.bufs[y][0]
        y = g.conv(y, c, 3, stride=(2, 1), groups=c, rng=rng, **W("conv", nxt()))
        y = g.relu(g.bn(y, rng=rng, **W("bn", nxt())))
        y = g.conv(y, out_ch, 1, rng=rng, **W("conv", nxt()))
        y = g.relu(g.bn(y, rng=rng, **W("bn", nxt())))
    y = g.gap(y)
    feat, _ = g.dense(y, shared, rng=rng, **W("dense", nxt()))
    feat = g.relu(feat)
    h, d1 = g.dense(feat, head_hidden, rng=rng, **W("dense", nxt()))
    h = g.relu(h)
    logits, d2 = g.dense(h, n_classes, rng=rng, **W("dense", nxt()))
    f, _ = g.dense(feat, fat_hidden, rng=rng, **W("dense", nxt()))
    f = g.relu(f)
    shat, _ = g.dense(f, 1, rng=rng, **W("dense", nxt()))
    g.out_gesture, g.out_fatigue, g.tb_dense1, g.tb_dense2 = logits, shat, d1, d2
    return g


# ------------------------------------------------------------------ forward
class NetState:
    """Mutable copy of BN statistics and head weights (Tier A / Tier B adapt these)."""

    def __init__(self, g: Graph):
        self.g = g
        self.reset()

    def reset(self):
        self.mean = {i: nd.p["mean"].astype(np.float64).copy() for i, nd in enumerate(self.g.nodes) if nd.type == BN}
        self.var = {i: nd.p["var"].astype(np.float64).copy() for i, nd in enumerate(self.g.nodes) if nd.type == BN}
        self.w = {i: nd.p["w"].astype(np.float64).copy() for i, nd in enumerate(self.g.nodes) if nd.type == DENSE}
        self.b = {i: nd.p["b"].astype(np.float64).copy() for i, nd in enumerate(self.g.nodes) if nd.type == DENSE}


def _conv(x, p):
    c, h, w = x.shape
    kh, kw, sh, sw, ph, pw, g = p["kh"], p["kw"], p["sh"], p["sw"], p["ph"], p["pw"], p["groups"]
    xp = np.pad(x, ((0, 0), (ph, ph), (pw, pw)))
    ho, wo = (h + 2 * ph - kh) // sh + 1, (w + 2 * pw - kw) // sw + 1
    W = p["w"].astype(np.float64)
    out = np.empty((p["out_ch"], ho, wo))
    icg, ocg = c // g, p["out_ch"] // g
    # im2col per group
    cols = np.empty((c, kh, kw, ho, wo))
    for ky in range(kh):
        for kx in range(kw):
            cols[:, ky, kx] = xp[:, ky: ky + sh * ho: sh, kx: kx + sw * wo: sw]
    for gi in range(g):
        cg = cols[gi * icg:(gi + 1) * icg].reshape(icg * kh * kw, ho * wo)
        wg = W[gi * ocg:(gi + 1) * ocg].reshape(ocg, icg * kh * kw)
        out[gi * ocg:(gi + 1) * ocg] = (wg @ cg).reshape(ocg, ho, wo)
    return out + p["b"].astype(np.float64)[:, None, None]


def forward(state: NetState, x, adapt_alpha=0.0):
    """Returns (buffers list). Tier A updates state.mean/var when adapt_alpha > 0."""
    g = state.g
    bufs = [None] * len(g.bufs)
    bufs[0] = np.asarray(x, np.float64).reshape(g.bufs[0])
    for i, nd in enumerate(g.nodes):
        x = bufs[nd.inputs[0]]
        if nd.type == CONV2D:
            y = _conv(x, nd.p)
        elif nd.type == BN:
            if adapt_alpha > 0:
                mu = x.reshape(x.shape[0], -1).mean(axis=1)
                var = x.reshape(x.shape[0], -1).var(axis=1)
                state.mean[i] = (1 - adapt_alpha) * state.mean[i] + adapt_alpha * mu
                state.var[i] = (1 - adapt_alpha) * state.var[i] + adapt_alpha * var
            sc = nd.p["gamma"] / np.sqrt(state.var[i] + nd.p["eps"])
            y = x * sc[:, None, None] + (nd.p["beta"] - state.mean[i] * sc)[:, None, None]
        elif nd.type == RELU:
            y = np.maximum(x, 0)
        elif nd.type == CONCAT:
            y = np.concatenate([bufs[j] for j in nd.inputs], axis=0)
        elif nd.type == GAP:
            y = x.reshape(x.shape[0], -1).mean(axis=1)
        elif nd.type == DENSE:
            y = state.w[i] @ x.reshape(-1) + state.b[i]
        bufs[nd.out] = y
    return bufs


def softmax(z):
    z = np.asarray(z, np.float64)
    e = np.exp(z - z.max())
    return e / e.sum()


# ------------------------------------------------------------------ Tier B
class TierB:
    """Reference of firmware tier_b.c (model figure 2)."""

    def __init__(self, state: NetState, sampen_ratio=0.8, shat_threshold=0.15, conf_threshold=0.9, min_windows=8,
                 steps=4, lr=1e-3, n_layers=2, refractory_windows=300, hist=24):
        self.s = state
        self.cfg = dict(sampen_ratio=sampen_ratio, shat_threshold=shat_threshold, conf_threshold=conf_threshold,
                        min_windows=min_windows, steps=steps, lr=lr, n_layers=n_layers, refractory=refractory_windows)
        self.hist = hist
        self.clear()

    def clear(self):
        self.feat, self.prob, self.shat = [], [], []
        self.cooldown = 0
        self.n_updates = 0

    def record(self, bufs, probs, shat):
        g = self.s.g
        first = g.tb_dense1 if self.cfg["n_layers"] >= 2 else g.tb_dense2
        self.feat.append(np.asarray(bufs[g.nodes[first].inputs[0]], np.float64).reshape(-1).copy())
        self.prob.append(np.asarray(probs, np.float64))
        self.shat.append(float(shat))
        if len(self.feat) > self.hist:
            self.feat.pop(0); self.prob.pop(0); self.shat.pop(0)

    def step(self, sampen_valid, sampen, baseline):
        c = self.cfg
        if self.cooldown > 0:
            self.cooldown -= 1
        if not sampen_valid or not (sampen < c["sampen_ratio"] * baseline):
            return False
        if len(self.feat) < self.hist or not (np.mean(self.shat) > c["shat_threshold"]):
            return False
        if self.cooldown > 0:
            return False
        sel = [(i, int(np.argmax(p))) for i, p in enumerate(self.prob) if p.max() > c["conf_threshold"]]
        if len(sel) < c["min_windows"]:
            return False
        g, s = self.s.g, self.s
        d1, d2 = (g.tb_dense1 if c["n_layers"] >= 2 else None), g.tb_dense2
        k = len(sel)
        for _ in range(c["steps"]):
            g2w = np.zeros_like(s.w[d2]); g2b = np.zeros_like(s.b[d2])
            if d1 is not None:
                g1w = np.zeros_like(s.w[d1]); g1b = np.zeros_like(s.b[d1])
            for i, lab in sel:
                x = self.feat[i]
                if d1 is not None:
                    z1 = s.w[d1] @ x + s.b[d1]
                    h = np.maximum(z1, 0)
                else:
                    h = x
                p = softmax(s.w[d2] @ h + s.b[d2])
                dz2 = p.copy(); dz2[lab] -= 1
                g2w += np.outer(dz2, h); g2b += dz2
                if d1 is not None:
                    dh = (s.w[d2].T @ dz2) * (z1 > 0)
                    g1w += np.outer(dh, x); g1b += dh
            lr = c["lr"] / k
            s.w[d2] -= lr * g2w; s.b[d2] -= lr * g2b
            if d1 is not None:
                s.w[d1] -= lr * g1w; s.b[d1] -= lr * g1b
        self.cooldown = c["refractory"]
        self.n_updates += 1
        return True
