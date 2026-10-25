/* Gesture probabilities -> two proportional terminal-device outputs (see fama_dsp.h). */
#include <string.h>

#include "fama_dsp.h"

void fama_td_init(fama_td_t *td, int class_out0, int class_out1)
{
    memset(td, 0, sizeof(*td));
    td->cls[0] = class_out0;
    td->cls[1] = class_out1;
    td->on_threshold = 0.7f;
    td->off_threshold = 0.5f;
    td->alpha = 0.3f;
}

void fama_td_update(fama_td_t *td, const float *probs, int n_classes, float out[2])
{
    float p[2];
    for (int k = 0; k < 2; k++) {
        p[k] = (td->cls[k] >= 0 && td->cls[k] < n_classes) ? probs[td->cls[k]] : 0.0f;
        if (!td->active[k] && p[k] > td->on_threshold) td->active[k] = 1;
        else if (td->active[k] && p[k] < td->off_threshold) td->active[k] = 0;
    }
    if (td->active[0] && td->active[1]) td->active[p[0] >= p[1] ? 1 : 0] = 0; /* never both */
    for (int k = 0; k < 2; k++) {
        float target = 0.0f;
        if (td->active[k]) {
            target = (p[k] - td->off_threshold) / (1.0f - td->off_threshold);
            if (target < 0.0f) target = 0.0f;
            if (target > 1.0f) target = 1.0f;
        }
        td->level[k] += td->alpha * (target - td->level[k]);
        if (!td->active[k] && td->level[k] < 1e-3f) td->level[k] = 0.0f;
        out[k] = td->level[k];
    }
}

void fama_td_stale(fama_td_t *td, float out[2])
{
    td->active[0] = td->active[1] = 0;
    td->level[0] = td->level[1] = 0.0f;
    out[0] = out[1] = 0.0f;
}
