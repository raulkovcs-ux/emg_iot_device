#pragma once
#include <stdint.h>

#include "esp_err.h"

esp_err_t imu_init(void);
/* ax ay az gx gy gz, raw LSB (2 g / 250 dps full scale) */
esp_err_t imu_read(int16_t out[6]);
