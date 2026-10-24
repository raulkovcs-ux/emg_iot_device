#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

esp_err_t storage_init(void);
bool storage_ready(void);
bool storage_recording(void);
const char *storage_path(void);
/* Creates /sdcard/<id>_NNN.fbr with the 512-byte header (meta_json inside). */
esp_err_t storage_open_session(const char *session_id, const char *meta_json);
/* Buffered packet append. Only call from the storage task. */
void storage_write(const uint8_t *pkt, size_t len);
void storage_flush(bool sync);
void storage_close_session(void);
/* Reads a whole file from the card into PSRAM (caller frees). */
esp_err_t storage_read_file(const char *name, uint8_t **data, size_t *len);
