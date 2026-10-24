#pragma once
#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

esp_err_t power_init(void);            /* GPIOs, I2C bus, fuel gauge */
bool power_usb_present(void);
/* Request the analog rail. Returns false (and keeps it off) while USB power is
 * present. The hardware interlock (Q1) enforces the same rule independently. */
bool power_afe_enable(bool on);
bool power_afe_is_on(void);
/* Battery state from the LC709203F; returns ESP_FAIL if the gauge did not answer. */
esp_err_t power_battery(uint8_t *pct, uint16_t *mv);
void power_led(int green, int blue);
/* Called on VBUS edges (from an ISR-deferred task) */
void power_set_usb_callback(void (*cb)(bool present));
