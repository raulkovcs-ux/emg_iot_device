"""A software FAMA-Band that speaks the real UDP protocol (for testing host tools).

    python -m famaband.simulator_device [--port 5005] [--speed 1.0]

It beacons on port+1, accepts the same commands as the firmware and streams
synthetic sEMG (famaband.simulator) as FB_T_EMG_1K packets, plus STATUS once
per second and echoes markers/sessions with the current frame index.
"""
import argparse
import socket
import struct
import threading
import time

import numpy as np

from . import dsp_ref, protocol as P
from .simulator import synthesize, to_codes


class SimDevice:
    def __init__(self, port=5005, speed=1.0, seed=0, duration_s=600):
        self.port, self.speed = port, speed
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.settimeout(0.05)
        x, _, _ = synthesize(duration_s, [1, 2, 3, 4, 5, 6, 7, 0], lambda t: min(1.0, max(0.0, (t - 120) / 300)), seed=seed)
        # [n, 8] at 1 kHz; drop the FIR start-up ramp (the device has it once, at power-up)
        self.stream = to_codes(dsp_ref.decimate(x)).T[200:]
        self.host = None
        self.session = False
        self.session_id = "none"
        self.frame = 0
        self.seq = 0
        self.run = True

    def _send(self, ptype, n, payload):
        if self.host:
            self.sock.sendto(P.pack(ptype, self.seq, self.frame, n, payload), self.host)
            self.seq += 1

    def _handle(self, data, addr):
        pkts, _, _ = P.parse(data)
        for p in pkts:
            if p.type != P.T_CMD:
                continue
            self.host = addr
            cmd, args = p.payload[0], p.payload[1:]
            if cmd == P.CMD_SESSION_START:
                self.session, self.session_id = True, args.decode() or "S000"
                self._send(P.T_SESSION, len(args), b"\x01" + args)
            elif cmd == P.CMD_SESSION_END:
                self._send(P.T_SESSION, len(self.session_id), b"\x02" + self.session_id.encode())
                self.session = False
            elif cmd == P.CMD_MARKER:
                self._send(P.T_MARKER, len(args) - 2, args)

    def serve(self, seconds=None):
        t0 = time.time()
        next_status = t0
        while self.run and (seconds is None or time.time() - t0 < seconds):
            try:
                data, addr = self.sock.recvfrom(2048)
                self._handle(data, addr)
            except socket.timeout:
                pass
            now = time.time()
            if now >= next_status:
                st = P.STATUS.pack(90, 3900, (2 if self.session else 0) | 4 | (8 if self.host else 0) | 16, 0,
                                   int(now - t0), self.session_id.encode()[:24])
                if self.host:
                    self._send(P.T_STATUS, 1, st)
                else:
                    self.sock.sendto(P.pack(P.T_STATUS, 0, 0, 0, b"FAMA-Band"), ("127.0.0.1", self.port + 1))
                next_status = now + 1.0
            due = int((now - t0) * 1000 * self.speed)
            while self.host and self.frame + 25 <= due:
                i = self.frame % (len(self.stream) - 25)
                self.sock.sendto(P.pack_emg(P.T_EMG_1K, self.seq, self.frame, self.stream[i:i + 25]), self.host)
                self.seq += 1
                self.frame += 25

    def start(self, seconds=None):
        th = threading.Thread(target=self.serve, args=(seconds,), daemon=True)
        th.start()
        return th

    def stop(self):
        self.run = False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5005)
    ap.add_argument("--speed", type=float, default=1.0)
    a = ap.parse_args()
    print(f"simulated FAMA-Band on 127.0.0.1:{a.port}")
    SimDevice(a.port, a.speed).serve()


if __name__ == "__main__":
    main()
