"""Record a FAMA-Band stream to an .fbr file (no protocol cues).

    python -m famaband.record --session S01_free --seconds 60 [--device IP] [--out x.fbr]
"""
import argparse
import sys
import time

from . import protocol as P
from .link import DeviceLink, discover


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device")
    ap.add_argument("--port", type=int, default=5005)
    ap.add_argument("--session", default="FREE")
    ap.add_argument("--seconds", type=float, default=0, help="0 = until Ctrl-C")
    ap.add_argument("--out")
    a = ap.parse_args()
    ip = a.device or discover(10, a.port)
    if not ip:
        sys.exit("no device found")
    link = DeviceLink(ip, a.port)
    last = {}
    link.callbacks.append(lambda p: last.__setitem__(p.type, p))
    out = a.out or f"{a.session}_{time.strftime('%Y%m%d_%H%M%S')}.fbr"
    link.record(out, session=a.session)
    link.session_start(a.session)
    t0 = time.time()
    try:
        while not a.seconds or time.time() - t0 < a.seconds:
            time.sleep(1)
            s = link.stats
            st = last.get(P.T_STATUS)
            bat = f"{st.status()['battery_pct']}%" if st and st.n else "?"
            r = last.get(P.T_RESULT)
            gest = P.GESTURES[r.result()["gesture"]] if r and r.result()["gesture"] < 8 else "-"
            print(f"\r{time.time() - t0:6.0f} s  frames {s['emg_frames']:8d}  lost pkts {s['lost']:4d}  "
                  f"battery {bat:>4}  gesture {gest:<16}", end="", flush=True)
    except KeyboardInterrupt:
        pass
    link.session_end()
    time.sleep(0.5)
    link.close()
    print("\nsaved", out)


if __name__ == "__main__":
    main()
