/* Live link: Wi-Fi UDP (default) or BLE GATT notifications (CONFIG_FAMA_LINK_BLE).
 *
 * UDP: the device joins CONFIG_FAMA_WIFI_SSID and listens on CONFIG_FAMA_UDP_PORT.
 * Until a host talks to it, it broadcasts a STATUS packet every second to
 * port CONFIG_FAMA_UDP_PORT + 1 so the host can discover its address. The
 * first FB_T_CMD packet received fixes the host address/port for streaming.
 *
 * BLE: service 0xFA00, characteristic 0xFA01 (notify, packets), 0xFA02
 * (write, commands). Packets are sized to fit a 247-byte ATT MTU.
 */
#include "link.h"

#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "fama_proto.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "sdkconfig.h"

static const char *TAG = "link";
static link_cmd_cb_t s_cb;

#if CONFIG_FAMA_LINK_BLE
/* ===================================================================== BLE */
#include "host/ble_hs.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

static uint16_t s_conn = BLE_HS_CONN_HANDLE_NONE;
static uint16_t s_data_handle;
static void advertise(void);

static int chr_access(uint16_t conn, uint16_t attr, struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    if (ctxt->op == BLE_GATT_ACCESS_OP_WRITE_CHR) {
        uint8_t buf[256];
        uint16_t n = 0;
        if (ble_hs_mbuf_to_flat(ctxt->om, buf, sizeof(buf), &n) == 0) {
            size_t len = fb_check(buf, n);
            if (len && buf[3] == FB_T_CMD && s_cb) s_cb(buf + FB_HDR, (size_t)(buf[14] | buf[15] << 8));
        }
        return 0;
    }
    return 0;
}

static const struct ble_gatt_svc_def s_svcs[] = {
    { .type = BLE_GATT_SVC_TYPE_PRIMARY, .uuid = BLE_UUID16_DECLARE(0xFA00),
      .characteristics = (struct ble_gatt_chr_def[]){
          { .uuid = BLE_UUID16_DECLARE(0xFA01), .access_cb = chr_access, .flags = BLE_GATT_CHR_F_NOTIFY,
            .val_handle = &s_data_handle },
          { .uuid = BLE_UUID16_DECLARE(0xFA02), .access_cb = chr_access,
            .flags = BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_NO_RSP },
          { 0 } } },
    { 0 } };

static int gap_event(struct ble_gap_event *ev, void *arg)
{
    switch (ev->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (ev->connect.status == 0) {
            s_conn = ev->connect.conn_handle;
            ESP_LOGI(TAG, "BLE connected");
        } else {
            advertise();
        }
        break;
    case BLE_GAP_EVENT_DISCONNECT:
        s_conn = BLE_HS_CONN_HANDLE_NONE;
        advertise();
        break;
    default:
        break;
    }
    return 0;
}

static void advertise(void)
{
    struct ble_hs_adv_fields f = { 0 };
    const char *name = ble_svc_gap_device_name();
    f.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    f.name = (uint8_t *)name;
    f.name_len = (uint8_t)strlen(name);
    f.name_is_complete = 1;
    ble_gap_adv_set_fields(&f);
    struct ble_gap_adv_params p = { .conn_mode = BLE_GAP_CONN_MODE_UND, .disc_mode = BLE_GAP_DISC_MODE_GEN };
    ble_gap_adv_start(BLE_OWN_ADDR_PUBLIC, NULL, BLE_HS_FOREVER, &p, gap_event, NULL);
}

static void on_sync(void) { advertise(); }

static void host_task(void *param)
{
    nimble_port_run();
    nimble_port_freertos_deinit();
}

esp_err_t link_init(link_cmd_cb_t cb)
{
    s_cb = cb;
    ESP_ERROR_CHECK(nimble_port_init());
    ble_hs_cfg.sync_cb = on_sync;
    ble_svc_gap_init();
    ble_svc_gatt_init();
    ble_svc_gap_device_name_set("FAMA-Band");
    ble_att_set_preferred_mtu(247);
    ble_gatts_count_cfg(s_svcs);
    ble_gatts_add_svcs(s_svcs);
    nimble_port_freertos_init(host_task);
    return ESP_OK;
}

bool link_connected(void) { return s_conn != BLE_HS_CONN_HANDLE_NONE; }
int link_frames_per_packet(void) { return 9; }

esp_err_t link_send(const uint8_t *pkt, size_t len)
{
    if (s_conn == BLE_HS_CONN_HANDLE_NONE) return ESP_ERR_INVALID_STATE;
    struct os_mbuf *om = ble_hs_mbuf_from_flat(pkt, (uint16_t)len);
    return ble_gatts_notify_custom(s_conn, s_data_handle, om) == 0 ? ESP_OK : ESP_FAIL;
}

#else
/* ================================================================ Wi-Fi UDP */
static int s_sock = -1;
static struct sockaddr_in s_host;
static volatile bool s_have_host, s_wifi_up;

static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        s_wifi_up = false;
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *e = (ip_event_got_ip_t *)data;
        ESP_LOGI(TAG, "Wi-Fi up, IP " IPSTR ", UDP port %d", IP2STR(&e->ip_info.ip), CONFIG_FAMA_UDP_PORT);
        s_wifi_up = true;
    }
}

static void rx_task(void *arg)
{
    uint8_t buf[512];
    for (;;) {
        struct sockaddr_in from;
        socklen_t fl = sizeof(from);
        int n = recvfrom(s_sock, buf, sizeof(buf), 0, (struct sockaddr *)&from, &fl);
        if (n <= 0) continue;
        size_t len = fb_check(buf, (size_t)n);
        if (!len || buf[3] != FB_T_CMD) continue;
        if (!s_have_host || from.sin_addr.s_addr != s_host.sin_addr.s_addr || from.sin_port != s_host.sin_port) {
            s_host = from;
            s_have_host = true;
            ESP_LOGI(TAG, "host %s:%d", inet_ntoa(from.sin_addr), ntohs(from.sin_port));
        }
        if (s_cb) s_cb(buf + FB_HDR, (size_t)(buf[14] | buf[15] << 8));
    }
}

static void beacon_task(void *arg)
{
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        if (!s_wifi_up || s_have_host) continue;
        uint8_t pkt[32];
        size_t n = fb_pack(pkt, sizeof(pkt), FB_T_STATUS, 0, 0, 0, "FAMA-Band", 9);
        struct sockaddr_in bc = { .sin_family = AF_INET, .sin_port = htons(CONFIG_FAMA_UDP_PORT + 1),
                                  .sin_addr.s_addr = htonl(INADDR_BROADCAST) };
        sendto(s_sock, pkt, n, 0, (struct sockaddr *)&bc, sizeof(bc));
    }
}

esp_err_t link_init(link_cmd_cb_t cb)
{
    s_cb = cb;
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t wc = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&wc));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, NULL));
    wifi_config_t cfg = { 0 };
    strlcpy((char *)cfg.sta.ssid, CONFIG_FAMA_WIFI_SSID, sizeof(cfg.sta.ssid));
    strlcpy((char *)cfg.sta.password, CONFIG_FAMA_WIFI_PASSWORD, sizeof(cfg.sta.password));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &cfg));
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE)); /* latency over power while streaming */
    ESP_ERROR_CHECK(esp_wifi_start());

    s_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    int on = 1;
    setsockopt(s_sock, SOL_SOCKET, SO_BROADCAST, &on, sizeof(on));
    struct sockaddr_in a = { .sin_family = AF_INET, .sin_port = htons(CONFIG_FAMA_UDP_PORT), .sin_addr.s_addr = htonl(INADDR_ANY) };
    bind(s_sock, (struct sockaddr *)&a, sizeof(a));
    xTaskCreatePinnedToCore(rx_task, "udp_rx", 4096, NULL, 8, NULL, 0);
    xTaskCreatePinnedToCore(beacon_task, "beacon", 3072, NULL, 3, NULL, 0);
    return ESP_OK;
}

bool link_connected(void) { return s_wifi_up && s_have_host; }
int link_frames_per_packet(void) { return 25; }

esp_err_t link_send(const uint8_t *pkt, size_t len)
{
    if (!link_connected()) return ESP_ERR_INVALID_STATE;
    int n = sendto(s_sock, pkt, len, 0, (struct sockaddr *)&s_host, sizeof(s_host));
    return n == (int)len ? ESP_OK : ESP_FAIL;
}
#endif
