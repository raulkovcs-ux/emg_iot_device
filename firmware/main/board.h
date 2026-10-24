/* FAMA-Band hub rev A pin map. Must match hardware/gen/hub.py (MCU sheet). */
#pragma once
#include "driver/gpio.h"

/* ADS1298 on SPI2 */
#define PIN_AFE_SCLK   GPIO_NUM_12
#define PIN_AFE_DIN    GPIO_NUM_11   /* MCU MOSI -> ADS DIN */
#define PIN_AFE_DOUT   GPIO_NUM_13   /* ADS DOUT -> MCU MISO */
#define PIN_AFE_CS     GPIO_NUM_10
#define PIN_AFE_DRDY   GPIO_NUM_9
#define PIN_AFE_START  GPIO_NUM_14
#define PIN_AFE_RESET  GPIO_NUM_21
#define PIN_AFE_PWDN   GPIO_NUM_47
#define PIN_AFE_EN     GPIO_NUM_48   /* LP5907 enable via R; hardware-gated off by VBUS */

/* I2C: LSM6DSL (0x6A) + LC709203F (0x0B) */
#define PIN_I2C_SDA    GPIO_NUM_4
#define PIN_I2C_SCL    GPIO_NUM_5
#define PIN_IMU_INT1   GPIO_NUM_6
#define PIN_FG_ALRT    GPIO_NUM_7

#define PIN_VBUS_DET   GPIO_NUM_15   /* high = USB power present (2/3 VBUS divider) */
#define PIN_LED_G      GPIO_NUM_17
#define PIN_LED_B      GPIO_NUM_18
#define PIN_BTN        GPIO_NUM_0    /* BOOT/USER, active low */

/* microSD, 4-bit SDMMC through the GPIO matrix */
#define PIN_SD_CLK     GPIO_NUM_39
#define PIN_SD_CMD     GPIO_NUM_38
#define PIN_SD_D0      GPIO_NUM_40
#define PIN_SD_D1      GPIO_NUM_41
#define PIN_SD_D2      GPIO_NUM_42
#define PIN_SD_D3      GPIO_NUM_2
#define PIN_SD_DET     GPIO_NUM_1    /* low = card inserted (DM3AT detect switch to GND) */

#define AFE_SPI_HZ     (8 * 1000 * 1000)
