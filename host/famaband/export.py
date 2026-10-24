"""Export FAMA-Net weights to the device blob (copy it to the microSD card as fama_net.bin).

    python -m famaband.export --checkpoint model.pt --out fama_net.bin
    python -m famaband.export --random --out fama_net.bin      # untrained, for bring-up

A checkpoint is torch.save({"state_dict": model.state_dict(), "cfg": model.cfg}).
"""
import argparse

import numpy as np

from .net_ref import build_fama_net


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint")
    ap.add_argument("--random", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="fama_net.bin")
    a = ap.parse_args()
    if a.checkpoint:
        import torch
        from .model import FamaNet, to_graph
        ck = torch.load(a.checkpoint, map_location="cpu")
        m = FamaNet(**{k: v for k, v in ck["cfg"].items()})
        m.load_state_dict(ck["state_dict"])
        m.eval()
        g = to_graph(m)
    elif a.random:
        g = build_fama_net(np.random.default_rng(a.seed))
    else:
        ap.error("give --checkpoint or --random")
    blob = g.to_blob()
    open(a.out, "wb").write(blob)
    act = sum(c * h * w for c, h, w in g.bufs)
    print(f"{a.out}: {len(blob)} bytes, {g.n_params()} parameters, {len(g.nodes)} nodes, "
          f"activations {act * 4 / 1024:.0f} kB (device arena ~{(len(blob) + act * 4) / 1024:.0f} kB in PSRAM)")


if __name__ == "__main__":
    main()
