#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

/* Commands arrive as FB_T_CMD packets (fama_proto.h) over Wi-Fi UDP or BLE. */
typedef void (*link_cmd_cb_t)(const uint8_t *payload, size_t len);

esp_err_t link_init(link_cmd_cb_t cb);
bool link_connected(void);
/* Max EMG frames per stream packet for the active link (UDP 25, BLE 9). */
int link_frames_per_packet(void);
esp_err_t link_send(const uint8_t *pkt, size_t len);
