"""Session protocol runner: cues the subject, sends markers, records the session.

Implements the thesis protocol (review sheet §2, §4): MVC trials before and
after the session (the MVC drop is one of the three fatigue criteria), then
`cycles` x [gesture block G, fatigue block F] with the isometric load rising
from 50 % to 75 % MVC. Every cue becomes a marker in the device timebase, so
the 60/10/30 % temporal split can be cut from the recording afterwards.

    python -m famaband.protocol_runner --subject S01 --day 1 [--device 192.168.1.50]
           [--config my_protocol.json] [--force-serial /dev/ttyUSB0]

Marker codes (u16):
    0x01gg / 0x02gg  gesture gg cue on / off           (gg = index in GESTURES)
    0x0300+k / 0x0400+k  MVC trial k start / end       (k = 0.. pre, 0x80+.. post)
    0x0500+p / 0x0600+p  fatigue contraction start/end (p = target % MVC)
    0x0700+c             cycle c begins
    0xFFFF               device button
"""
import argparse
import json
import os
import sys
import threading
import time

from . import protocol as P
from .link import DeviceLink, discover

DEFAULT = dict(
    gestures=P.GESTURES,
    mvc=dict(trials=3, hold_s=5.0, rest_s=60.0),
    cycles=8,
    gesture_block=dict(reps=5, hold_s=5.0, rest_s=3.0),
    fatigue_block=dict(contractions=6, hold_s=20.0, rest_s=10.0, level_start=0.50, level_end=0.75),
    transition_rest_s=60.0,
)


class ForceReader:
    """Optional hand dynamometer / load cell on a serial port, one force value (N) per line."""

    def __init__(self, port, baud=115200):
        import serial  # pyserial
        self.ser = serial.Serial(port, baud, timeout=0.1)
        self.value = 0.0
        self.peak = 0.0
        self.log = []
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            line = self.ser.readline().strip()
            try:
                self.value = float(line)
            except ValueError:
                continue
            self.peak = max(self.peak, self.value)
            self.log.append((time.time(), self.value))


class Runner:
    def __init__(self, link, cfg, out_dir, subject, day, time_scale=1.0, force=None, out=sys.stdout):
        self.link, self.cfg, self.out_dir = link, cfg, out_dir
        self.subject, self.day = subject, day
        self.ts = time_scale
        self.force = force
        self.out = out
        self.frame = 0
        self.timetable = []
        self.mvc = {}
        link.callbacks.append(self._on_packet)

    def _on_packet(self, p):
        if p.type in (P.T_EMG_1K, P.T_STATUS, P.T_RESULT):
            self.frame = max(self.frame, p.frame)

    def say(self, text):
        print(f"\033[1m{text}\033[0m" if self.out.isatty() else text, file=self.out, flush=True)

    def wait(self, seconds, label=""):
        end = time.time() + seconds * self.ts
        while time.time() < end:
            if self.force and label:
                print(f"\r{label}: {self.force.value:6.1f} N", end="", file=self.out, flush=True)
            time.sleep(min(0.1, max(0.0, end - time.time())))
        if self.force and label:
            print(file=self.out)

    def mark(self, code, label):
        self.link.marker(code, label)
        self.timetable.append(dict(t=time.time(), frame_estimate=self.frame, code=code, label=label))

    def mvc_block(self, phase):
        c = self.cfg["mvc"]
        base = 0x80 if phase == "post" else 0
        peaks = []
        for k in range(c["trials"]):
            self.say(f"MVC {phase} {k + 1}/{c['trials']}: squeeze as hard as you can in 3 ... 2 ... 1")
            self.wait(3)
            if self.force:
                self.force.peak = 0.0
            self.mark(0x0300 + base + k, f"MVC_{phase}_{k + 1}_START")
            self.say("  SQUEEZE!")
            self.wait(c["hold_s"], "force")
            self.mark(0x0400 + base + k, f"MVC_{phase}_{k + 1}_END")
            if self.force:
                peaks.append(self.force.peak)
            if k < c["trials"] - 1:
                self.say(f"  rest {c['rest_s']:.0f} s")
                self.wait(c["rest_s"])
        if peaks:
            self.mvc[phase] = max(peaks)
            self.say(f"  MVC {phase} = {self.mvc[phase]:.1f} N")

    def gesture_block(self, cycle):
        g = self.cfg["gesture_block"]
        names = self.cfg["gestures"]
        for rep in range(g["reps"]):
            for gi, name in enumerate(names):
                self.say(f"[cycle {cycle + 1}] {name.upper()}")
                self.mark(0x0100 + gi, f"G_{name}")
                self.wait(g["hold_s"])
                self.mark(0x0200 + gi, f"G_{name}_END")
                self.say("  relax")
                self.wait(g["rest_s"])

    def fatigue_block(self, cycle):
        f = self.cfg["fatigue_block"]
        n = max(1, self.cfg["cycles"] - 1)
        level = f["level_start"] + (f["level_end"] - f["level_start"]) * cycle / n
        pct = int(round(level * 100))
        target = self.mvc.get("pre", 0) * level
        for k in range(f["contractions"]):
            self.say(f"[cycle {cycle + 1}] FATIGUE {k + 1}/{f['contractions']}: hold {pct} % MVC"
                     + (f" = {target:.0f} N" if target else ""))
            self.mark(0x0500 + pct, f"F_{pct}_START")
            self.wait(f["hold_s"], f"target {target:.0f} N" if target else "")
            self.mark(0x0600 + pct, f"F_{pct}_END")
            self.wait(f["rest_s"])

    def run(self):
        sid = f"{self.subject}_D{self.day}"
        os.makedirs(self.out_dir, exist_ok=True)
        path = os.path.join(self.out_dir, f"{sid}_{time.strftime('%Y%m%d_%H%M%S')}.fbr")
        self.link.record(path, subject=self.subject, day=self.day, protocol=self.cfg)
        self.link.session_start(sid)
        self.wait(2)
        try:
            self.mvc_block("pre")
            for c in range(self.cfg["cycles"]):
                self.mark(0x0700 + c, f"CYCLE_{c + 1}")
                self.gesture_block(c)
                self.fatigue_block(c)
                if c < self.cfg["cycles"] - 1:
                    self.say(f"rest {self.cfg['transition_rest_s']:.0f} s")
                    self.wait(self.cfg["transition_rest_s"])
            self.mvc_block("post")
        finally:
            self.link.session_end()
            self.wait(1)
            self.link.record(None)
            summary = dict(session=sid, file=path, mvc_newton=self.mvc, markers=self.timetable,
                           link_stats=self.link.stats, protocol=self.cfg)
            if self.mvc.get("pre") and self.mvc.get("post"):
                summary["mvc_drop_pct"] = 100 * (1 - self.mvc["post"] / self.mvc["pre"])
            with open(path.replace(".fbr", "_summary.json"), "w") as fh:
                json.dump(summary, fh, indent=1)
            if self.force:
                with open(path.replace(".fbr", "_force.csv"), "w") as fh:
                    fh.write("host_time_s,force_N\n")
                    fh.writelines(f"{t:.4f},{v:.3f}\n" for t, v in self.force.log)
        return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subject", required=True)
    ap.add_argument("--day", type=int, default=1)
    ap.add_argument("--device", help="device IP (default: wait for its beacon)")
    ap.add_argument("--port", type=int, default=5005)
    ap.add_argument("--config", help="JSON file overriding protocol parameters")
    ap.add_argument("--out", default="recordings")
    ap.add_argument("--force-serial")
    ap.add_argument("--time-scale", type=float, default=1.0, help="<1 to rehearse quickly")
    a = ap.parse_args()
    cfg = json.loads(json.dumps(DEFAULT))
    if a.config:
        cfg.update(json.load(open(a.config)))
    ip = a.device or discover(10, a.port)
    if not ip:
        sys.exit("no FAMA-Band found (is it switched on, unplugged from USB and on the lab Wi-Fi?)")
    link = DeviceLink(ip, a.port)
    force = ForceReader(a.force_serial) if a.force_serial else None
    path = Runner(link, cfg, a.out, a.subject, a.day, a.time_scale, force).run()
    link.close()
    print("saved", path)


if __name__ == "__main__":
    main()
