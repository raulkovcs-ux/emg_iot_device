"""Firmware DSP (C, via ctypes) against the NumPy/SciPy reference and the spec."""
import numpy as np
import pytest
from scipy import signal

from famaband import cdsp
from famaband import dsp_ref as d


@pytest.fixture(scope="module")
def lib():
    return cdsp.load()


def test_decimator_response():
    """Spec ACQ-3: ADS1298 sinc^3 at 2 kSPS + firmware FIR is flat within
    +/-0.5 dB over 20-450 Hz, and everything that could alias into the band
    after decimation (>= 560 Hz) is suppressed by >= 50 dB."""
    w, h = signal.freqz(d.DECIM_H, worN=16384, fs=d.FS_ADC)
    total = np.abs(h) * d.ads1298_sinc3(w)
    band = (w >= 20) & (w <= 450)
    db = 20 * np.log10(total[band])
    assert db.min() > -0.5 and db.max() < 0.5
    assert 20 * np.log10(total[w >= 560].max()) < -50


def test_sinc3_alone_fails_at_1ksps():
    """Why the ADC runs at 2 kSPS: at 1 kSPS the sinc^3 is already -3 dB at 262 Hz."""
    assert 20 * np.log10(d.ads1298_sinc3(450.0, f_dr=1000.0)) < -9
    assert abs(20 * np.log10(d.ads1298_sinc3(262.0, f_dr=1000.0)) + 3.0) < 0.1


def test_bandpass_and_notches():
    w, h = signal.sosfreqz(d.SOS, worN=1 << 15, fs=d.FS)
    mag = 20 * np.log10(np.abs(h) + 1e-15)
    at = lambda f: mag[np.argmin(np.abs(w - f))]
    for f0 in (50, 100, 150):
        assert at(f0) < -30, f"notch at {f0} Hz only {at(f0):.1f} dB"
    assert abs(at(20) + 3) < 0.6 and abs(at(450) + 3) < 0.6
    assert abs(at(250)) < 0.5
    assert at(5) < -40


def test_decimate_matches_c(lib):
    x = np.random.default_rng(1).normal(0, 1e-3, (8, 4001))
    yc = cdsp.decimate(lib, x)
    yp = d.decimate(x)
    assert yc.shape == yp.shape
    np.testing.assert_allclose(yc, yp, atol=1e-8, rtol=1e-4)


def test_preprocess_matches_c(lib):
    rng = np.random.default_rng(2)
    x = 0.1 + rng.normal(0, 1e-4, (8, 20000))       # 20 s with a 100 mV electrode offset
    bpc, zc = cdsp.preprocess(lib, x)
    bp = d.bandpass_notch(d.dc_block(x))
    z = d.running_zscore(bp)
    # float32 vs float64 IIR: allow 0.2 % of the 100 uV signal scale
    np.testing.assert_allclose(bpc, bp, atol=2e-7, rtol=1e-3)
    # z-score: compare after the first second (initial transient divides by tiny variance)
    np.testing.assert_allclose(zc[:, 1000:], z[:, 1000:], atol=2e-3, rtol=2e-3)


def test_stft_matches_c(lib):
    win = np.random.default_rng(3).normal(size=(8, 200))
    np.testing.assert_allclose(cdsp.stft(lib, win), d.stft_window(win), atol=2e-5, rtol=1e-4)
    assert d.stft_window(win).shape == (8, 33, 9)


def _sampen_bruteforce(x, m=2, rf=0.2):
    x = np.asarray(x, np.float64); n = len(x); r = rf * x.std()
    def count(mm):
        t = [x[i:i + mm] for i in range(n - m)]
        return sum(1 for i in range(len(t)) for j in range(i + 1, len(t)) if np.max(np.abs(t[i] - t[j])) <= r)
    b, a = count(m), count(m + 1)
    return -np.log(a / b)


def test_sampen_reference_vs_bruteforce():
    x = np.random.default_rng(4).normal(size=200).astype(np.float32)
    assert abs(d.sample_entropy(x) - _sampen_bruteforce(x)) < 1e-12


def test_sampen_matches_c(lib):
    rng = np.random.default_rng(5)
    for _ in range(3):
        x = signal.lfilter([1], [1, -0.9], rng.normal(size=1000)).astype(np.float32)
        assert abs(cdsp.sampen(lib, x) - d.sample_entropy(x)) < 1e-3


def test_sampen_drops_with_fatigue():
    from famaband.simulator import _shaped_noise
    rng = np.random.default_rng(6)
    fresh = d.sample_entropy(_shaped_noise(rng, 2000, 0.0)[::2])
    tired = d.sample_entropy(_shaped_noise(rng, 2000, 1.0)[::2])
    assert tired < 0.8 * fresh
