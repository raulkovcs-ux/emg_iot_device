/* Decimator, pre-processing, STFT and SampEn. Mirrors host/famaband/dsp_ref.py. */
#include <math.h>
#include <string.h>

#include "fama_dsp.h"

/* ------------------------------------------------------------------ decimator */
void fama_decim_init(fama_decim_t *d) { memset(d, 0, sizeof(*d)); }

int fama_decim_push(fama_decim_t *d, const float in[FAMA_N_CH], float out[FAMA_N_CH])
{
    for (int c = 0; c < FAMA_N_CH; c++)
        d->hist[c][d->pos] = in[c];
    int emit = (d->count & 1u) == 1u; /* lfilter(...)[1::2] */
    if (emit) {
        for (int c = 0; c < FAMA_N_CH; c++) {
            float acc = 0.0f;
            int idx = d->pos;
            for (int k = 0; k < FAMA_DECIM_TAPS; k++) {
                acc += FAMA_DECIM_H[k] * d->hist[c][idx];
                idx = idx == 0 ? FAMA_DECIM_TAPS - 1 : idx - 1;
            }
            out[c] = acc;
        }
    }
    d->pos = (d->pos + 1) % FAMA_DECIM_TAPS;
    d->count++;
    return emit;
}

/* ------------------------------------------------------------- pre-processing */
void fama_pre_init(fama_pre_t *p) { memset(p, 0, sizeof(*p)); }

void fama_pre_push(fama_pre_t *p, const float in[FAMA_N_CH], float bp[FAMA_N_CH], float out[FAMA_N_CH])
{
    for (int c = 0; c < FAMA_N_CH; c++) {
        /* DC blocker: y = x - x1 + a*y1 */
        float y = in[c] - p->dc_x1[c] + FAMA_DC_POLE * p->dc_y1[c];
        p->dc_x1[c] = in[c];
        p->dc_y1[c] = y;
        /* SOS cascade, direct form II transposed (as scipy.signal.sosfilt) */
        float x = y;
        for (int s = 0; s < FAMA_N_SOS; s++) {
            const float *q = &FAMA_SOS[s * 6];
            float *z = p->z[c][s];
            float o = q[0] * x + z[0];
            z[0] = q[1] * x - q[4] * o + z[1];
            z[1] = q[2] * x - q[5] * o;
            x = o;
        }
        bp[c] = x;
        /* causal running z-score: normalise with the statistics before this sample */
        if (!p->started) {
            p->z_mean[c] = x;
            p->z_var[c] = 0.0;
        }
        double d = (double)x - p->z_mean[c];
        out[c] = (float)(d / sqrt(p->z_var[c] + FAMA_Z_EPS));
        p->z_mean[c] += FAMA_Z_ALPHA * d;
        p->z_var[c] = (1.0 - FAMA_Z_ALPHA) * (p->z_var[c] + FAMA_Z_ALPHA * d * d);
    }
    p->started = 1;
}

/* ------------------------------------------------------------------- STFT */
static void fft64(float re[FAMA_NFFT], float im[FAMA_NFFT])
{
    const int n = FAMA_NFFT;
    for (int i = 1, j = 0; i < n; i++) { /* bit reversal */
        int bit = n >> 1;
        for (; j & bit; bit >>= 1)
            j ^= bit;
        j ^= bit;
        if (i < j) {
            float t = re[i]; re[i] = re[j]; re[j] = t;
            t = im[i]; im[i] = im[j]; im[j] = t;
        }
    }
    for (int len = 2; len <= n; len <<= 1) {
        double ang = -2.0 * M_PI / len;
        for (int i = 0; i < n; i += len) {
            for (int k = 0; k < len / 2; k++) {
                float wr = (float)cos(ang * k), wi = (float)sin(ang * k);
                int a = i + k, b = i + k + len / 2;
                float xr = re[b] * wr - im[b] * wi;
                float xi = re[b] * wi + im[b] * wr;
                re[b] = re[a] - xr; im[b] = im[a] - xi;
                re[a] += xr; im[a] += xi;
            }
        }
    }
}

void fama_stft(const float win[FAMA_N_CH][FAMA_WIN], float out[FAMA_N_CH][FAMA_N_FREQ][FAMA_N_FRAMES])
{
    float re[FAMA_NFFT], im[FAMA_NFFT];
    for (int c = 0; c < FAMA_N_CH; c++) {
        for (int t = 0; t < FAMA_N_FRAMES; t++) {
            const float *seg = &win[c][t * FAMA_STFT_HOP];
            for (int i = 0; i < FAMA_NFFT; i++) {
                re[i] = seg[i] * FAMA_HANN[i];
                im[i] = 0.0f;
            }
            fft64(re, im);
            for (int f = 0; f < FAMA_N_FREQ; f++)
                out[c][f][t] = log1pf(sqrtf(re[f] * re[f] + im[f] * im[f]));
        }
    }
}

/* ----------------------------------------------------------------- SampEn */
float fama_sampen(const float *x, int n, int m, float r_factor)
{
    double s = 0.0, s2 = 0.0;
    for (int i = 0; i < n; i++) {
        s += (double)x[i];
        s2 += (double)x[i] * (double)x[i];
    }
    double mean = s / n;
    double var = s2 / n - mean * mean;
    float r = (float)((double)r_factor * sqrt(var > 0 ? var : 0));
    int nt = n - m;
    long a = 0, b = 0;
    for (int i = 0; i < nt - 1; i++) {
        for (int j = i + 1; j < nt; j++) {
            int k = 0;
            for (; k < m; k++)
                if (fabsf(x[i + k] - x[j + k]) > r)
                    break;
            if (k < m)
                continue;
            b++;
            if (fabsf(x[i + m] - x[j + m]) <= r)
                a++;
        }
    }
    if (a == 0 || b == 0)
        return 10.0f;
    return (float)(-log((double)a / (double)b));
}
