"""Reference (NumPy/SciPy) implementation of the FAMA-Band on-device signal chain.

This module is the specification of the firmware DSP: the C code in
firmware/components/fama_dsp must reproduce these functions sample for
sample (checked by host/tests/test_dsp.py through ctypes). Filter
coefficients for the firmware are generated from here by
`python -m famaband.dsp_ref --emit-header`.

Chain (per channel):
  ADS1298 @ 2000 SPS -> equaliser/anti-alias FIR, decimate by 2 -> 1000 Hz
  -> DC blocker -> Butterworth band-pass 20-450 Hz (order 4, i.e. 4 SOS)
  -> notches 50/100/150 Hz (Q = 30) -> causal running z-score
  -> 200 ms windows, hop 100 ms -> STFT (n_fft 64, hop 16, Hann) -> log1p|X|
"""
import argparse
import os

import numpy as np
from scipy import signal

N_CH = 8
FS_ADC = 2000.0
FS = 1000.0
DECIM_TAPS = 63
WIN = 200
HOP = 100
NFFT = 64
STFT_HOP = 16
N_FREQ = NFFT // 2 + 1                 # 33
N_FRAMES = 1 + (WIN - NFFT) // STFT_HOP  # 9
BAND = (20.0, 450.0)
NOTCHES = (50.0, 100.0, 150.0)
NOTCH_Q = 30.0
DC_POLE = 0.995
Z_TAU_S = 10.0
Z_EPS = 1e-6
ADC_LSB_V = 2 * 2.4 / 6 / 2 ** 24      # VREF 2.4 V, gain 6, 24 bit


# ----------------------------------------------------------------- filters
def ads1298_sinc3(f, f_dr=FS_ADC):
    """Magnitude of the ADS1298 sinc^3 decimation filter at output rate f_dr."""
    return np.abs(np.sinc(np.asarray(f) / f_dr)) ** 3


def design_decimator(taps=DECIM_TAPS, stop_edge=560.0):
    """Linear-phase least-squares FIR at 2 kHz: flattens the sinc^3 droop over
    0-450 Hz and rejects >= 560 Hz, which would alias into 20-450 Hz after
    decimation. Combined with sinc^3: +/-0.06 dB passband, <= -58 dB stopband."""
    edges = np.linspace(0, 450, 31)
    bands, desired = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        bands += [a, b]
        desired += [1.0 / ads1298_sinc3(a), 1.0 / ads1298_sinc3(b)]
    bands += [stop_edge, FS_ADC / 2]
    desired += [0.0, 0.0]
    return signal.firls(taps, bands, desired, weight=[1.0] * 30 + [30.0], fs=FS_ADC)


def design_iir():
    sos_bp = signal.butter(4, BAND, btype="bandpass", fs=FS, output="sos")
    # iirnotch returns (b, a) with a[0] == 1, which is already one SOS row
    sos_notch = np.array([np.concatenate(signal.iirnotch(f0, NOTCH_Q, fs=FS)) for f0 in NOTCHES])
    return np.vstack([sos_bp, sos_notch])


DECIM_H = design_decimator()
SOS = design_iir()
Z_ALPHA = 1.0 - np.exp(-1.0 / (Z_TAU_S * FS))


def decimate(x2k, h=DECIM_H):
    """x2k: [..., n] at 2 kHz -> [..., n//2] at 1 kHz (causal, output at odd input index)."""
    y = signal.lfilter(h, [1.0], x2k, axis=-1)
    return y[..., 1::2]


def dc_block(x):
    return signal.lfilter([1.0, -1.0], [1.0, -DC_POLE], x, axis=-1)


def bandpass_notch(x):
    return signal.sosfilt(SOS, x, axis=-1)


def running_zscore(x, alpha=Z_ALPHA):
    """Causal per-channel z-score with exponential running mean/variance.

    Both statistics start from the first sample (mean = x0, var = 0) and the
    output uses the statistics *before* the current sample is folded in.
    """
    x = np.atleast_2d(np.asarray(x, dtype=np.float64))
    out = np.empty_like(x)
    mean = x[:, 0].copy()
    var = np.zeros(x.shape[0])
    for i in range(x.shape[1]):
        v = x[:, i]
        out[:, i] = (v - mean) / np.sqrt(var + Z_EPS)
        d = v - mean
        mean = mean + alpha * d
        var = (1 - alpha) * (var + alpha * d * d)
    return out


def preprocess(x1k):
    """Full per-sample chain at 1 kHz: DC block -> band-pass+notch -> z-score."""
    return running_zscore(bandpass_notch(dc_block(x1k)))


# ----------------------------------------------------------------- STFT
HANN = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(NFFT) / NFFT))  # periodic Hann


def stft_window(win):
    """win: [8, 200] -> [8, 33, 9] log1p magnitude spectrogram."""
    win = np.asarray(win, dtype=np.float64)
    out = np.empty((win.shape[0], N_FREQ, N_FRAMES))
    for t in range(N_FRAMES):
        seg = win[:, t * STFT_HOP: t * STFT_HOP + NFFT] * HANN
        out[:, :, t] = np.log1p(np.abs(np.fft.rfft(seg, axis=-1)))
    return out


def windows(x, win=WIN, hop=HOP):
    n = (x.shape[-1] - win) // hop + 1
    return np.stack([x[:, i * hop: i * hop + win] for i in range(max(n, 0))]) if n > 0 else np.empty((0, x.shape[0], win))


# ----------------------------------------------------------------- SampEn
def sample_entropy(x, m=2, r_factor=0.2):
    """Sample entropy (Richman & Moorman 2000), Chebyshev distance <= r,
    r = r_factor * population std, self-matches excluded, N - m templates
    for both lengths. Returns +inf-capped value 10.0 when no matches."""
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    r = r_factor * x.std()
    nt = n - m
    t_m = np.stack([x[i: i + nt] for i in range(m)], axis=1)
    b = a = 0
    for i in range(nt - 1):
        d = np.max(np.abs(t_m[i + 1:] - t_m[i]), axis=1)
        close = d <= r
        b += int(np.count_nonzero(close))
        ext = np.abs(x[i + 1 + m: nt + m] - x[i + m]) <= r
        a += int(np.count_nonzero(close & ext))
    if a == 0 or b == 0:
        return 10.0
    return float(-np.log(a / b))


# ----------------------------------------------------------------- header gen
def emit_header(path):
    def arr(name, vals, ctype="float"):
        body = ",\n    ".join(", ".join(f"{v:.10e}f" for v in vals[i:i + 4]) for i in range(0, len(vals), 4))
        return f"static const {ctype} {name}[{len(vals)}] = {{\n    {body}\n}};\n"

    sos = SOS
    lines = [
        "/* Generated by host/famaband/dsp_ref.py --emit-header. Do not edit. */",
        "#pragma once",
        f"#define FAMA_N_CH {N_CH}",
        f"#define FAMA_FS_ADC {int(FS_ADC)}",
        f"#define FAMA_FS {int(FS)}",
        f"#define FAMA_DECIM_TAPS {DECIM_TAPS}",
        f"#define FAMA_N_SOS {len(sos)}",
        f"#define FAMA_WIN {WIN}",
        f"#define FAMA_HOP {HOP}",
        f"#define FAMA_NFFT {NFFT}",
        f"#define FAMA_STFT_HOP {STFT_HOP}",
        f"#define FAMA_N_FREQ {N_FREQ}",
        f"#define FAMA_N_FRAMES {N_FRAMES}",
        f"#define FAMA_DC_POLE {DC_POLE:.10e}f",
        f"#define FAMA_Z_ALPHA {Z_ALPHA:.17e}",
        f"#define FAMA_Z_EPS {Z_EPS:.17e}",
        f"#define FAMA_ADC_LSB_V {ADC_LSB_V:.10e}",
        arr("FAMA_DECIM_H", DECIM_H),
        "/* SOS rows: b0 b1 b2 a0(=1) a1 a2; 4 band-pass sections then 3 notches */",
        arr("FAMA_SOS", sos.reshape(-1)),
        arr("FAMA_HANN", HANN),
    ]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-header", action="store_true")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "..", "firmware", "components",
                                                   "fama_dsp", "include", "fama_coeffs.h"))
    args = ap.parse_args()
    if args.emit_header:
        emit_header(os.path.normpath(args.out))
        print("wrote", os.path.normpath(args.out))


if __name__ == "__main__":
    main()
