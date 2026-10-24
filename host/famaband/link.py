"""Host side of the Wi-Fi/UDP link: discovery, commands, packet reception, recording."""
import socket
import struct
import threading
import time

from . import protocol as P
from .io import FbrWriter

DEFAULT_PORT = 5005


def discover(timeout=5.0, port=DEFAULT_PORT):
    """Wait for a device beacon (STATUS broadcast to port+1). Returns the device IP or None."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", port + 1))
    s.settimeout(timeout)
    try:
        data, addr = s.recvfrom(2048)
        pkts, _, _ = P.parse(data)
        if pkts and pkts[0].type == P.T_STATUS:
            return addr[0]
    except socket.timeout:
        return None
    finally:
        s.close()
    return None


class DeviceLink:
    """UDP connection to one FAMA-Band. Received packets are passed to callbacks and,
    when recording, appended to an .fbr file exactly as received."""

    def __init__(self, device_ip, port=DEFAULT_PORT, local_port=0):
        self.addr = (device_ip, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("", local_port))
        self.sock.settimeout(0.2)
        self.callbacks = []
        self.writer = None
        self.lock = threading.Lock()
        self.stats = dict(packets=0, crc_errors=0, lost=0, emg_frames=0)
        self._last_seq = {}
        self._run = True
        self._seq = 0
        self.thread = threading.Thread(target=self._rx, daemon=True)
        self.thread.start()
        self.send_cmd(P.CMD_PING)

    def _rx(self):
        while self._run:
            try:
                data, _ = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            pkts, _, bad = P.parse(data)
            self.stats["crc_errors"] += bad
            for p in pkts:
                self.stats["packets"] += 1
                if p.type == P.T_EMG_1K:
                    last = self._last_seq.get(p.type)
                    if last is not None and p.seq != (last + 1) & 0xFFFFFFFF:
                        self.stats["lost"] += (p.seq - last - 1) & 0xFFFFFFFF
                    self._last_seq[p.type] = p.seq
                    self.stats["emg_frames"] += p.n
                with self.lock:
                    if self.writer:
                        self.writer.write(P.pack(p.type, p.seq, p.frame, p.n, p.payload))
                for cb in self.callbacks:
                    cb(p)

    def send_cmd(self, cmd, args=b""):
        self._seq += 1
        self.sock.sendto(P.pack_cmd(cmd, args, self._seq), self.addr)

    def session_start(self, session_id):
        self.send_cmd(P.CMD_SESSION_START, session_id.encode()[:23])

    def session_end(self):
        self.send_cmd(P.CMD_SESSION_END)

    def marker(self, code, label=""):
        self.send_cmd(P.CMD_MARKER, struct.pack("<H", code) + label.encode()[:200])

    def set_mode(self, mode):
        self.send_cmd(P.CMD_SET_MODE, bytes([mode]))

    def record(self, path, **meta):
        with self.lock:
            if self.writer:
                self.writer.close()
            self.writer = FbrWriter(path, source="host-udp", device=self.addr[0], **meta) if path else None

    def close(self):
        self._run = False
        self.record(None)
        time.sleep(0.25)
        self.sock.close()
