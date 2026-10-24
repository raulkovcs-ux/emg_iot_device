"""Electrode contact check for donning (run before every session).

    python -m famaband.contact [--device IP] [--seconds 2]

For each channel it shows the DC offset (must stay well inside the ADS1298
common-mode window, see docs/03_hardware_design.md section 3.2), the 50 Hz
mains pickup (high = poor contact or a lifted electrode), and the EMG RMS in
20-450 Hz (relaxed forearm: a few uV; a fist: tens to hundreds of uV).
"""
import argparse
import sys
import time

import numpy as np
from scipy import signal

from . import protocol as P
from .link import DeviceLink, discover

DC_LIMIT_MV = 250.0     # leaves margin inside the +/-300 mV design window
MAINS_LIMIT_UV = 50.0


def assess(volts, fs=1000.0, mains=50.0):
    """volts: [8, n]. Returns a list of per-channel dicts."""
    out = []
    sos = signal.butter(4, [20, 450], "bandpass", fs=fs, output="sos")
    for ch, v in enumerate(volts):
        dc = float(np.mean(v))
        ac = v - dc
        f, pxx = signal.welch(ac, fs=fs, nperseg=min(len(ac), 1000))
        band = (f > mains - 2) & (f < mains + 2)
        mains_uv = float(np.sqrt(np.trapezoid(pxx[band], f[band])) * 1e6 * np.sqrt(2))  # amplitude
        rms_uv = float(np.std(signal.sosfiltfilt(sos, ac)) * 1e6)
        ok = abs(dc) * 1e3 < DC_LIMIT_MV and mains_uv < MAINS_LIMIT_UV
        out.append(dict(ch=ch + 1, dc_mv=dc * 1e3, mains_uv=mains_uv, emg_rms_uv=rms_uv, ok=ok))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device")
    ap.add_argument("--port", type=int, default=5005)
    ap.add_argument("--seconds", type=float, default=2.0)
    a = ap.parse_args()
    ip = a.device or discover(10, a.port)
    if not ip:
        sys.exit("no device found")
    link = DeviceLink(ip, a.port)
    chunks = []
    link.callbacks.append(lambda p: chunks.append(p.emg()) if p.type == P.T_EMG_1K else None)
    link.session_start("CONTACT")
    time.sleep(a.seconds + 1.0)
    link.session_end()
    link.close()
    if not chunks:
        sys.exit("no EMG received")
    v = np.concatenate(chunks)[-int(a.seconds * 1000):].T * P.LSB_V
    print(" ch   DC [mV]   50 Hz [uV]   EMG rms [uV]")
    for r in assess(v):
        print(f" {r['ch']:2d}  {r['dc_mv']:8.1f}   {r['mains_uv']:9.1f}   {r['emg_rms_uv']:11.1f}   "
              f"{'ok' if r['ok'] else 'CHECK CONTACT'}")


if __name__ == "__main__":
    main()
