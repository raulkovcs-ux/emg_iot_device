"""Reference of firmware pipeline.c: 2 kSPS frames in, one result per 100 ms window out."""
import numpy as np

from . import dsp_ref as d
from .net_ref import NetState, TierB, forward, softmax

SAMPEN_N = 1000
BASELINE_S = 60
WARMUP_WINDOWS = 300


def run(x2k, graph=None, **tb_kwargs):
    """x2k: [8, n] volts at 2000 SPS. Returns a list of per-window dicts."""
    y = d.decimate(np.asarray(x2k, np.float64))
    bp = d.bandpass_notch(d.dc_block(y))
    z = d.running_zscore(bp)
    n_out = y.shape[1]
    state = NetState(graph) if graph is not None else None
    tb = TierB(state, **tb_kwargs) if graph is not None else None
    sampen_hist, sampen_last, baseline = [], 0.0, 0.0
    next_sampen = SAMPEN_N
    results = []
    w = 0
    for end in range(d.WIN, n_out + 1, d.HOP):
        while next_sampen <= end:
            seg = bp[:, next_sampen - SAMPEN_N: next_sampen].astype(np.float32)
            sampen_last = float(np.mean([d.sample_entropy(ch) for ch in seg]))
            if len(sampen_hist) < BASELINE_S:
                sampen_hist.append(sampen_last)
                if len(sampen_hist) == BASELINE_S:
                    baseline = float(np.median(sampen_hist))
            next_sampen += SAMPEN_N
        spec = d.stft_window(z[:, end - d.WIN: end])
        r = dict(window=w, sampen=sampen_last, baseline=baseline if len(sampen_hist) >= BASELINE_S else -1.0,
                 sampen_valid=len(sampen_hist) >= BASELINE_S, spec=spec)
        if graph is not None:
            alpha = 0.10 if w < WARMUP_WINDOWS else 0.01
            bufs = forward(state, spec, adapt_alpha=alpha)
            probs = softmax(bufs[graph.out_gesture])
            shat = float(bufs[graph.out_fatigue][0])
            tb.record(bufs, probs, shat)
            r.update(probs=probs, gesture=int(np.argmax(probs)), shat=shat,
                     tb_updated=tb.step(r["sampen_valid"], sampen_last, baseline))
        results.append(r)
        w += 1
    return results
