/*
 * FAMA-Band on-device signal chain and FAMA-Net runtime.
 *
 * Portable C99, no dynamic allocation in the per-sample path, no ESP-IDF
 * dependency: the same sources build for the ESP32-S3 and for the host
 * (host/tests drive them through ctypes against the NumPy reference in
 * host/famaband/dsp_ref.py and host/famaband/net_ref.py).
 */
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "fama_coeffs.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------ decimator
 * 2000 SPS -> 1000 SPS, 63-tap equaliser/anti-alias FIR, polyphase.        */
typedef struct {
    float hist[FAMA_N_CH][FAMA_DECIM_TAPS];
    int pos;
    uint32_t count;
} fama_decim_t;

void fama_decim_init(fama_decim_t *d);
/* Returns 1 and writes out[] on every second input sample (odd input index). */
int fama_decim_push(fama_decim_t *d, const float in[FAMA_N_CH], float out[FAMA_N_CH]);

/* ------------------------------------------------------------- pre-processing
 * DC blocker -> band-pass 20-450 Hz + notches (SOS, DF-II transposed)
 * -> causal running z-score. `bp` receives the band-passed signal (for SampEn). */
typedef struct {
    float dc_x1[FAMA_N_CH], dc_y1[FAMA_N_CH];
    float z[FAMA_N_CH][FAMA_N_SOS][2];
    double z_mean[FAMA_N_CH], z_var[FAMA_N_CH];
    int started;
} fama_pre_t;

void fama_pre_init(fama_pre_t *p);
void fama_pre_push(fama_pre_t *p, const float in[FAMA_N_CH], float bp[FAMA_N_CH], float out[FAMA_N_CH]);

/* ------------------------------------------------------------------- STFT
 * win[ch][WIN] -> out[ch][N_FREQ][N_FRAMES] = log1p(|rfft(hann * frame)|)  */
void fama_stft(const float win[FAMA_N_CH][FAMA_WIN], float out[FAMA_N_CH][FAMA_N_FREQ][FAMA_N_FRAMES]);

/* ----------------------------------------------------------------- SampEn
 * Richman & Moorman, Chebyshev distance <= r, r = r_factor * std (population),
 * self-matches excluded, N - m templates. Returns 10.0 when undefined.      */
float fama_sampen(const float *x, int n, int m, float r_factor);

/* ------------------------------------------------------------- FAMA-Net
 * A tiny graph runtime: CONV2D (grouped), BN (optionally adaptive = Tier A),
 * RELU, CONCAT (channel axis), GAP, DENSE. Weights come from a blob written
 * by host/famaband/export.py (format documented in docs/05_firmware.md).    */
enum { FN_CONV2D = 1, FN_BN = 2, FN_RELU = 3, FN_CONCAT = 4, FN_GAP = 5, FN_DENSE = 6 };

#define FN_MAX_IN 3
#define FN_MAX_NODES 48
#define FN_MAX_BUFS 48

typedef struct {
    uint32_t c, h, w;
    float *data;
} fn_tensor_t;

typedef struct {
    uint32_t type, n_in, in[FN_MAX_IN], out;
    /* conv */
    uint32_t out_ch, in_ch, kh, kw, sh, sw, ph, pw, groups;
    /* dense */
    uint32_t n_inputs, n_outputs;
    /* bn */
    uint32_t ch;
    float eps;
    float *w, *b;                 /* conv/dense weights and bias, BN gamma/beta */
    float *mean, *var;            /* BN running statistics (adapted by Tier A) */
    float *mean0, *var0;          /* BN pre-trained statistics (session reset) */
    float *w0, *b0;               /* dense pre-trained weights (Tier B reset) */
} fn_node_t;

typedef struct {
    uint32_t n_bufs, n_nodes;
    fn_tensor_t buf[FN_MAX_BUFS];
    fn_node_t node[FN_MAX_NODES];
    uint32_t out_gesture, out_fatigue;  /* buffer ids */
    int32_t tb_dense1, tb_dense2;       /* node ids of the last two gesture-head dense layers (-1: none) */
    float adapt_alpha;                  /* Tier A EMA rate, 0 disables adaptation */
    float *arena;
    size_t arena_floats, arena_used;
} fama_net_t;

/* Parse a weight blob into `net`, using `arena` (floats) for all tensors.
 * Returns 0 on success, negative error code otherwise. */
int fama_net_load(fama_net_t *net, const uint8_t *blob, size_t len, float *arena, size_t arena_floats);
/* Arena size (floats) a blob needs. */
size_t fama_net_arena_size(const uint8_t *blob, size_t len);
/* Run the graph on input[] (size of buffer 0). Tier A adapts BN statistics when adapt_alpha > 0. */
void fama_net_forward(fama_net_t *net, const float *input);
/* Restore pre-trained BN statistics (Tier A) and dense weights (Tier B). */
void fama_net_reset(fama_net_t *net);
float *fama_net_buffer(fama_net_t *net, uint32_t id, uint32_t *size);

/* ------------------------------------------------------------- Tier B
 * Two fatigue checks (SampEn < ratio*baseline, mean s_hat over the recent
 * windows > threshold), then pseudo-label the recent windows (softmax > conf)
 * and take `steps` SGD steps on the last 1-2 gesture-head dense layers.      */
#define TB_HIST 24
#define TB_MAX_FEAT 256
#define TB_MAX_CLASSES 16
#define TB_MAX_HID 128   /* width of the hidden gesture-head layer (dense1 outputs) */

typedef struct {
    float sampen_ratio;     /* 0.8   */
    float shat_threshold;   /* 0.15  */
    float conf_threshold;   /* 0.9   */
    int min_windows;        /* 8     */
    int steps;              /* 4 (3-5) */
    float lr;               /* 1e-3  */
    int n_layers;           /* 1 or 2 */
    int refractory_windows; /* 300 (30 s at 100 ms hop) */
} fama_tierb_cfg_t;

typedef struct {
    fama_tierb_cfg_t cfg;
    float feat[TB_HIST][TB_MAX_FEAT];
    float prob[TB_HIST][TB_MAX_CLASSES];
    float shat[TB_HIST];
    int n, head;
    int cooldown;
    uint32_t n_updates;
    int last_used;          /* windows used by the last update */
    /* SGD workspace (kept here, not on the stack or in .bss: the firmware puts this struct in PSRAM) */
    float g1w[TB_MAX_HID * TB_MAX_FEAT];
    float g2w[TB_MAX_CLASSES * TB_MAX_HID];
} fama_tierb_t;

void fama_tierb_default_cfg(fama_tierb_cfg_t *cfg);
void fama_tierb_init(fama_tierb_t *tb, const fama_tierb_cfg_t *cfg);
/* Record the current window (after fama_net_forward). */
void fama_tierb_record(fama_tierb_t *tb, const fama_net_t *net, const float *probs, int n_classes, float shat);
/* Decide and, if both checks pass, update the head. Returns 1 if weights changed. */
int fama_tierb_step(fama_tierb_t *tb, fama_net_t *net, int sampen_valid, float sampen, float sampen_baseline);

void fama_softmax(const float *logits, float *probs, int n);

/* ------------------------------------------------------------- pipeline */
#define FAMA_SAMPEN_N 1000
#define FAMA_BASELINE_S 60
#define FAMA_WARMUP_WINDOWS 300   /* 30 s at 100 ms hop: alpha = 0.10, then 0.01 */

typedef struct {
    uint32_t window_index;
    int gesture;
    float probs[TB_MAX_CLASSES];
    int n_classes;
    float shat;
    float sampen, sampen_baseline;
    int sampen_valid;
    int tierb_updated;
} fama_result_t;

typedef struct {
    fama_decim_t decim;
    fama_pre_t pre;
    float ring[FAMA_N_CH][FAMA_WIN];          /* preprocessed, for windows */
    float bp_ring[FAMA_N_CH][FAMA_SAMPEN_N];  /* band-passed, for SampEn */
    uint32_t n_out;                           /* 1 kHz samples produced in this session */
    int ring_pos, bp_pos;
    float win[FAMA_N_CH][FAMA_WIN];
    float spec[FAMA_N_CH * FAMA_N_FREQ * FAMA_N_FRAMES];
    float sampen_hist[FAMA_BASELINE_S];
    int n_sampen;
    float sampen_last, sampen_baseline;
    fama_net_t *net;
    fama_tierb_t tb;
    int use_net;
    uint32_t windows;
} fama_pipeline_t;

void fama_pipeline_init(fama_pipeline_t *p, fama_net_t *net, const fama_tierb_cfg_t *cfg);
/* Session boundaries: resets filters' adaptive state, SampEn baseline, Tier A/B. */
void fama_pipeline_session_start(fama_pipeline_t *p);
void fama_pipeline_session_end(fama_pipeline_t *p);
/* Push one 2 kSPS frame in volts. Returns 1 when a new window result is ready. */
int fama_pipeline_push(fama_pipeline_t *p, const float v[FAMA_N_CH], fama_result_t *res);

#ifdef __cplusplus
}
#endif
