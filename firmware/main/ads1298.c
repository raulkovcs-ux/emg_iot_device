/* TI ADS1298 driver (datasheet SBAS459). */
#include "ads1298.h"

#include <string.h>

#include "board.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "power.h"

static const char *TAG = "ads1298";

/* commands */
#define CMD_WAKEUP 0x02
#define CMD_RESET 0x06
#define CMD_START 0x08
#define CMD_STOP 0x0A
#define CMD_RDATAC 0x10
#define CMD_SDATAC 0x11
#define CMD_RREG 0x20
#define CMD_WREG 0x40
/* registers */
#define REG_ID 0x00
#define REG_CONFIG1 0x01
#define REG_CONFIG2 0x02
#define REG_CONFIG3 0x03
#define REG_LOFF 0x04
#define REG_CH1SET 0x05
#define REG_RLD_SENSP 0x0D
#define REG_RLD_SENSN 0x0E
#define REG_GPIO 0x14
#define REG_CONFIG4 0x17
#define REG_WCT1 0x18
#define REG_WCT2 0x19

/* CONFIG1: HR=1, DAISY_EN=0, CLK_EN=0 (external clock in), DR=100 -> 2 kSPS in HR mode */
#define CONFIG1_VAL 0x84
/* CONFIG2: test signal internal, 1x amplitude, f_CLK/2^21 (~1 Hz) - only used by the self test */
#define CONFIG2_NORMAL 0x00
#define CONFIG2_TEST 0x10
/* CONFIG3: PD_REFBUF=1, bit6=1, VREF_4V=0 (2.4 V), RLDREF_INT=1, PD_RLD=1 */
#define CONFIG3_VAL 0xCC
/* CHnSET: power on, gain 6 (000), mux */
#define CHSET(mux) ((uint8_t)(0x00 | ((mux) & 0x07)))

static spi_device_handle_t s_dev;
static TaskHandle_t s_drdy_task;
static uint8_t s_id;

static void IRAM_ATTR drdy_isr(void *arg)
{
    BaseType_t woken = pdFALSE;
    if (s_drdy_task) vTaskNotifyGiveFromISR(s_drdy_task, &woken);
    portYIELD_FROM_ISR(woken);
}

void ads_set_drdy_task(void *task_handle) { s_drdy_task = (TaskHandle_t)task_handle; }

static esp_err_t xfer(const uint8_t *tx, uint8_t *rx, size_t len)
{
    spi_transaction_t t = { .length = len * 8, .tx_buffer = tx, .rx_buffer = rx };
    return spi_device_polling_transmit(s_dev, &t);
}

static esp_err_t cmd(uint8_t c)
{
    esp_err_t e = xfer(&c, NULL, 1);
    esp_rom_delay_us(4); /* >= 4 tCLK between commands */
    return e;
}

static esp_err_t wreg(uint8_t reg, uint8_t val)
{
    uint8_t tx[3] = { (uint8_t)(CMD_WREG | reg), 0x00, val };
    esp_err_t e = xfer(tx, NULL, 3);
    esp_rom_delay_us(4);
    return e;
}

static uint8_t rreg(uint8_t reg)
{
    uint8_t tx[3] = { (uint8_t)(CMD_RREG | reg), 0x00, 0x00 }, rx[3] = { 0 };
    xfer(tx, rx, 3);
    esp_rom_delay_us(4);
    return rx[2];
}

uint8_t ads_id(void) { return s_id; }

esp_err_t ads_init(ads_input_t input)
{
    if (!power_afe_enable(true)) {
        ESP_LOGW(TAG, "analog rail refused (USB power present)");
        return ESP_ERR_INVALID_STATE;
    }
    gpio_config_t out = { .pin_bit_mask = (1ULL << PIN_AFE_START) | (1ULL << PIN_AFE_RESET) | (1ULL << PIN_AFE_PWDN),
                          .mode = GPIO_MODE_OUTPUT };
    gpio_config(&out);
    gpio_set_level(PIN_AFE_START, 0);
    gpio_set_level(PIN_AFE_PWDN, 1);
    gpio_set_level(PIN_AFE_RESET, 1);

    if (!s_dev) {
        spi_bus_config_t bus = { .mosi_io_num = PIN_AFE_DIN, .miso_io_num = PIN_AFE_DOUT, .sclk_io_num = PIN_AFE_SCLK,
                                 .quadwp_io_num = -1, .quadhd_io_num = -1, .max_transfer_sz = 64 };
        ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_DISABLED));
        spi_device_interface_config_t dev = { .clock_speed_hz = AFE_SPI_HZ, .mode = 1, .spics_io_num = PIN_AFE_CS,
                                              .queue_size = 2, .cs_ena_posttrans = 2 };
        ESP_ERROR_CHECK(spi_bus_add_device(SPI2_HOST, &dev, &s_dev));
        gpio_config_t in = { .pin_bit_mask = 1ULL << PIN_AFE_DRDY, .mode = GPIO_MODE_INPUT, .pull_up_en = 1,
                             .intr_type = GPIO_INTR_NEGEDGE };
        gpio_config(&in);
        gpio_install_isr_service(ESP_INTR_FLAG_IRAM);
        gpio_isr_handler_add(PIN_AFE_DRDY, drdy_isr, NULL);
    }

    /* power-up: wait for the supplies and VCAP1 (t_POR = 2^18 tCLK = 128 ms), then reset */
    vTaskDelay(pdMS_TO_TICKS(150));
    gpio_set_level(PIN_AFE_RESET, 0);
    esp_rom_delay_us(10); /* >= 2 tCLK */
    gpio_set_level(PIN_AFE_RESET, 1);
    esp_rom_delay_us(20); /* 18 tCLK before use */
    cmd(CMD_SDATAC);

    s_id = rreg(REG_ID);
    if ((s_id & 0xE0) != 0x80 || (s_id & 0x07) != 0x02) {
        ESP_LOGE(TAG, "unexpected ID 0x%02X (want ADS1298 0x92)", s_id);
        return ESP_ERR_NOT_FOUND;
    }
    wreg(REG_CONFIG3, CONFIG3_VAL);
    vTaskDelay(pdMS_TO_TICKS(10)); /* internal reference settling */
    wreg(REG_CONFIG1, CONFIG1_VAL);
    wreg(REG_CONFIG2, input == ADS_INPUT_TEST ? CONFIG2_TEST : CONFIG2_NORMAL);
    wreg(REG_LOFF, 0x00);
    for (int ch = 0; ch < ADS_N_CH; ch++) wreg((uint8_t)(REG_CH1SET + ch), CHSET(input));
    wreg(REG_RLD_SENSP, 0xFF); /* RLD derived from all positive ... */
    wreg(REG_RLD_SENSN, 0xFF); /* ... and negative inputs */
    wreg(REG_GPIO, 0x00);      /* GPIO1-4 outputs, low (pins are unconnected) */
    wreg(REG_CONFIG4, 0x00);
    wreg(REG_WCT1, 0x00);
    wreg(REG_WCT2, 0x00);
    if (rreg(REG_CONFIG1) != CONFIG1_VAL || rreg(REG_CONFIG3) != CONFIG3_VAL) {
        ESP_LOGE(TAG, "register read-back failed");
        return ESP_FAIL;
    }
    cmd(CMD_RDATAC);
    ESP_LOGI(TAG, "ADS1298 ready (ID 0x%02X), 2000 SPS, gain 6, input mux %d", s_id, input);
    return ESP_OK;
}

esp_err_t ads_start(void)
{
    gpio_set_level(PIN_AFE_START, 1);
    return ESP_OK;
}

esp_err_t ads_stop(void)
{
    gpio_set_level(PIN_AFE_START, 0);
    return ESP_OK;
}

void ads_power_off(void)
{
    ads_stop();
    gpio_set_level(PIN_AFE_PWDN, 0);
    power_afe_enable(false);
}

esp_err_t ads_read_frame(int32_t codes[ADS_N_CH], uint32_t *status)
{
    uint8_t rx[3 + 3 * ADS_N_CH];
    static const uint8_t zeros[3 + 3 * ADS_N_CH] = { 0 };
    esp_err_t e = xfer(zeros, rx, sizeof(rx));
    if (e != ESP_OK) return e;
    if (status) *status = (uint32_t)rx[0] << 16 | (uint32_t)rx[1] << 8 | rx[2];
    for (int ch = 0; ch < ADS_N_CH; ch++) {
        const uint8_t *p = &rx[3 + 3 * ch];
        int32_t v = (int32_t)((uint32_t)p[0] << 16 | (uint32_t)p[1] << 8 | p[2]);
        if (v & 0x800000) v -= 0x1000000;
        codes[ch] = v;
    }
    return ESP_OK;
}
