"""PyTorch FAMA-Net (training side) and its export to the device graph.

The topology follows model figure 1 and matches net_ref.build_fama_net, so a
model trained here (or any model with the same layer structure) exports
losslessly to the firmware blob:

    python -m famaband.export --checkpoint model.pt --out fama_net.bin

Training loss (thesis): L_total = L_gesture + lambda * L_fatigue, with
s_label(t) = (SampEn_baseline - SampEn(t)) / SampEn_baseline.
"""
import numpy as np
import torch
from torch import nn

from .net_ref import build_fama_net


class FamaNet(nn.Module):
    def __init__(self, n_classes=8, in_ch=8, ms_ch=16, pw1=96, pw2=128, shared=128, head_hidden=64, fat_hidden=32):
        super().__init__()
        self.cfg = dict(n_classes=n_classes, ms_ch=ms_ch, pw1=pw1, pw2=pw2, shared=shared, head_hidden=head_hidden,
                        fat_hidden=fat_hidden)
        self.ms = nn.ModuleList([nn.Sequential(nn.Conv2d(in_ch, ms_ch, k, padding=k // 2), nn.BatchNorm2d(ms_ch), nn.ReLU())
                                 for k in (3, 5, 7)])
        c = 3 * ms_ch
        blocks = []
        for out_ch in (pw1, pw2):
            blocks += [nn.Conv2d(c, c, 3, stride=(2, 1), padding=1, groups=c), nn.BatchNorm2d(c), nn.ReLU(),
                       nn.Conv2d(c, out_ch, 1), nn.BatchNorm2d(out_ch), nn.ReLU()]
            c = out_ch
        self.dsconv = nn.Sequential(*blocks)
        self.shared = nn.Linear(c, shared)
        self.gesture = nn.Sequential(nn.Linear(shared, head_hidden), nn.ReLU(), nn.Linear(head_hidden, n_classes))
        self.fatigue = nn.Sequential(nn.Linear(shared, fat_hidden), nn.ReLU(), nn.Linear(fat_hidden, 1))

    def forward(self, x):
        y = torch.cat([b(x) for b in self.ms], dim=1)
        y = self.dsconv(y).mean(dim=(2, 3))
        f = torch.relu(self.shared(y))
        return self.gesture(f), self.fatigue(f)


def multitask_loss(logits, shat, y_gesture, s_label, lam=0.2):
    return nn.functional.cross_entropy(logits, y_gesture) + lam * nn.functional.mse_loss(shat.squeeze(-1), s_label)


def to_graph(model: FamaNet):
    """Export trained weights into the device graph (same node order as build_fama_net)."""
    convs, bns, denses = [], [], []
    for b in model.ms:
        convs.append(b[0]); bns.append(b[1])
    for m in model.dsconv:
        if isinstance(m, nn.Conv2d):
            convs.append(m)
        elif isinstance(m, nn.BatchNorm2d):
            bns.append(m)
    denses = [model.shared, model.gesture[0], model.gesture[2], model.fatigue[0], model.fatigue[2]]
    order = []  # interleaving used by build_fama_net: conv, bn, conv, bn, ...
    for cv, bn in zip(convs, bns):
        order += [("conv", cv), ("bn", bn)]
    order += [("dense", d) for d in denses]

    def t(a):
        return a.detach().cpu().numpy().astype(np.float64)

    def supply(kind, i):
        k, mod = order[i]
        assert k == kind, (k, kind, i)
        if kind == "conv":
            return dict(w=t(mod.weight), b=t(mod.bias) if mod.bias is not None else np.zeros(mod.out_channels))
        if kind == "bn":
            return dict(gamma=t(mod.weight), beta=t(mod.bias), mean=t(mod.running_mean), var=t(mod.running_var), eps=mod.eps)
        return dict(w=t(mod.weight), b=t(mod.bias))

    c = model.cfg
    return build_fama_net(n_classes=c["n_classes"], ms_ch=c["ms_ch"], pw1=c["pw1"], pw2=c["pw2"], shared=c["shared"],
                          head_hidden=c["head_hidden"], fat_hidden=c["fat_hidden"], weights=supply)
