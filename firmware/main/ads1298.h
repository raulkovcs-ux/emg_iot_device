#pragma once
#include <stdint.h>

#include "esp_err.h"

#define ADS_N_CH 8

typedef enum { ADS_INPUT_NORMAL = 0, ADS_INPUT_SHORTED = 1, ADS_INPUT_TEST = 5 } ads_input_t;

/* Power the analog rail (refused while USB power is present), reset, configure
 * 2000 SPS high-resolution mode, gain 6, internal 2.4 V reference, RLD on all
 * inputs, external 2.048 MHz clock. Leaves the device in RDATAC, not started. */
esp_err_t ads_init(ads_input_t input);
esp_err_t ads_start(void);   /* START high; DRDY begins at 2 kHz */
esp_err_t ads_stop(void);
void ads_power_off(void);
uint8_t ads_id(void);

/* Called from the acquisition task after a DRDY notification.
 * codes[8] receive sign-extended 24-bit values; status = 24 status bits. */
esp_err_t ads_read_frame(int32_t codes[ADS_N_CH], uint32_t *status);

/* Task to notify (xTaskNotifyGive) on each DRDY falling edge. */
void ads_set_drdy_task(void *task_handle);
