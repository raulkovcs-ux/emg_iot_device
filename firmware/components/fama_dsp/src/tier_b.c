/* Tier B: SampEn-triggered fine-tuning of the last gesture-head layers (model fig. 2).
 * Mirrors host/famaband/net_ref.py::TierB. */
#include <math.h>
#include <string.h>

#include "fama_dsp.h"

void fama_softmax(const float *logits, float *probs, int n)
{
    float mx = logits[0];
    for (int i = 1; i < n; i++) if (logits[i] > mx) mx = logits[i];
    float s = 0.0f;
    for (int i = 0; i < n; i++) { probs[i] = expf(logits[i] - mx); s += probs[i]; }
    for (int i = 0; i < n; i++) probs[i] /= s;
}

void fama_tierb_default_cfg(fama_tierb_cfg_t *cfg)
{
    cfg->sampen_ratio = 0.8f;
    cfg->shat_threshold = 0.15f;
    cfg->conf_threshold = 0.9f;
    cfg->min_windows = 8;
    cfg->steps = 4;
    cfg->lr = 1e-3f;
    cfg->n_layers = 2;
    cfg->refractory_windows = 300;
}

void fama_tierb_init(fama_tierb_t *tb, const fama_tierb_cfg_t *cfg)
{
    memset(tb, 0, sizeof(*tb));
    if (cfg) tb->cfg = *cfg; else fama_tierb_default_cfg(&tb->cfg);
}

/* the stored feature is the input of the first trainable layer */
static const fn_node_t *first_trainable(const fama_tierb_t *tb, const fama_net_t *net)
{
    if (net->tb_dense2 < 0) return NULL;
    if (tb->cfg.n_layers >= 2 && net->tb_dense1 >= 0) return &net->node[net->tb_dense1];
    return &net->node[net->tb_dense2];
}

void fama_tierb_record(fama_tierb_t *tb, const fama_net_t *net, const float *probs, int n_classes, float shat)
{
    const fn_node_t *nd = first_trainable(tb, net);
    if (!nd || nd->n_inputs > TB_MAX_FEAT || n_classes > TB_MAX_CLASSES) return;
    const fn_tensor_t *x = &net->buf[nd->in[0]];
    memcpy(tb->feat[tb->head], x->data, nd->n_inputs * sizeof(float));
    memcpy(tb->prob[tb->head], probs, n_classes * sizeof(float));
    tb->shat[tb->head] = shat;
    tb->head = (tb->head + 1) % TB_HIST;
    if (tb->n < TB_HIST) tb->n++;
}

static int argmax(const float *p, int n)
{
    int k = 0;
    for (int i = 1; i < n; i++) if (p[i] > p[k]) k = i;
    return k;
}

int fama_tierb_step(fama_tierb_t *tb, fama_net_t *net, int sampen_valid, float sampen, float sampen_baseline)
{
    if (tb->cooldown > 0) tb->cooldown--;
    if (net->tb_dense2 < 0) return 0;
    /* check 1: SampEn below ratio * baseline */
    if (!sampen_valid || !(sampen < tb->cfg.sampen_ratio * sampen_baseline)) return 0;
    /* check 2: fatigue head above threshold in the recent windows */
    if (tb->n < TB_HIST) return 0;
    float ms = 0.0f;
    for (int i = 0; i < tb->n; i++) ms += tb->shat[i];
    if (!(ms / (float)tb->n > tb->cfg.shat_threshold)) return 0;
    if (tb->cooldown > 0) return 0;

    fn_node_t *d2 = &net->node[net->tb_dense2];
    fn_node_t *d1 = (tb->cfg.n_layers >= 2 && net->tb_dense1 >= 0) ? &net->node[net->tb_dense1] : NULL;
    const int nc = (int)d2->n_outputs;
    const int nh = (int)d2->n_inputs;
    const int nx = d1 ? (int)d1->n_inputs : nh;
    if (nc > TB_MAX_CLASSES || nh > TB_MAX_HID || nx > TB_MAX_FEAT) return 0;

    /* pseudo-label the recent windows, keep the confident ones */
    int sel[TB_HIST], lab[TB_HIST], k = 0;
    for (int i = 0; i < tb->n; i++) {
        int a = argmax(tb->prob[i], nc);
        if (tb->prob[i][a] > tb->cfg.conf_threshold) { sel[k] = i; lab[k] = a; k++; }
    }
    if (k < tb->cfg.min_windows) return 0;

    float *g2w = tb->g2w, *g1w = tb->g1w;
    float g2b[TB_MAX_CLASSES], g1b[TB_MAX_FEAT];
    float h[TB_MAX_FEAT], z1[TB_MAX_FEAT], dz2[TB_MAX_CLASSES], dh[TB_MAX_FEAT], logit[TB_MAX_CLASSES], p[TB_MAX_CLASSES];
    for (int step = 0; step < tb->cfg.steps; step++) {
        memset(g2w, 0, sizeof(float) * nc * nh); memset(g2b, 0, sizeof(float) * nc);
        if (d1) { memset(g1w, 0, sizeof(float) * nh * nx); memset(g1b, 0, sizeof(float) * nh); }
        for (int s = 0; s < k; s++) {
            const float *x = tb->feat[sel[s]];
            if (d1) {
                for (int j = 0; j < nh; j++) {
                    float a = d1->b[j];
                    for (int i = 0; i < nx; i++) a += d1->w[j * nx + i] * x[i];
                    z1[j] = a; h[j] = a > 0 ? a : 0;
                }
            } else {
                memcpy(h, x, sizeof(float) * nh);
            }
            for (int c = 0; c < nc; c++) {
                float a = d2->b[c];
                for (int j = 0; j < nh; j++) a += d2->w[c * nh + j] * h[j];
                logit[c] = a;
            }
            fama_softmax(logit, p, nc);
            for (int c = 0; c < nc; c++) dz2[c] = p[c] - (c == lab[s] ? 1.0f : 0.0f);
            for (int c = 0; c < nc; c++) {
                g2b[c] += dz2[c];
                for (int j = 0; j < nh; j++) g2w[c * nh + j] += dz2[c] * h[j];
            }
            if (d1) {
                for (int j = 0; j < nh; j++) {
                    float a = 0;
                    for (int c = 0; c < nc; c++) a += d2->w[c * nh + j] * dz2[c];
                    dh[j] = z1[j] > 0 ? a : 0;
                }
                for (int j = 0; j < nh; j++) {
                    g1b[j] += dh[j];
                    for (int i = 0; i < nx; i++) g1w[j * nx + i] += dh[j] * x[i];
                }
            }
        }
        const float lr = tb->cfg.lr / (float)k;
        for (int i = 0; i < nc * nh; i++) d2->w[i] -= lr * g2w[i];
        for (int c = 0; c < nc; c++) d2->b[c] -= lr * g2b[c];
        if (d1) {
            for (int i = 0; i < nh * nx; i++) d1->w[i] -= lr * g1w[i];
            for (int j = 0; j < nh; j++) d1->b[j] -= lr * g1b[j];
        }
    }
    tb->cooldown = tb->cfg.refractory_windows;
    tb->n_updates++;
    tb->last_used = k;
    return 1;
}
