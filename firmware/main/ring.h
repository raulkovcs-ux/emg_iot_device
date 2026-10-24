/* Single-producer / single-consumer ring of fixed-size records (lock-free). */
#pragma once
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

typedef struct {
    uint8_t *buf;
    uint32_t rec, cap;          /* record size, capacity in records (power of two) */
    atomic_uint head, tail;     /* head: next write, tail: next read */
    atomic_uint dropped;
} ring_t;

static inline void ring_init(ring_t *r, void *mem, uint32_t rec, uint32_t cap)
{
    r->buf = (uint8_t *)mem; r->rec = rec; r->cap = cap;
    atomic_store(&r->head, 0); atomic_store(&r->tail, 0); atomic_store(&r->dropped, 0);
}

static inline bool ring_push(ring_t *r, const void *item)
{
    uint32_t h = atomic_load_explicit(&r->head, memory_order_relaxed);
    uint32_t t = atomic_load_explicit(&r->tail, memory_order_acquire);
    if (h - t >= r->cap) { atomic_fetch_add(&r->dropped, 1); return false; }
    memcpy(r->buf + (h & (r->cap - 1)) * r->rec, item, r->rec);
    atomic_store_explicit(&r->head, h + 1, memory_order_release);
    return true;
}

static inline bool ring_pop(ring_t *r, void *item)
{
    uint32_t t = atomic_load_explicit(&r->tail, memory_order_relaxed);
    uint32_t h = atomic_load_explicit(&r->head, memory_order_acquire);
    if (t == h) return false;
    memcpy(item, r->buf + (t & (r->cap - 1)) * r->rec, r->rec);
    atomic_store_explicit(&r->tail, t + 1, memory_order_release);
    return true;
}

static inline uint32_t ring_count(ring_t *r)
{
    return atomic_load(&r->head) - atomic_load(&r->tail);
}
