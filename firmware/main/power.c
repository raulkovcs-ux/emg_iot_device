/* Power management: USB interlock, analog-rail enable, LC709203F fuel gauge, LEDs. */
#include "power.h"

#include "board.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "power";
static bool s_afe_on;
static i2c_master_bus_handle_t s_bus;
static i2c_master_dev_handle_t s_fg;
static void (*s_usb_cb)(bool);
static TaskHandle_t s_vbus_task;

i2c_master_bus_handle_t power_i2c_bus(void) { return s_bus; }

static void IRAM_ATTR vbus_isr(void *arg)
{
    /* Safety: drop the analog rail immediately on USB insertion; firmware state follows in the task. */
    if (gpio_get_level(PIN_VBUS_DET)) {
        gpio_set_level(PIN_AFE_EN, 0);
        s_afe_on = false;
    }
    BaseType_t woken = pdFALSE;
    if (s_vbus_task) vTaskNotifyGiveFromISR(s_vbus_task, &woken);
    portYIELD_FROM_ISR(woken);
}

static void vbus_task(void *arg)
{
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        vTaskDelay(pdMS_TO_TICKS(20)); /* debounce */
        bool usb = power_usb_present();
        ESP_LOGW(TAG, "USB power %s", usb ? "PRESENT - analog front end locked out" : "removed");
        if (s_usb_cb) s_usb_cb(usb);
    }
}

void power_set_usb_callback(void (*cb)(bool present)) { s_usb_cb = cb; }

bool power_usb_present(void) { return gpio_get_level(PIN_VBUS_DET) == 1; }

bool power_afe_enable(bool on)
{
    if (on && power_usb_present()) {
        gpio_set_level(PIN_AFE_EN, 0);
        s_afe_on = false;
        return false;
    }
    gpio_set_level(PIN_AFE_EN, on ? 1 : 0);
    s_afe_on = on;
    return true;
}

bool power_afe_is_on(void) { return s_afe_on; }

void power_led(int green, int blue)
{
    if (green >= 0) gpio_set_level(PIN_LED_G, green);
    if (blue >= 0) gpio_set_level(PIN_LED_B, blue);
}

/* ---------------------------------------------------------------- LC709203F
 * I2C address 0x0B, 16-bit little-endian registers, CRC-8 (poly 0x07) over
 * address/command/data on every transfer. */
#define FG_ADDR 0x0B
#define FG_CELL_VOLTAGE 0x09
#define FG_APA 0x0B
#define FG_RSOC 0x0D
#define FG_CHANGE_PARAM 0x12
#define FG_STATUS_BIT 0x16
#define FG_POWER_MODE 0x15

static uint8_t crc8(const uint8_t *d, int n)
{
    uint8_t c = 0;
    for (int i = 0; i < n; i++) {
        c ^= d[i];
        for (int b = 0; b < 8; b++) c = (c & 0x80) ? (uint8_t)((c << 1) ^ 0x07) : (uint8_t)(c << 1);
    }
    return c;
}

static esp_err_t fg_write(uint8_t reg, uint16_t val)
{
    uint8_t f[5] = { FG_ADDR << 1, reg, (uint8_t)val, (uint8_t)(val >> 8), 0 };
    f[4] = crc8(f, 4);
    return i2c_master_transmit(s_fg, &f[1], 4, 50);
}

static esp_err_t fg_read(uint8_t reg, uint16_t *val)
{
    uint8_t rx[3];
    esp_err_t e = i2c_master_transmit_receive(s_fg, &reg, 1, rx, 3, 50);
    if (e != ESP_OK) return e;
    uint8_t f[5] = { FG_ADDR << 1, reg, (FG_ADDR << 1) | 1, rx[0], rx[1] };
    if (crc8(f, 5) != rx[2]) return ESP_ERR_INVALID_CRC;
    *val = (uint16_t)(rx[0] | rx[1] << 8);
    return ESP_OK;
}

esp_err_t power_battery(uint8_t *pct, uint16_t *mv)
{
    if (!s_fg) return ESP_FAIL;
    uint16_t v = 0, r = 0;
    esp_err_t e = fg_read(FG_CELL_VOLTAGE, &v);
    if (e == ESP_OK) e = fg_read(FG_RSOC, &r);
    if (e == ESP_OK) { *mv = v; *pct = (uint8_t)(r > 100 ? 100 : r); }
    return e;
}

esp_err_t power_init(void)
{
    gpio_config_t out = { .pin_bit_mask = (1ULL << PIN_AFE_EN) | (1ULL << PIN_LED_G) | (1ULL << PIN_LED_B),
                          .mode = GPIO_MODE_OUTPUT };
    gpio_config(&out);
    gpio_set_level(PIN_AFE_EN, 0);
    power_led(0, 0);
    gpio_config_t in = { .pin_bit_mask = 1ULL << PIN_VBUS_DET, .mode = GPIO_MODE_INPUT, .intr_type = GPIO_INTR_ANYEDGE };
    gpio_config(&in);
    gpio_config_t btn = { .pin_bit_mask = (1ULL << PIN_BTN) | (1ULL << PIN_FG_ALRT), .mode = GPIO_MODE_INPUT,
                          .pull_up_en = 1 };
    gpio_config(&btn);
    gpio_install_isr_service(ESP_INTR_FLAG_IRAM);
    xTaskCreate(vbus_task, "vbus", 3072, NULL, 10, &s_vbus_task);
    gpio_isr_handler_add(PIN_VBUS_DET, vbus_isr, NULL);

    i2c_master_bus_config_t bc = { .i2c_port = I2C_NUM_0, .sda_io_num = PIN_I2C_SDA, .scl_io_num = PIN_I2C_SCL,
                                   .clk_source = I2C_CLK_SRC_DEFAULT, .glitch_ignore_cnt = 7 };
    ESP_ERROR_CHECK(i2c_new_master_bus(&bc, &s_bus));
    i2c_device_config_t dc = { .dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = FG_ADDR, .scl_speed_hz = 100000 };
    ESP_ERROR_CHECK(i2c_master_bus_add_device(s_bus, &dc, &s_fg));
    /* operational mode, APA 0x12 (~600 mAh: between the 500 mAh 0x10 and 1000 mAh 0x19 table points),
     * battery profile 4.2 V Li-ion, temperature via I2C */
    esp_err_t e = fg_write(FG_POWER_MODE, 0x0001);
    if (e == ESP_OK) e = fg_write(FG_APA, 0x0012);
    if (e == ESP_OK) e = fg_write(FG_CHANGE_PARAM, 0x0001);
    if (e == ESP_OK) e = fg_write(FG_STATUS_BIT, 0x0000);
    if (e != ESP_OK) ESP_LOGW(TAG, "fuel gauge not responding (%s)", esp_err_to_name(e));
    ESP_LOGI(TAG, "USB power %s", power_usb_present() ? "present (AFE locked out)" : "absent");
    return ESP_OK;
}
