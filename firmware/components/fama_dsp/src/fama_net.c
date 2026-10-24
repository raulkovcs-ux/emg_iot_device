/* FAMA-Net graph runtime (float32). Mirrors host/famaband/net_ref.py. */
#include <math.h>
#include <string.h>

#include "fama_dsp.h"

#define BLOB_MAGIC 0x414D4146u /* "FAMA" little endian */
#define BLOB_VERSION 1u

typedef struct {
    const uint8_t *p, *end;
    int err;
} rd_t;

static uint32_t rd_u32(rd_t *r)
{
    if (r->p + 4 > r->end) { r->err = 1; return 0; }
    uint32_t v = (uint32_t)r->p[0] | (uint32_t)r->p[1] << 8 | (uint32_t)r->p[2] << 16 | (uint32_t)r->p[3] << 24;
    r->p += 4;
    return v;
}

static float rd_f32(rd_t *r)
{
    uint32_t u = rd_u32(r);
    float f;
    memcpy(&f, &u, 4);
    return f;
}

static float *arena_take(fama_net_t *n, size_t count)
{
    if (!n->arena) { n->arena_used += count; return NULL; } /* sizing pass */
    if (n->arena_used + count > n->arena_floats) return NULL;
    float *p = n->arena + n->arena_used;
    n->arena_used += count;
    return p;
}

static int rd_floats(rd_t *r, fama_net_t *n, float **dst, size_t count)
{
    float *p = arena_take(n, count);
    if (n->arena && !p) return -3;
    for (size_t i = 0; i < count; i++) {
        float v = rd_f32(r);
        if (p) p[i] = v;
    }
    *dst = p;
    return r->err ? -2 : 0;
}

static int copy_of(fama_net_t *n, float **dst, const float *src, size_t count)
{
    float *p = arena_take(n, count);
    if (n->arena && !p) return -3;
    if (p) memcpy(p, src, count * sizeof(float));
    *dst = p;
    return 0;
}

static int parse(fama_net_t *net, const uint8_t *blob, size_t len)
{
    rd_t r = { blob, blob + len, 0 };
    if (rd_u32(&r) != BLOB_MAGIC) return -1;
    if (rd_u32(&r) != BLOB_VERSION) return -1;
    net->n_bufs = rd_u32(&r);
    net->n_nodes = rd_u32(&r);
    net->out_gesture = rd_u32(&r);
    net->out_fatigue = rd_u32(&r);
    net->tb_dense1 = (int32_t)rd_u32(&r);
    net->tb_dense2 = (int32_t)rd_u32(&r);
    if (r.err || net->n_bufs > FN_MAX_BUFS || net->n_nodes > FN_MAX_NODES) return -2;
    for (uint32_t i = 0; i < net->n_bufs; i++) {
        fn_tensor_t *t = &net->buf[i];
        t->c = rd_u32(&r); t->h = rd_u32(&r); t->w = rd_u32(&r);
        t->data = arena_take(net, (size_t)t->c * t->h * t->w);
        if (net->arena && !t->data) return -3;
    }
    for (uint32_t i = 0; i < net->n_nodes; i++) {
        fn_node_t *nd = &net->node[i];
        memset(nd, 0, sizeof(*nd));
        nd->type = rd_u32(&r);
        nd->n_in = rd_u32(&r);
        for (int k = 0; k < FN_MAX_IN; k++) nd->in[k] = rd_u32(&r);
        nd->out = rd_u32(&r);
        if (r.err || nd->n_in > FN_MAX_IN || nd->out >= net->n_bufs) return -2;
        for (uint32_t k = 0; k < nd->n_in; k++)
            if (nd->in[k] >= net->n_bufs) return -2;
        int e = 0;
        switch (nd->type) {
        case FN_CONV2D: {
            nd->out_ch = rd_u32(&r); nd->in_ch = rd_u32(&r); nd->kh = rd_u32(&r); nd->kw = rd_u32(&r);
            nd->sh = rd_u32(&r); nd->sw = rd_u32(&r); nd->ph = rd_u32(&r); nd->pw = rd_u32(&r); nd->groups = rd_u32(&r);
            if (!nd->groups || nd->in_ch % nd->groups || nd->out_ch % nd->groups) return -2;
            size_t nw = (size_t)nd->out_ch * (nd->in_ch / nd->groups) * nd->kh * nd->kw;
            e = rd_floats(&r, net, &nd->w, nw);
            if (!e) e = rd_floats(&r, net, &nd->b, nd->out_ch);
            break;
        }
        case FN_BN:
            nd->ch = rd_u32(&r);
            nd->eps = rd_f32(&r);
            e = rd_floats(&r, net, &nd->w, nd->ch);
            if (!e) e = rd_floats(&r, net, &nd->b, nd->ch);
            if (!e) e = rd_floats(&r, net, &nd->mean0, nd->ch);
            if (!e) e = rd_floats(&r, net, &nd->var0, nd->ch);
            if (!e) e = copy_of(net, &nd->mean, nd->mean0, nd->ch);
            if (!e) e = copy_of(net, &nd->var, nd->var0, nd->ch);
            break;
        case FN_DENSE:
            nd->n_inputs = rd_u32(&r);
            nd->n_outputs = rd_u32(&r);
            e = rd_floats(&r, net, &nd->w, (size_t)nd->n_inputs * nd->n_outputs);
            if (!e) e = rd_floats(&r, net, &nd->b, nd->n_outputs);
            if (!e) e = copy_of(net, &nd->w0, nd->w, (size_t)nd->n_inputs * nd->n_outputs);
            if (!e) e = copy_of(net, &nd->b0, nd->b, nd->n_outputs);
            break;
        case FN_RELU:
        case FN_CONCAT:
        case FN_GAP:
            break;
        default:
            return -2;
        }
        if (e) return e;
        if (r.err) return -2;
    }
    return 0;
}

size_t fama_net_arena_size(const uint8_t *blob, size_t len)
{
    static fama_net_t tmp; /* large struct: keep off the stack */
    memset(&tmp, 0, sizeof(tmp));
    if (parse(&tmp, blob, len) != 0) return 0;
    return tmp.arena_used;
}

int fama_net_load(fama_net_t *net, const uint8_t *blob, size_t len, float *arena, size_t arena_floats)
{
    memset(net, 0, sizeof(*net));
    net->arena = arena;
    net->arena_floats = arena_floats;
    int e = parse(net, blob, len);
    if (e) return e;
    net->adapt_alpha = 0.0f;
    return 0;
}

float *fama_net_buffer(fama_net_t *net, uint32_t id, uint32_t *size)
{
    fn_tensor_t *t = &net->buf[id];
    if (size) *size = t->c * t->h * t->w;
    return t->data;
}

void fama_net_reset(fama_net_t *net)
{
    for (uint32_t i = 0; i < net->n_nodes; i++) {
        fn_node_t *nd = &net->node[i];
        if (nd->type == FN_BN) {
            memcpy(nd->mean, nd->mean0, nd->ch * sizeof(float));
            memcpy(nd->var, nd->var0, nd->ch * sizeof(float));
        } else if (nd->type == FN_DENSE) {
            memcpy(nd->w, nd->w0, (size_t)nd->n_inputs * nd->n_outputs * sizeof(float));
            memcpy(nd->b, nd->b0, nd->n_outputs * sizeof(float));
        }
    }
}

static void conv2d(const fn_node_t *nd, const fn_tensor_t *x, fn_tensor_t *y)
{
    const uint32_t icg = nd->in_ch / nd->groups, ocg = nd->out_ch / nd->groups;
    for (uint32_t o = 0; o < nd->out_ch; o++) {
        const uint32_t g = o / ocg;
        const float *wo = nd->w + (size_t)o * icg * nd->kh * nd->kw;
        float *yo = y->data + (size_t)o * y->h * y->w;
        for (uint32_t oy = 0; oy < y->h; oy++) {
            for (uint32_t ox = 0; ox < y->w; ox++) {
                float acc = nd->b[o];
                for (uint32_t ci = 0; ci < icg; ci++) {
                    const float *xc = x->data + (size_t)(g * icg + ci) * x->h * x->w;
                    const float *wc = wo + (size_t)ci * nd->kh * nd->kw;
                    for (uint32_t ky = 0; ky < nd->kh; ky++) {
                        int iy = (int)(oy * nd->sh + ky) - (int)nd->ph;
                        if (iy < 0 || iy >= (int)x->h) continue;
                        for (uint32_t kx = 0; kx < nd->kw; kx++) {
                            int ix = (int)(ox * nd->sw + kx) - (int)nd->pw;
                            if (ix < 0 || ix >= (int)x->w) continue;
                            acc += wc[ky * nd->kw + kx] * xc[iy * x->w + ix];
                        }
                    }
                }
                yo[oy * y->w + ox] = acc;
            }
        }
    }
}

static void batchnorm(fn_node_t *nd, const fn_tensor_t *x, fn_tensor_t *y, float alpha)
{
    const uint32_t hw = x->h * x->w;
    for (uint32_t c = 0; c < nd->ch; c++) {
        const float *xc = x->data + (size_t)c * hw;
        float *yc = y->data + (size_t)c * hw;
        if (alpha > 0.0f) { /* Tier A: causal AdaBN, statistics of the current window only */
            double s = 0, s2 = 0;
            for (uint32_t i = 0; i < hw; i++) { s += (double)xc[i]; s2 += (double)xc[i] * (double)xc[i]; }
            double mu = s / hw, var = s2 / hw - mu * mu;
            if (var < 0) var = 0;
            nd->mean[c] = (1.0f - alpha) * nd->mean[c] + alpha * (float)mu;
            nd->var[c] = (1.0f - alpha) * nd->var[c] + alpha * (float)var;
        }
        const float sc = nd->w[c] / sqrtf(nd->var[c] + nd->eps);
        const float sh = nd->b[c] - nd->mean[c] * sc;
        for (uint32_t i = 0; i < hw; i++) yc[i] = xc[i] * sc + sh;
    }
}

void fama_net_forward(fama_net_t *net, const float *input)
{
    fn_tensor_t *in = &net->buf[0];
    memcpy(in->data, input, (size_t)in->c * in->h * in->w * sizeof(float));
    for (uint32_t i = 0; i < net->n_nodes; i++) {
        fn_node_t *nd = &net->node[i];
        fn_tensor_t *x = &net->buf[nd->in[0]], *y = &net->buf[nd->out];
        const uint32_t n = x->c * x->h * x->w;
        switch (nd->type) {
        case FN_CONV2D: conv2d(nd, x, y); break;
        case FN_BN: batchnorm(nd, x, y, net->adapt_alpha); break;
        case FN_RELU:
            for (uint32_t k = 0; k < n; k++) y->data[k] = x->data[k] > 0.0f ? x->data[k] : 0.0f;
            break;
        case FN_CONCAT: {
            float *dst = y->data;
            for (uint32_t k = 0; k < nd->n_in; k++) {
                fn_tensor_t *s = &net->buf[nd->in[k]];
                size_t m = (size_t)s->c * s->h * s->w;
                memmove(dst, s->data, m * sizeof(float));
                dst += m;
            }
            break;
        }
        case FN_GAP: {
            const uint32_t hw = x->h * x->w;
            for (uint32_t c = 0; c < x->c; c++) {
                double s = 0;
                for (uint32_t k = 0; k < hw; k++) s += (double)x->data[(size_t)c * hw + k];
                y->data[c] = (float)(s / hw);
            }
            break;
        }
        case FN_DENSE:
            for (uint32_t o = 0; o < nd->n_outputs; o++) {
                const float *wr = nd->w + (size_t)o * nd->n_inputs;
                float acc = nd->b[o];
                for (uint32_t k = 0; k < nd->n_inputs; k++) acc += wr[k] * x->data[k];
                y->data[o] = acc;
            }
            break;
        }
    }
}
