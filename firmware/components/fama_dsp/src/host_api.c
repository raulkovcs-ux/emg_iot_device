/* Host-only helpers so Python (ctypes) can drive the C core on whole arrays.
 * Not compiled into the firmware. Arrays are row-major [n][FAMA_N_CH] float32. */
#include <stdlib.h>
#include <string.h>

#include "fama_dsp.h"

size_t fh_sizeof_net(void) { return sizeof(fama_net_t); }
size_t fh_sizeof_pipeline(void) { return sizeof(fama_pipeline_t); }

int fh_decimate(const float *x, int n, float *y)
{
    fama_decim_t d;
    fama_decim_init(&d);
    int k = 0;
    for (int i = 0; i < n; i++)
        if (fama_decim_push(&d, &x[i * FAMA_N_CH], &y[k * FAMA_N_CH])) k++;
    return k;
}

void fh_preprocess(const float *x, int n, float *bp, float *z)
{
    fama_pre_t p;
    fama_pre_init(&p);
    for (int i = 0; i < n; i++)
        fama_pre_push(&p, &x[i * FAMA_N_CH], &bp[i * FAMA_N_CH], &z[i * FAMA_N_CH]);
}

void fh_stft(const float *win, float *out)
{
    fama_stft((const float (*)[FAMA_WIN])win, (float (*)[FAMA_N_FREQ][FAMA_N_FRAMES])out);
}

void fh_pipeline_init(void *p, void *net, int n_layers, int refractory, float lr, int steps)
{
    fama_tierb_cfg_t cfg;
    fama_tierb_default_cfg(&cfg);
    if (n_layers > 0) cfg.n_layers = n_layers;
    if (refractory >= 0) cfg.refractory_windows = refractory;
    if (lr > 0) cfg.lr = lr;
    if (steps > 0) cfg.steps = steps;
    fama_pipeline_init((fama_pipeline_t *)p, (fama_net_t *)net, &cfg);
    fama_pipeline_session_start((fama_pipeline_t *)p);
}

/* Push n frames at 2 kSPS; write one row per window result. Returns the number of results. */
int fh_pipeline_run(void *pp, const float *x, int n, int max_res, float *probs /*[max][16]*/,
                    float *shat, float *sampen, float *baseline, int *gesture, int *tb_updated)
{
    fama_pipeline_t *p = (fama_pipeline_t *)pp;
    fama_result_t r;
    int k = 0;
    for (int i = 0; i < n && k < max_res; i++) {
        if (fama_pipeline_push(p, &x[i * FAMA_N_CH], &r)) {
            memcpy(&probs[k * TB_MAX_CLASSES], r.probs, sizeof(r.probs));
            shat[k] = r.shat;
            sampen[k] = r.sampen;
            baseline[k] = r.sampen_valid ? r.sampen_baseline : -1.0f;
            gesture[k] = r.gesture;
            tb_updated[k] = r.tierb_updated;
            k++;
        }
    }
    return k;
}

void fh_pipeline_session_end(void *p) { fama_pipeline_session_end((fama_pipeline_t *)p); }
uint32_t fh_pipeline_tb_updates(void *p) { return ((fama_pipeline_t *)p)->tb.n_updates; }
void fh_net_set_alpha(void *net, float a) { ((fama_net_t *)net)->adapt_alpha = a; }
