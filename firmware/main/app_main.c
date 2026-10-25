/*
 * FAMA-Band firmware (ESP32-S3).
 *
 * Core 1:  acq_task  (DRDY @ 2 kHz -> rings)          priority 20
 *          dsp_task  (fama_pipeline: filters, STFT, FAMA-Net, Tier A/B, SampEn)  10
 * Core 0:  storage_task (raw 2 kSPS + events -> microSD .fbr)   5
 *          link_task    (1 kHz stream + results -> UDP/BLE)     6
 *          imu_task (104 Hz), ui_task (status, LEDs, button), radio stacks
 *
 * The acquisition path never blocks on storage, radio or inference: every
 * consumer has its own lock-free ring and overflow is counted, not hidden.
 */
#include <inttypes.h>
#include <math.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>

#include "ads1298.h"
#include "board.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "fama_dsp.h"
#include "fama_proto.h"
#include "freertos/FreeRTOS.h"
#include "freertos/message_buffer.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "imu.h"
#include "link.h"
#include "nvs_flash.h"
#include "power.h"
#include "ring.h"
#include "storage.h"
#if CONFIG_FAMA_BOARD_SOCKET
#include "td.h"
#endif

static const char *TAG = "fama";
#define FW_VERSION "0.1.0"

#define RAW_RING 4096     /* 2.0 s of 2 kSPS frames */
#define DSP_RING 4096
#define STREAM_RING 2048  /* 2.0 s of 1 kHz frames */
#define RAW_PER_PKT 50    /* 25 ms per SD packet */

typedef struct { int32_t c[8]; } frame_t;

static ring_t s_raw, s_dsp, s_stream;
static MessageBufferHandle_t s_log_q, s_out_q;
static SemaphoreHandle_t s_post_mx;
static atomic_uint s_frame2k;            /* ADC frame counter (2 kHz timebase) */
static atomic_bool s_acquiring, s_session, s_req_start, s_req_end, s_close_file;
static atomic_uint s_mode = FB_MODE_BOTH;
static char s_session_id[24] = "none";
static fama_net_t *s_net;
static fama_pipeline_t *s_pipe;
static uint32_t s_seq_log, s_seq_out;

/* ------------------------------------------------------------------ helpers */
static uint32_t frame1k(void) { return atomic_load(&s_frame2k) / 2; }

/* Post an event packet to the SD log and/or the live link. */
static void post(uint8_t type, uint32_t frame, uint16_t n, const void *payload, uint16_t len, bool to_log, bool to_out)
{
    uint8_t pkt[FB_HDR + 256 + FB_CRC];
    xSemaphoreTake(s_post_mx, portMAX_DELAY);
    if (to_log) {
        size_t m = fb_pack(pkt, sizeof(pkt), type, s_seq_log++, frame, n, payload, len);
        if (m) xMessageBufferSend(s_log_q, pkt, m, 0);
    }
    if (to_out) {
        size_t m = fb_pack(pkt, sizeof(pkt), type, s_seq_out++, frame, n, payload, len);
        if (m) xMessageBufferSend(s_out_q, pkt, m, 0);
    }
    xSemaphoreGive(s_post_mx);
}

/* ------------------------------------------------------------------ sessions */
static void session_start(const char *id)
{
    if (power_usb_present()) {
        ESP_LOGW(TAG, "session refused: unplug USB first (the analog front end is locked out)");
        return;
    }
    if (atomic_load(&s_session)) return;
    strlcpy(s_session_id, id && *id ? id : "S000", sizeof(s_session_id));
    if (!atomic_load(&s_acquiring)) {
#if CONFIG_FAMA_AFE_INPUT_SHORTED
        const ads_input_t mux = ADS_INPUT_SHORTED;
#elif CONFIG_FAMA_AFE_INPUT_TEST
        const ads_input_t mux = ADS_INPUT_TEST;
#else
        const ads_input_t mux = ADS_INPUT_NORMAL;
#endif
        if (ads_init(mux) != ESP_OK) {
            ESP_LOGE(TAG, "AFE init failed");
            return;
        }
        ads_start();
        atomic_store(&s_acquiring, true);
    }
    char meta[400];
    snprintf(meta, sizeof(meta),
             "{\"device\":\"FAMA-Band rev A\",\"fw\":\"%s\",\"session\":\"%s\",\"ads_id\":%u,"
             "\"fs_raw_hz\":2000,\"fs_stream_hz\":1000,\"gain\":6,\"vref\":2.4,\"lsb_volts\":%.10e,"
             "\"model_loaded\":%s,\"clock\":\"2.048 MHz +/-20 ppm\",\"start_frame_2k\":%u}",
             FW_VERSION, s_session_id, ads_id(), FAMA_ADC_LSB_V, s_net ? "true" : "false", (unsigned)atomic_load(&s_frame2k));
    if (storage_ready()) storage_open_session(s_session_id, meta);
    atomic_store(&s_req_start, true);  /* dsp_task resets the pipeline (Tier A warm-up, SampEn baseline) */
    atomic_store(&s_session, true);
    uint8_t ev[1 + sizeof(s_session_id)] = { 1 };
    memcpy(ev + 1, s_session_id, strlen(s_session_id));
    post(FB_T_SESSION, frame1k(), (uint16_t)strlen(s_session_id), ev, (uint16_t)(1 + strlen(s_session_id)), true, true);
    power_led(1, -1);
    ESP_LOGI(TAG, "session %s started", s_session_id);
}

static void session_end(void)
{
    if (!atomic_load(&s_session)) return;
    uint8_t ev[1 + sizeof(s_session_id)] = { 2 };
    memcpy(ev + 1, s_session_id, strlen(s_session_id));
    post(FB_T_SESSION, frame1k(), (uint16_t)strlen(s_session_id), ev, (uint16_t)(1 + strlen(s_session_id)), true, true);
    atomic_store(&s_session, false);
    atomic_store(&s_req_end, true);    /* dsp_task restores the pre-trained weights */
    atomic_store(&s_close_file, true); /* storage_task drains and closes */
    power_led(0, -1);
    ESP_LOGI(TAG, "session %s ended", s_session_id);
}

static void on_usb(bool present)
{
    if (present) {
        session_end();
        atomic_store(&s_acquiring, false);
        ads_power_off();
    }
}

static void on_cmd(const uint8_t *p, size_t len)
{
    if (!len) return;
    switch (p[0]) {
    case FB_CMD_SESSION_START: {
        char id[24] = { 0 };
        memcpy(id, p + 1, len - 1 < sizeof(id) - 1 ? len - 1 : sizeof(id) - 1);
        session_start(id);
        break;
    }
    case FB_CMD_SESSION_END: session_end(); break;
    case FB_CMD_MARKER:
        if (len >= 3) post(FB_T_MARKER, frame1k(), (uint16_t)(len - 3), p + 1, (uint16_t)(len - 1), true, true);
        break;
#if CONFIG_FAMA_BOARD_SOCKET
    case FB_CMD_TD_ENABLE:
        if (len >= 2) td_enable(p[1] != 0);
        break;
#endif
    case FB_CMD_SET_MODE:
        if (len >= 2) atomic_store(&s_mode, p[1]);
        break;
    default: break;
    }
}

/* ------------------------------------------------------------------ tasks */
static void acq_task(void *arg)
{
    static fama_decim_t dec;
    fama_decim_init(&dec);
    ads_set_drdy_task(xTaskGetCurrentTaskHandle());
    frame_t f;
    float in[8], out[8];
    for (;;) {
        if (!ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(20))) continue;
        if (!atomic_load(&s_acquiring)) continue;
        if (ads_read_frame(f.c, NULL) != ESP_OK) continue;
        atomic_fetch_add(&s_frame2k, 1);
        if (atomic_load(&s_session)) ring_push(&s_raw, &f);
        ring_push(&s_dsp, &f);
        for (int c = 0; c < 8; c++) in[c] = (float)f.c[c];
        if (fama_decim_push(&dec, in, out)) {
            frame_t s;
            for (int c = 0; c < 8; c++) s.c[c] = (int32_t)lrintf(out[c]);
            ring_push(&s_stream, &s);
        }
    }
}

static void dsp_task(void *arg)
{
    frame_t f;
    float v[8];
    fama_result_t r;
    for (;;) {
        if (atomic_exchange(&s_req_start, false)) fama_pipeline_session_start(s_pipe);
        if (atomic_exchange(&s_req_end, false)) fama_pipeline_session_end(s_pipe);
        if (!ring_pop(&s_dsp, &f)) {
            vTaskDelay(pdMS_TO_TICKS(2));
            continue;
        }
        for (int c = 0; c < 8; c++) v[c] = (float)((double)f.c[c] * FAMA_ADC_LSB_V);
        if (!fama_pipeline_push(s_pipe, v, &r)) continue;
        fb_result_t res = { .window = r.window_index, .gesture = (uint8_t)(r.gesture < 0 ? 255 : r.gesture),
                            .n_classes = (uint8_t)r.n_classes, .shat = r.shat, .sampen = r.sampen,
                            .sampen_baseline = r.sampen_baseline };
        res.flags = (uint8_t)((r.tierb_updated ? 1 : 0) | (r.window_index < FAMA_WARMUP_WINDOWS ? 2 : 0) |
                              (r.sampen_valid ? 4 : 0));
        for (int i = 0; i < r.n_classes && i < 16; i++) res.probs[i] = (uint8_t)lrintf(r.probs[i] * 255.0f);
        uint32_t mode = atomic_load(&s_mode);
        post(FB_T_RESULT, frame1k(), 1, &res, sizeof(res), atomic_load(&s_session), mode & FB_MODE_INFER);
#if CONFIG_FAMA_BOARD_SOCKET
        if (r.gesture >= 0) td_on_result(&res, r.probs, r.n_classes);
#endif
        if (r.tierb_updated) ESP_LOGI(TAG, "Tier B update #%" PRIu32 " at window %" PRIu32, s_pipe->tb.n_updates, r.window_index);
    }
}

static void storage_task(void *arg)
{
    static int32_t block[RAW_PER_PKT * 8];
    static uint8_t pkt[FB_HDR + RAW_PER_PKT * 24 + FB_CRC];
    static uint8_t ev[FB_HDR + 256 + FB_CRC];
    uint32_t first = 0, n = 0, seq = 0;
    int64_t last_sync = esp_timer_get_time();
    frame_t f;
    for (;;) {
        bool busy = false;
        while (ring_pop(&s_raw, &f)) {
            if (n == 0) first = atomic_load(&s_frame2k) - ring_count(&s_raw) - 1;
            memcpy(&block[n * 8], f.c, sizeof(f.c));
            if (++n == RAW_PER_PKT) {
                size_t m = fb_pack_emg(pkt, sizeof(pkt), FB_T_EMG_RAW, seq++, first, block, RAW_PER_PKT);
                storage_write(pkt, m);
                n = 0;
                busy = true;
            }
        }
        size_t m;
        while ((m = xMessageBufferReceive(s_log_q, ev, sizeof(ev), 0)) > 0) storage_write(ev, m);
        if (atomic_exchange(&s_close_file, false)) {
            if (n) storage_write(pkt, fb_pack_emg(pkt, sizeof(pkt), FB_T_EMG_RAW, seq++, first, block, (uint16_t)n));
            n = 0;
            storage_close_session();
        }
        if (esp_timer_get_time() - last_sync > 2000000) {
            storage_flush(true);
            last_sync = esp_timer_get_time();
        }
        if (!busy) vTaskDelay(pdMS_TO_TICKS(10));
    }
}

static void link_task(void *arg)
{
    static int32_t block[25 * 8];
    static uint8_t pkt[FB_HDR + 25 * 24 + FB_CRC];
    static uint8_t ev[FB_HDR + 256 + FB_CRC];
    uint32_t n = 0, first = 0, seq = 0;
    frame_t f;
    for (;;) {
        int per = link_frames_per_packet();
        bool stream = atomic_load(&s_mode) & FB_MODE_STREAM;
        while (ring_pop(&s_stream, &f)) {
            if (!stream || !link_connected()) { n = 0; continue; }
            if (n == 0) first = frame1k() - ring_count(&s_stream) - 1;
            memcpy(&block[n * 8], f.c, sizeof(f.c));
            if (++n == (uint32_t)per) {
                size_t m = fb_pack_emg(pkt, sizeof(pkt), FB_T_EMG_1K, seq++, first, block, (uint16_t)per);
                link_send(pkt, m);
                n = 0;
            }
        }
        size_t m;
        while ((m = xMessageBufferReceive(s_out_q, ev, sizeof(ev), 0)) > 0)
            if (link_connected()) link_send(ev, m);
        vTaskDelay(pdMS_TO_TICKS(5));
    }
}

static void imu_task(void *arg)
{
    int16_t buf[10 * 6];
    int k = 0;
    uint32_t first = 0;
    TickType_t last = xTaskGetTickCount();
    for (;;) {
        vTaskDelayUntil(&last, pdMS_TO_TICKS(10));
        if (!atomic_load(&s_acquiring)) { k = 0; continue; }
        if (k == 0) first = frame1k();
        if (imu_read(&buf[k * 6]) == ESP_OK && ++k == 10) {
            post(FB_T_IMU, first, 10, buf, sizeof(buf), atomic_load(&s_session), true);
            k = 0;
        }
    }
}

static void ui_task(void *arg)
{
    int held = 0;
    uint32_t t = 0;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(50));
        /* button: long press (2 s) toggles a session, short press drops a marker */
        if (gpio_get_level(PIN_BTN) == 0) {
            if (++held == 40) {
                if (atomic_load(&s_session)) session_end(); else session_start("BTN");
            }
        } else {
            if (held > 0 && held < 40) {
                uint8_t mk[2 + 6] = { 0xFF, 0xFF, 'B', 'U', 'T', 'T', 'O', 'N' };
                post(FB_T_MARKER, frame1k(), 6, mk, sizeof(mk), atomic_load(&s_session), true);
            }
            held = 0;
        }
        if (++t % 20) continue; /* 1 Hz below */
        fb_status_t st = { 0 };
        power_battery(&st.battery_pct, &st.battery_mv);
        st.flags = (uint8_t)((power_usb_present() ? 1 : 0) | (atomic_load(&s_session) ? 2 : 0) | (storage_ready() ? 4 : 0) |
                             (link_connected() ? 8 : 0) | (power_afe_is_on() ? 16 : 0));
        st.dropped_frames = atomic_load(&s_raw.dropped) + atomic_load(&s_dsp.dropped) + atomic_load(&s_stream.dropped);
        st.uptime_s = (uint32_t)(esp_timer_get_time() / 1000000);
        strlcpy(st.session, s_session_id, sizeof(st.session));
        post(FB_T_STATUS, frame1k(), 1, &st, sizeof(st), atomic_load(&s_session), true);
        power_led(-1, link_connected() ? 1 : (int)((t / 20) & 1));
    }
}

/* ------------------------------------------------------------------ main */
static void load_model(void)
{
    uint8_t *blob = NULL;
    size_t len = 0;
    if (!storage_ready() || storage_read_file("fama_net.bin", &blob, &len) != ESP_OK) {
        ESP_LOGW(TAG, "no /sdcard/fama_net.bin - streaming/logging only, no on-device inference");
        return;
    }
    size_t n = fama_net_arena_size(blob, len);
    float *arena = n ? heap_caps_malloc(n * sizeof(float), MALLOC_CAP_SPIRAM) : NULL;
    s_net = heap_caps_calloc(1, sizeof(fama_net_t), MALLOC_CAP_SPIRAM);
    if (!arena || !s_net || fama_net_load(s_net, blob, len, arena, n) != 0) {
        ESP_LOGE(TAG, "model blob rejected");
        free(s_net);
        s_net = NULL;
    } else {
        ESP_LOGI(TAG, "FAMA-Net loaded: %u nodes, %u buffers, arena %u kB", (unsigned)s_net->n_nodes,
                 (unsigned)s_net->n_bufs, (unsigned)(n * 4 / 1024));
    }
    free(blob);
}

void app_main(void)
{
    ESP_LOGI(TAG, "FAMA-Band firmware %s", FW_VERSION);
    esp_err_t e = nvs_flash_init();
    if (e == ESP_ERR_NVS_NO_FREE_PAGES || e == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }
    s_post_mx = xSemaphoreCreateMutex();
    s_log_q = xMessageBufferCreate(16 * 1024);
    s_out_q = xMessageBufferCreate(16 * 1024);
    ring_init(&s_raw, heap_caps_malloc(RAW_RING * sizeof(frame_t), MALLOC_CAP_SPIRAM), sizeof(frame_t), RAW_RING);
    ring_init(&s_dsp, heap_caps_malloc(DSP_RING * sizeof(frame_t), MALLOC_CAP_SPIRAM), sizeof(frame_t), DSP_RING);
    ring_init(&s_stream, heap_caps_malloc(STREAM_RING * sizeof(frame_t), MALLOC_CAP_SPIRAM), sizeof(frame_t), STREAM_RING);

    power_init();
    power_set_usb_callback(on_usb);
    imu_init();
    storage_init();
    load_model();
    s_pipe = heap_caps_calloc(1, sizeof(fama_pipeline_t), MALLOC_CAP_SPIRAM);
    fama_tierb_cfg_t tb;
    fama_tierb_default_cfg(&tb);
    fama_pipeline_init(s_pipe, s_net, &tb);
#if CONFIG_FAMA_BOARD_SOCKET
    td_init();
#endif
#if CONFIG_FAMA_RADIO_OFF
    ESP_LOGI(TAG, "prosthesis mode: radio off, logging to microSD only");
#else
    link_init(on_cmd);
#endif

    xTaskCreatePinnedToCore(acq_task, "acq", 4096, NULL, 20, NULL, 1);
    xTaskCreatePinnedToCore(dsp_task, "dsp", 8192, NULL, 10, NULL, 1);
    xTaskCreatePinnedToCore(storage_task, "sd", 6144, NULL, 5, NULL, 0);
    xTaskCreatePinnedToCore(link_task, "link", 6144, NULL, 6, NULL, 0);
    xTaskCreatePinnedToCore(imu_task, "imu", 3072, NULL, 4, NULL, 0);
    xTaskCreatePinnedToCore(ui_task, "ui", 4096, NULL, 2, NULL, 0);
    if (power_usb_present())
        ESP_LOGW(TAG, "USB connected: charging / data offload mode. Unplug to record.");
}
