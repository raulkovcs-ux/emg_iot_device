"""ctypes bridge to the firmware DSP/ML core built for the host
(firmware/components/fama_dsp/Makefile.host -> build/libfama_dsp.so)."""
import ctypes as C
import os
import subprocess

import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMP = os.path.join(ROOT, "firmware", "components", "fama_dsp")
LIB = os.path.join(COMP, "build", "libfama_dsp.so")
N_CH, WIN, N_FREQ, N_FRAMES, MAXC = 8, 200, 33, 9, 16

_f = np.ctypeslib.ndpointer(np.float32, flags="C_CONTIGUOUS")
_i = np.ctypeslib.ndpointer(np.int32, flags="C_CONTIGUOUS")


def build():
    subprocess.run(["make", "-s", "-f", "Makefile.host"], cwd=COMP, check=True)


def load():
    build()
    lib = C.CDLL(LIB)
    lib.fh_decimate.argtypes = [_f, C.c_int, _f]; lib.fh_decimate.restype = C.c_int
    lib.fh_preprocess.argtypes = [_f, C.c_int, _f, _f]
    lib.fh_stft.argtypes = [_f, _f]
    lib.fama_sampen.argtypes = [_f, C.c_int, C.c_int, C.c_float]; lib.fama_sampen.restype = C.c_float
    lib.fh_sizeof_net.restype = C.c_size_t
    lib.fh_sizeof_pipeline.restype = C.c_size_t
    lib.fama_net_arena_size.argtypes = [C.c_char_p, C.c_size_t]; lib.fama_net_arena_size.restype = C.c_size_t
    lib.fama_net_load.argtypes = [C.c_void_p, C.c_char_p, C.c_size_t, _f, C.c_size_t]; lib.fama_net_load.restype = C.c_int
    lib.fama_net_forward.argtypes = [C.c_void_p, _f]
    lib.fama_net_reset.argtypes = [C.c_void_p]
    lib.fh_net_set_alpha.argtypes = [C.c_void_p, C.c_float]
    lib.fama_net_buffer.argtypes = [C.c_void_p, C.c_uint32, C.POINTER(C.c_uint32)]
    lib.fama_net_buffer.restype = C.POINTER(C.c_float)
    lib.fh_pipeline_init.argtypes = [C.c_void_p, C.c_void_p, C.c_int, C.c_int, C.c_float, C.c_int]
    lib.fh_pipeline_run.argtypes = [C.c_void_p, _f, C.c_int, C.c_int, _f, _f, _f, _f, _i, _i]
    lib.fh_pipeline_run.restype = C.c_int
    lib.fh_pipeline_session_end.argtypes = [C.c_void_p]
    lib.fh_pipeline_tb_updates.argtypes = [C.c_void_p]; lib.fh_pipeline_tb_updates.restype = C.c_uint32
    lib.fb_pack_emg.argtypes = [C.c_char_p, C.c_size_t, C.c_uint8, C.c_uint32, C.c_uint32,
                                np.ctypeslib.ndpointer(np.int32, flags="C_CONTIGUOUS"), C.c_uint16]
    lib.fb_pack_emg.restype = C.c_size_t
    lib.fb_check.argtypes = [C.c_char_p, C.c_size_t]; lib.fb_check.restype = C.c_size_t
    lib.fb_crc16.argtypes = [C.c_char_p, C.c_size_t]; lib.fb_crc16.restype = C.c_uint16
    return lib


def pack_emg(lib, ptype, seq, frame, codes):
    codes = np.ascontiguousarray(codes, np.int32).reshape(-1, 8)
    buf = C.create_string_buffer(16 + codes.size * 3 + 2)
    n = lib.fb_pack_emg(buf, len(buf), ptype, seq, frame, codes, codes.shape[0])
    return buf.raw[:n]


class CNet:
    """A FAMA-Net instance living in C memory."""

    def __init__(self, lib, blob: bytes):
        self.lib = lib
        self.blob = blob
        self.mem = C.create_string_buffer(lib.fh_sizeof_net())
        n = lib.fama_net_arena_size(blob, len(blob))
        if n == 0:
            raise ValueError("blob rejected by fama_net_arena_size")
        self.arena = np.zeros(n, np.float32)
        rc = lib.fama_net_load(self.mem, blob, len(blob), self.arena, n)
        if rc != 0:
            raise ValueError(f"fama_net_load failed: {rc}")
        self.ptr = C.cast(self.mem, C.c_void_p)

    def set_alpha(self, alpha):
        self.lib.fh_net_set_alpha(self.ptr, alpha)

    def reset(self):
        self.lib.fama_net_reset(self.ptr)

    def forward(self, x, ids):
        self.lib.fama_net_forward(self.ptr, np.ascontiguousarray(x, np.float32).reshape(-1))
        out = []
        for i in ids:
            n = C.c_uint32()
            p = self.lib.fama_net_buffer(self.ptr, i, C.byref(n))
            out.append(np.ctypeslib.as_array(p, shape=(n.value,)).copy())
        return out


def decimate(lib, x2k):
    x = np.ascontiguousarray(np.asarray(x2k, np.float32).T)
    y = np.zeros((x.shape[0] // 2 + 1, N_CH), np.float32)
    k = lib.fh_decimate(x, x.shape[0], y)
    return y[:k].T


def preprocess(lib, x1k):
    x = np.ascontiguousarray(np.asarray(x1k, np.float32).T)
    bp = np.zeros_like(x); z = np.zeros_like(x)
    lib.fh_preprocess(x, x.shape[0], bp, z)
    return bp.T, z.T


def stft(lib, win):
    out = np.zeros((N_CH, N_FREQ, N_FRAMES), np.float32)
    lib.fh_stft(np.ascontiguousarray(win, np.float32), out)
    return out


def sampen(lib, x, m=2, r=0.2):
    x = np.ascontiguousarray(x, np.float32)
    return float(lib.fama_sampen(x, len(x), m, r))


def run_pipeline(lib, x2k, net: CNet = None, n_layers=2, refractory=300, lr=1e-3, steps=4):
    mem = C.create_string_buffer(lib.fh_sizeof_pipeline())
    p = C.cast(mem, C.c_void_p)
    lib.fh_pipeline_init(p, net.ptr if net else None, n_layers, refractory, lr, steps)
    x = np.ascontiguousarray(np.asarray(x2k, np.float32).T)
    max_res = x.shape[0] // 200 + 2
    probs = np.zeros((max_res, MAXC), np.float32)
    shat = np.zeros(max_res, np.float32); se = np.zeros(max_res, np.float32); bl = np.zeros(max_res, np.float32)
    gest = np.zeros(max_res, np.int32); tb = np.zeros(max_res, np.int32)
    k = lib.fh_pipeline_run(p, x, x.shape[0], max_res, probs, shat, se, bl, gest, tb)
    return dict(probs=probs[:k], shat=shat[:k], sampen=se[:k], baseline=bl[:k], gesture=gest[:k],
                tb_updated=tb[:k].astype(bool), n_updates=lib.fh_pipeline_tb_updates(p), _mem=mem)
