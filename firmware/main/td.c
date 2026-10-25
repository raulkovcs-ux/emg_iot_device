/* Terminal-device output: FAMA-Net decisions -> prosthetic hand controller. */
#include "sdkconfig.h"
#if CONFIG_FAMA_BOARD_SOCKET
#include "td.h"

#include <stdatomic.h>

#include "driver/ledc.h"
#include "driver/uart.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "fama_dsp.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

static const char *TAG = "td";
#define PIN_TD_TX 43
#define PIN_TD_RX 44
#define PIN_TD_PWM1 16
#define PIN_TD_PWM2 8
#define PWM_BITS LEDC_TIMER_10_BIT
#define PWM_MAX ((1 << 10) - 1)
#define STALE_US (300 * 1000)   /* no result for 300 ms -> outputs to 0 */

static fama_td_t s_td;
static atomic_bool s_on;
static atomic_llong s_last_us;
static uint32_t s_seq;
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;

static void set_out(const float out[2])
{
    for (int k = 0; k < 2; k++) {
        uint32_t d = (uint32_t)(out[k] * PWM_MAX + 0.5f);
        ledc_set_duty(LEDC_LOW_SPEED_MODE, (ledc_channel_t)k, d > PWM_MAX ? PWM_MAX : d);
        ledc_update_duty(LEDC_LOW_SPEED_MODE, (ledc_channel_t)k);
    }
}

static void watchdog_task(void *arg)
{
    float zero[2];
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(20));
        if (esp_timer_get_time() - atomic_load(&s_last_us) > STALE_US || !atomic_load(&s_on)) {
            taskENTER_CRITICAL(&s_mux);
            fama_td_stale(&s_td, zero);
            taskEXIT_CRITICAL(&s_mux);
            set_out(zero);
        }
    }
}

void td_init(void)
{
    ledc_timer_config_t t = { .speed_mode = LEDC_LOW_SPEED_MODE, .duty_resolution = PWM_BITS, .timer_num = LEDC_TIMER_0,
                              .freq_hz = 20000, .clk_cfg = LEDC_AUTO_CLK };
    ESP_ERROR_CHECK(ledc_timer_config(&t));
    const int pins[2] = { PIN_TD_PWM1, PIN_TD_PWM2 };
    for (int k = 0; k < 2; k++) {
        ledc_channel_config_t ch = { .gpio_num = pins[k], .speed_mode = LEDC_LOW_SPEED_MODE, .channel = (ledc_channel_t)k,
                                     .timer_sel = LEDC_TIMER_0, .duty = 0, .hpoint = 0 };
        ESP_ERROR_CHECK(ledc_channel_config(&ch));
    }
    uart_config_t uc = { .baud_rate = 115200, .data_bits = UART_DATA_8_BITS, .parity = UART_PARITY_DISABLE,
                         .stop_bits = UART_STOP_BITS_1, .flow_ctrl = UART_HW_FLOWCTRL_DISABLE, .source_clk = UART_SCLK_DEFAULT };
    ESP_ERROR_CHECK(uart_driver_install(UART_NUM_0, 256, 1024, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(UART_NUM_0, &uc));
    ESP_ERROR_CHECK(uart_set_pin(UART_NUM_0, PIN_TD_TX, PIN_TD_RX, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
    fama_td_init(&s_td, CONFIG_FAMA_TD_OPEN_CLASS, CONFIG_FAMA_TD_CLOSE_CLASS);
#ifdef CONFIG_FAMA_TD_ENABLE_AT_BOOT
    atomic_store(&s_on, true);
#else
    atomic_store(&s_on, false);
#endif
    xTaskCreatePinnedToCore(watchdog_task, "td_wd", 2048, NULL, 9, NULL, 0);
    ESP_LOGI(TAG, "hand interface ready, outputs %s (open=class %d, close=class %d)",
             atomic_load(&s_on) ? "ENABLED" : "disabled", CONFIG_FAMA_TD_OPEN_CLASS, CONFIG_FAMA_TD_CLOSE_CLASS);
}

void td_enable(bool on)
{
    atomic_store(&s_on, on);
    ESP_LOGW(TAG, "hand outputs %s", on ? "ENABLED" : "disabled");
}

bool td_enabled(void) { return atomic_load(&s_on); }

void td_on_result(const fb_result_t *res, const float *probs, int n_classes)
{
    uint8_t pkt[FB_HDR + sizeof(fb_result_t) + FB_CRC];
    size_t n = fb_pack(pkt, sizeof(pkt), FB_T_RESULT, s_seq++, res->window, 1, res, sizeof(*res));
    uart_write_bytes(UART_NUM_0, pkt, n);   /* decisions always visible to the hand controller */
    if (!atomic_load(&s_on)) return;
    float out[2];
    taskENTER_CRITICAL(&s_mux);
    fama_td_update(&s_td, probs, n_classes, out);
    taskEXIT_CRITICAL(&s_mux);
    set_out(out);
    atomic_store(&s_last_us, esp_timer_get_time());
}
#endif /* CONFIG_FAMA_BOARD_SOCKET */
