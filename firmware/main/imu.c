/* LSM6DSL 6-axis IMU, 104 Hz, polled. */
#include "imu.h"

#include "driver/i2c_master.h"
#include "esp_log.h"

static const char *TAG = "imu";
static i2c_master_dev_handle_t s_imu;

extern i2c_master_bus_handle_t power_i2c_bus(void);

#define IMU_ADDR 0x6A
#define WHO_AM_I 0x0F
#define CTRL1_XL 0x10
#define CTRL2_G 0x11
#define CTRL3_C 0x12
#define OUTX_L_G 0x22

static esp_err_t wr(uint8_t reg, uint8_t v)
{
    uint8_t b[2] = { reg, v };
    return i2c_master_transmit(s_imu, b, 2, 20);
}

esp_err_t imu_init(void)
{
    i2c_device_config_t dc = { .dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = IMU_ADDR, .scl_speed_hz = 400000 };
    ESP_ERROR_CHECK(i2c_master_bus_add_device(power_i2c_bus(), &dc, &s_imu));
    uint8_t reg = WHO_AM_I, id = 0;
    esp_err_t e = i2c_master_transmit_receive(s_imu, &reg, 1, &id, 1, 20);
    if (e != ESP_OK || id != 0x6A) {
        ESP_LOGW(TAG, "LSM6DSL not found (id 0x%02X, %s)", id, esp_err_to_name(e));
        return ESP_ERR_NOT_FOUND;
    }
    wr(CTRL3_C, 0x44);  /* BDU, register auto-increment */
    wr(CTRL1_XL, 0x40); /* 104 Hz, +/-2 g */
    wr(CTRL2_G, 0x40);  /* 104 Hz, 250 dps */
    return ESP_OK;
}

esp_err_t imu_read(int16_t out[6])
{
    uint8_t reg = OUTX_L_G, b[12];
    esp_err_t e = i2c_master_transmit_receive(s_imu, &reg, 1, b, 12, 20);
    if (e != ESP_OK) return e;
    /* device order gx gy gz ax ay az -> packet order ax ay az gx gy gz */
    for (int i = 0; i < 3; i++) {
        out[3 + i] = (int16_t)(b[2 * i] | b[2 * i + 1] << 8);
        out[i] = (int16_t)(b[6 + 2 * i] | b[6 + 2 * i + 1] << 8);
    }
    return ESP_OK;
}
