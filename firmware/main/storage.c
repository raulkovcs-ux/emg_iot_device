/* microSD (4-bit SDMMC, FAT) session logging in the .fbr format (docs/05_firmware.md). */
#include "storage.h"

#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "board.h"
#include "driver/sdmmc_host.h"
#include "esp_log.h"
#include "esp_vfs_fat.h"
#include "sdmmc_cmd.h"

static const char *TAG = "storage";
#define MOUNT "/sdcard"
#define HEADER_SIZE 512
#define WBUF (32 * 1024)

static sdmmc_card_t *s_card;
static FILE *s_f;
static uint8_t *s_buf;
static size_t s_fill;
static char s_path[64];

bool storage_ready(void) { return s_card != NULL; }
bool storage_recording(void) { return s_f != NULL; }
const char *storage_path(void) { return s_path; }

esp_err_t storage_init(void)
{
    gpio_config_t det = { .pin_bit_mask = 1ULL << PIN_SD_DET, .mode = GPIO_MODE_INPUT, .pull_up_en = 1 };
    gpio_config(&det);
    if (gpio_get_level(PIN_SD_DET) != 0) {
        ESP_LOGW(TAG, "no microSD card inserted");
        return ESP_ERR_NOT_FOUND;
    }
    sdmmc_host_t host = SDMMC_HOST_DEFAULT();
    host.max_freq_khz = SDMMC_FREQ_HIGHSPEED;
    sdmmc_slot_config_t slot = SDMMC_SLOT_CONFIG_DEFAULT();
    slot.width = 4;
    slot.clk = PIN_SD_CLK; slot.cmd = PIN_SD_CMD;
    slot.d0 = PIN_SD_D0; slot.d1 = PIN_SD_D1; slot.d2 = PIN_SD_D2; slot.d3 = PIN_SD_D3;
    slot.flags |= SDMMC_SLOT_FLAG_INTERNAL_PULLUP; /* external 10k pull-ups are fitted too */
    esp_vfs_fat_sdmmc_mount_config_t mc = { .format_if_mount_failed = false, .max_files = 4,
                                            .allocation_unit_size = 32 * 1024 };
    esp_err_t e = esp_vfs_fat_sdmmc_mount(MOUNT, &host, &slot, &mc, &s_card);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "mount failed: %s", esp_err_to_name(e));
        s_card = NULL;
        return e;
    }
    s_buf = heap_caps_malloc(WBUF, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    sdmmc_card_print_info(stdout, s_card);
    return ESP_OK;
}

esp_err_t storage_open_session(const char *session_id, const char *meta_json)
{
    if (!s_card) return ESP_ERR_INVALID_STATE;
    storage_close_session();
    for (int n = 0; n < 1000; n++) {
        snprintf(s_path, sizeof(s_path), MOUNT "/%.20s_%03d.fbr", session_id, n);
        struct stat st;
        if (stat(s_path, &st) != 0) break;
    }
    s_f = fopen(s_path, "wb");
    if (!s_f) return ESP_FAIL;
    uint8_t hdr[HEADER_SIZE] = { 'F', 'A', 'M', 'A', 'B', 'A', 'N', 'D', 1, 0 };
    size_t jl = strnlen(meta_json, HEADER_SIZE - 12);
    hdr[10] = (uint8_t)jl;
    hdr[11] = (uint8_t)(jl >> 8);
    memcpy(&hdr[12], meta_json, jl);
    fwrite(hdr, 1, HEADER_SIZE, s_f);
    s_fill = 0;
    ESP_LOGI(TAG, "recording to %s", s_path);
    return ESP_OK;
}

void storage_write(const uint8_t *pkt, size_t len)
{
    if (!s_f) return;
    if (s_fill + len > WBUF) storage_flush(false);
    if (len > WBUF) { fwrite(pkt, 1, len, s_f); return; }
    memcpy(s_buf + s_fill, pkt, len);
    s_fill += len;
}

void storage_flush(bool sync)
{
    if (!s_f) return;
    if (s_fill) {
        fwrite(s_buf, 1, s_fill, s_f);
        s_fill = 0;
    }
    if (sync) {
        fflush(s_f);
        fsync(fileno(s_f));
    }
}

void storage_close_session(void)
{
    if (!s_f) return;
    storage_flush(true);
    fclose(s_f);
    s_f = NULL;
    ESP_LOGI(TAG, "closed %s", s_path);
}

esp_err_t storage_read_file(const char *name, uint8_t **data, size_t *len)
{
    char path[64];
    snprintf(path, sizeof(path), MOUNT "/%s", name);
    FILE *f = fopen(path, "rb");
    if (!f) return ESP_ERR_NOT_FOUND;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    *data = heap_caps_malloc((size_t)n, MALLOC_CAP_SPIRAM);
    if (!*data) { fclose(f); return ESP_ERR_NO_MEM; }
    *len = fread(*data, 1, (size_t)n, f);
    fclose(f);
    return *len == (size_t)n ? ESP_OK : ESP_FAIL;
}
