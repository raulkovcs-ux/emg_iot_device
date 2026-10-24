"""Synthetic 8-channel forearm sEMG with gestures and muscle fatigue.

Used for tests and for exercising the host tools without hardware. It is
NOT a physiological model. It is a controllable signal with the two
properties FAMA-Net cares about:

* gesture-dependent spatial activation (each gesture drives a different
  mix of the 8 channels), and
* fatigue as spectral compression: as `fatigue` goes 0 -> 1 the
  signal's centre frequency drops and its spectrum narrows. That lowers
  the median frequency and sample entropy, as in real fatiguing contractions.

Output is in volts at the ADS1298 rate (2000 SPS), with electrode DC
offsets, 50 Hz mains and white noise added.
"""
import numpy as np
from scipy import signal

FS = 2000.0
N_CH = 8
GESTURES = ["rest", "fist", "open", "pinch", "flexion", "extension", "pronation", "supination"]


def activation_patterns(rng):
    pats = np.abs(rng.normal(0.3, 0.3, (len(GESTURES), N_CH)))
    pats[0] = 0.05  # rest
    for g in range(1, len(GESTURES)):
        pats[g, (g * 3) % N_CH] += 1.0
        pats[g, (g * 3 + 1) % N_CH] += 0.6
    return pats


def _shaped_noise(rng, n, fatigue):
    """Unit-variance band-limited noise; centre frequency 120 -> 55 Hz and
    bandwidth narrowing as fatigue rises (second-order resonator)."""
    f0 = 120.0 - 65.0 * fatigue
    bw = 150.0 - 110.0 * fatigue
    r = np.exp(-np.pi * bw / FS)
    a = [1.0, -2 * r * np.cos(2 * np.pi * f0 / FS), r * r]
    y = signal.lfilter([1.0], a, rng.normal(size=n))
    y = signal.lfilter(*signal.butter(2, 15.0, "highpass", fs=FS), y)
    return y / (y.std() + 1e-12)


def synthesize(duration_s, gesture_seq, fatigue_curve, seed=0, amp_v=200e-6, noise_v=2e-6, mains_v=30e-6,
               dc_v=0.15, block_s=1.0):
    """gesture_seq: list of gesture indices, one per block of `block_s` seconds (cycled).
    fatigue_curve: callable t_seconds -> fatigue in [0, 1].
    Returns (x [8, n] volts, labels [n] gesture index, fatigue [n])."""
    rng = np.random.default_rng(seed)
    pats = activation_patterns(np.random.default_rng(1234))
    n = int(duration_s * FS)
    blk = int(block_s * FS)
    x = np.zeros((N_CH, n))
    labels = np.zeros(n, np.int32)
    fat = np.zeros(n)
    for b0 in range(0, n, blk):
        b1 = min(n, b0 + blk)
        t = b0 / FS
        f = float(np.clip(fatigue_curve(t), 0, 1))
        g = gesture_seq[(b0 // blk) % len(gesture_seq)]
        for c in range(N_CH):
            x[c, b0:b1] = pats[g, c] * _shaped_noise(rng, b1 - b0, f)
        labels[b0:b1] = g
        fat[b0:b1] = f
    # fatigued muscle produces larger amplitude at constant force
    x *= amp_v * (1.0 + 0.6 * fat)
    tt = np.arange(n) / FS
    x += mains_v * np.sin(2 * np.pi * 50 * tt + rng.uniform(0, 2 * np.pi, (N_CH, 1)))
    x += noise_v * rng.normal(size=x.shape)
    x += rng.uniform(-dc_v, dc_v, (N_CH, 1))
    return x, labels, fat


def to_codes(x_volts, lsb=2 * 2.4 / 6 / 2 ** 24):
    """Volts -> ADS1298 24-bit two's complement codes (clipped)."""
    return np.clip(np.round(x_volts / lsb), -2 ** 23, 2 ** 23 - 1).astype(np.int32)
