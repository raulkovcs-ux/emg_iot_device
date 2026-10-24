/* Per-sample pipeline: 2 kSPS frames in, one FAMA-Net result per 100 ms window out.
 * Mirrors host/famaband/pipeline_ref.py. */
#include <math.h>
#include <string.h>

#include "fama_dsp.h"

void fama_pipeline_init(fama_pipeline_t *p, fama_net_t *net, const fama_tierb_cfg_t *cfg)
{
    memset(p, 0, sizeof(*p));
    fama_decim_init(&p->decim);
    fama_pre_init(&p->pre);
    p->net = net;
    p->use_net = net != NULL;
    fama_tierb_init(&p->tb, cfg);
}

void fama_pipeline_session_start(fama_pipeline_t *p)
{
    fama_tierb_cfg_t cfg = p->tb.cfg;
    fama_decim_init(&p->decim);
    fama_pre_init(&p->pre);
    p->n_out = 0;
    p->ring_pos = p->bp_pos = 0;
    p->n_sampen = 0;
    p->sampen_last = 0.0f;
    p->sampen_baseline = 0.0f;
    p->windows = 0;
    if (p->net) fama_net_reset(p->net);   /* Tier A statistics + Tier B weights */
    fama_tierb_init(&p->tb, &cfg);
}

void fama_pipeline_session_end(fama_pipeline_t *p)
{
    if (p->net) fama_net_reset(p->net);   /* "restore the pre-trained weights" */
    fama_tierb_cfg_t cfg = p->tb.cfg;
    fama_tierb_init(&p->tb, &cfg);
}

static int cmp_float(const void *a, const void *b)
{
    float x = *(const float *)a, y = *(const float *)b;
    return (x > y) - (x < y);
}

static float median(float *v, int n)
{
    /* insertion sort, n <= 60 */
    for (int i = 1; i < n; i++) {
        float t = v[i];
        int j = i - 1;
        while (j >= 0 && cmp_float(&v[j], &t) > 0) { v[j + 1] = v[j]; j--; }
        v[j + 1] = t;
    }
    return n % 2 ? v[n / 2] : 0.5f * (v[n / 2 - 1] + v[n / 2]);
}

int fama_pipeline_push(fama_pipeline_t *p, const float v[FAMA_N_CH], fama_result_t *res)
{
    float y[FAMA_N_CH], bp[FAMA_N_CH], z[FAMA_N_CH];
    if (!fama_decim_push(&p->decim, v, y)) return 0;
    fama_pre_push(&p->pre, y, bp, z);
    for (int c = 0; c < FAMA_N_CH; c++) {
        p->ring[c][p->ring_pos] = z[c];
        p->bp_ring[c][p->bp_pos] = bp[c];
    }
    p->ring_pos = (p->ring_pos + 1) % FAMA_WIN;
    p->bp_pos = (p->bp_pos + 1) % FAMA_SAMPEN_N;
    p->n_out++;

    /* fatigue marker: SampEn once per second on the last N band-passed samples, channel mean */
    if (p->n_out % FAMA_SAMPEN_N == 0) {
        static float lin[FAMA_SAMPEN_N];
        float acc = 0.0f;
        for (int c = 0; c < FAMA_N_CH; c++) {
            for (int i = 0; i < FAMA_SAMPEN_N; i++) lin[i] = p->bp_ring[c][(p->bp_pos + i) % FAMA_SAMPEN_N];
            acc += fama_sampen(lin, FAMA_SAMPEN_N, 2, 0.2f);
        }
        p->sampen_last = acc / FAMA_N_CH;
        if (p->n_sampen < FAMA_BASELINE_S) {
            p->sampen_hist[p->n_sampen++] = p->sampen_last;
            if (p->n_sampen == FAMA_BASELINE_S) {
                float tmp[FAMA_BASELINE_S];
                memcpy(tmp, p->sampen_hist, sizeof(tmp));
                p->sampen_baseline = median(tmp, FAMA_BASELINE_S);
            }
        }
    }

    if (p->n_out < FAMA_WIN || (p->n_out - FAMA_WIN) % FAMA_HOP != 0) return 0;

    for (int c = 0; c < FAMA_N_CH; c++)
        for (int i = 0; i < FAMA_WIN; i++) p->win[c][i] = p->ring[c][(p->ring_pos + i) % FAMA_WIN];
    fama_stft((const float (*)[FAMA_WIN])p->win, (float (*)[FAMA_N_FREQ][FAMA_N_FRAMES])p->spec);

    memset(res, 0, sizeof(*res));
    res->window_index = p->windows;
    res->sampen = p->sampen_last;
    res->sampen_baseline = p->sampen_baseline;
    res->sampen_valid = p->n_sampen >= FAMA_BASELINE_S;
    res->gesture = -1;
    if (p->use_net) {
        fama_net_t *net = p->net;
        net->adapt_alpha = p->windows < FAMA_WARMUP_WINDOWS ? 0.10f : 0.01f;
        fama_net_forward(net, p->spec);
        uint32_t ng = 0, nf = 0;
        float *logits = fama_net_buffer(net, net->out_gesture, &ng);
        float *fat = fama_net_buffer(net, net->out_fatigue, &nf);
        if (ng > TB_MAX_CLASSES) ng = TB_MAX_CLASSES;
        fama_softmax(logits, res->probs, (int)ng);
        res->n_classes = (int)ng;
        int k = 0;
        for (int i = 1; i < (int)ng; i++) if (res->probs[i] > res->probs[k]) k = i;
        res->gesture = k;
        res->shat = fat[0];
        fama_tierb_record(&p->tb, net, res->probs, (int)ng, res->shat);
        res->tierb_updated = fama_tierb_step(&p->tb, net, res->sampen_valid, res->sampen, res->sampen_baseline);
    }
    p->windows++;
    return 1;
}
