#include "ble_bridge.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "connectivity.h"
#include "device_config.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "host/ble_hs.h"
#include "host/ble_uuid.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"
#include "store/config/ble_store_config.h"

#define RX_MAX 1024
#define RESPONSE_MAX 384

static const char *TAG = "ble_bridge";
static uint8_t s_own_addr_type;
static uint16_t s_response_handle;
static uint8_t s_rx[RX_MAX + 1];
static size_t s_rx_expected;
static size_t s_rx_length;
static uint32_t s_rx_crc;
static char s_response[RESPONSE_MAX] = "{\"ok\":true,\"state\":\"ready\"}";
static bool s_prepared;
static bool s_started;

void ble_store_config_init(void);

/* 7b4e0001-4db4-4c72-a729-ea5187241a43 */
static const ble_uuid128_t SERVICE_UUID = BLE_UUID128_INIT(
    0x43, 0x1a, 0x24, 0x87, 0x51, 0xea, 0x29, 0xa7,
    0x72, 0x4c, 0xb4, 0x4d, 0x01, 0x00, 0x4e, 0x7b);
static const ble_uuid128_t COMMAND_UUID = BLE_UUID128_INIT(
    0x43, 0x1a, 0x24, 0x87, 0x51, 0xea, 0x29, 0xa7,
    0x72, 0x4c, 0xb4, 0x4d, 0x02, 0x00, 0x4e, 0x7b);
static const ble_uuid128_t RESPONSE_UUID = BLE_UUID128_INIT(
    0x43, 0x1a, 0x24, 0x87, 0x51, 0xea, 0x29, 0xa7,
    0x72, 0x4c, 0xb4, 0x4d, 0x03, 0x00, 0x4e, 0x7b);
static const ble_uuid128_t STATUS_UUID = BLE_UUID128_INIT(
    0x43, 0x1a, 0x24, 0x87, 0x51, 0xea, 0x29, 0xa7,
    0x72, 0x4c, 0xb4, 0x4d, 0x04, 0x00, 0x4e, 0x7b);

static uint32_t crc32(const uint8_t *data, size_t length)
{
    uint32_t crc = 0xffffffffU;
    for (size_t index = 0; index < length; index++) {
        crc ^= data[index];
        for (unsigned bit = 0; bit < 8; bit++) {
            crc = (crc >> 1) ^ (0xedb88320U & (0U - (crc & 1U)));
        }
    }
    return ~crc;
}

static bool secure_equal(const char *left, const char *right)
{
    size_t left_len = strlen(left);
    size_t right_len = strlen(right);
    uint8_t difference = (uint8_t)(left_len ^ right_len);
    size_t length = left_len > right_len ? left_len : right_len;
    for (size_t index = 0; index < length; index++) {
        uint8_t a = index < left_len ? (uint8_t)left[index] : 0;
        uint8_t b = index < right_len ? (uint8_t)right[index] : 0;
        difference |= a ^ b;
    }
    return difference == 0;
}

static void set_response(const char *json)
{
    strlcpy(s_response, json, sizeof(s_response));
    if (s_response_handle != 0) ble_gatts_chr_updated(s_response_handle);
}

static void delayed_restart(void *argument)
{
    (void)argument;
    esp_restart();
}

static void schedule_restart(void)
{
    const esp_timer_create_args_t args = {.callback = delayed_restart, .name = "ble_restart"};
    esp_timer_handle_t timer;
    if (esp_timer_create(&args, &timer) == ESP_OK) {
        (void)esp_timer_start_once(timer, 1500000);
    }
}

static bool copy_required_string(cJSON *root, const char *key, char *target, size_t capacity, bool allow_empty)
{
    cJSON *value = cJSON_GetObjectItemCaseSensitive(root, key);
    if (!cJSON_IsString(value) || value->valuestring == NULL) return false;
    size_t length = strlen(value->valuestring);
    if (length >= capacity || (!allow_empty && length == 0)) return false;
    strlcpy(target, value->valuestring, capacity);
    return true;
}

static void apply_configuration(void)
{
    s_rx[s_rx_length] = '\0';
    cJSON *root = cJSON_ParseWithLength((const char *)s_rx, s_rx_length);
    if (!cJSON_IsObject(root)) {
        set_response("{\"ok\":false,\"error\":\"invalid_json\"}");
        cJSON_Delete(root);
        return;
    }
    cJSON *version = cJSON_GetObjectItemCaseSensitive(root, "v");
    cJSON *operation = cJSON_GetObjectItemCaseSensitive(root, "op");
    cJSON *auth = cJSON_GetObjectItemCaseSensitive(root, "auth");
    const device_config_values_t *current = device_config_get();
    if (!cJSON_IsNumber(version) || version->valueint != 1 ||
        !cJSON_IsString(operation) || strcmp(operation->valuestring, "configure") != 0 ||
        !cJSON_IsString(auth) ||
        (current->provisioned && !secure_equal(auth->valuestring, current->bridge_token))) {
        set_response("{\"ok\":false,\"error\":\"unauthorized\"}");
        cJSON_Delete(root);
        return;
    }
    device_config_values_t next = {0};
    bool valid = copy_required_string(root, "ssid", next.wifi_ssid, sizeof(next.wifi_ssid), false) &&
                 copy_required_string(root, "password", next.wifi_password, sizeof(next.wifi_password), true) &&
                 copy_required_string(root, "bridge_url", next.bridge_url, sizeof(next.bridge_url), false) &&
                 copy_required_string(root, "bridge_token", next.bridge_token, sizeof(next.bridge_token), false) &&
                 (strncmp(next.bridge_url, "http://", 7) == 0 || strncmp(next.bridge_url, "https://", 8) == 0) &&
                 strlen(next.bridge_token) >= 16;
    if (!valid) {
        set_response("{\"ok\":false,\"error\":\"invalid_config\"}");
        cJSON_Delete(root);
        return;
    }
    esp_err_t error = device_config_save(&next);
    cJSON_Delete(root);
    if (error != ESP_OK) {
        ESP_LOGE(TAG, "Unable to save Bluetooth configuration: %s", esp_err_to_name(error));
        set_response("{\"ok\":false,\"error\":\"storage_failed\"}");
        return;
    }
    ESP_LOGI(TAG, "Bluetooth provisioning accepted; restarting to apply configuration");
    set_response("{\"ok\":true,\"state\":\"restarting\"}");
    schedule_restart();
}

static int handle_command(struct os_mbuf *om)
{
    uint8_t buffer[256];
    uint16_t length = 0;
    if (ble_hs_mbuf_to_flat(om, buffer, sizeof(buffer), &length) != 0 || length < 1) {
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }
    if (buffer[0] == 1) {
        if (length != 7) return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
        size_t expected = (size_t)buffer[1] | ((size_t)buffer[2] << 8);
        if (expected == 0 || expected > RX_MAX) return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
        s_rx_expected = expected;
        s_rx_length = 0;
        s_rx_crc = (uint32_t)buffer[3] | ((uint32_t)buffer[4] << 8) |
                   ((uint32_t)buffer[5] << 16) | ((uint32_t)buffer[6] << 24);
        set_response("{\"ok\":true,\"state\":\"receiving\"}");
        return 0;
    }
    if (buffer[0] == 2) {
        if (s_rx_expected == 0 || s_rx_length + length - 1 > s_rx_expected) {
            return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
        }
        memcpy(s_rx + s_rx_length, buffer + 1, length - 1);
        s_rx_length += length - 1;
        return 0;
    }
    if (buffer[0] == 3) {
        if (length != 1 || s_rx_length != s_rx_expected || crc32(s_rx, s_rx_length) != s_rx_crc) {
            set_response("{\"ok\":false,\"error\":\"checksum_failed\"}");
            s_rx_expected = 0;
            return 0;
        }
        apply_configuration();
        s_rx_expected = 0;
        return 0;
    }
    return BLE_ATT_ERR_REQ_NOT_SUPPORTED;
}

static int gatt_access(uint16_t conn_handle, uint16_t attr_handle,
                       struct ble_gatt_access_ctxt *ctxt, void *argument)
{
    (void)conn_handle;
    (void)attr_handle;
    (void)argument;
    if (ctxt->op == BLE_GATT_ACCESS_OP_WRITE_CHR && ble_uuid_cmp(ctxt->chr->uuid, &COMMAND_UUID.u) == 0) {
        return handle_command(ctxt->om);
    }
    if (ctxt->op == BLE_GATT_ACCESS_OP_READ_CHR && ble_uuid_cmp(ctxt->chr->uuid, &RESPONSE_UUID.u) == 0) {
        return os_mbuf_append(ctxt->om, s_response, strlen(s_response)) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
    }
    if (ctxt->op == BLE_GATT_ACCESS_OP_READ_CHR && ble_uuid_cmp(ctxt->chr->uuid, &STATUS_UUID.u) == 0) {
        char bridge[64];
        connectivity_get_bridge_summary(bridge, sizeof(bridge));
        char status[RESPONSE_MAX];
        snprintf(status, sizeof(status),
                 "{\"v\":1,\"name\":\"Dotii\",\"firmware\":\"%s\",\"provisioned\":%s,\"wifi\":%s,\"bridge\":\"%s\"}",
                 esp_app_get_description()->version,
                 device_config_get()->provisioned ? "true" : "false",
                 connectivity_is_wifi_connected() ? "true" : "false", bridge);
        return os_mbuf_append(ctxt->om, status, strlen(status)) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
    }
    return BLE_ATT_ERR_UNLIKELY;
}

static const struct ble_gatt_svc_def SERVICES[] = {{
    .type = BLE_GATT_SVC_TYPE_PRIMARY,
    .uuid = &SERVICE_UUID.u,
    .characteristics = (struct ble_gatt_chr_def[]){{
        .uuid = &COMMAND_UUID.u,
        .access_cb = gatt_access,
        .flags = BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_ENC,
    }, {
        .uuid = &RESPONSE_UUID.u,
        .access_cb = gatt_access,
        .flags = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_READ_ENC | BLE_GATT_CHR_F_NOTIFY,
        .val_handle = &s_response_handle,
    }, {
        .uuid = &STATUS_UUID.u,
        .access_cb = gatt_access,
        .flags = BLE_GATT_CHR_F_READ,
    }, {0}}
}, {0}};

static int gap_event(struct ble_gap_event *event, void *argument);

static void advertise(void)
{
    struct ble_hs_adv_fields fields = {0};
    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.name = (uint8_t *)ble_svc_gap_device_name();
    fields.name_len = strlen((char *)fields.name);
    fields.name_is_complete = 1;
    fields.uuids128 = (ble_uuid128_t *)&SERVICE_UUID;
    fields.num_uuids128 = 1;
    fields.uuids128_is_complete = 1;
    int rc = ble_gap_adv_set_fields(&fields);
    if (rc != 0) {
        ESP_LOGE(TAG, "Unable to set advertising fields: %d", rc);
        return;
    }
    struct ble_gap_adv_params params = {0};
    params.conn_mode = BLE_GAP_CONN_MODE_UND;
    params.disc_mode = BLE_GAP_DISC_MODE_GEN;
    rc = ble_gap_adv_start(s_own_addr_type, NULL, BLE_HS_FOREVER, &params, gap_event, NULL);
    if (rc != 0) ESP_LOGE(TAG, "Unable to advertise: %d", rc);
}

static int gap_event(struct ble_gap_event *event, void *argument)
{
    (void)argument;
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status == 0) {
            ESP_LOGI(TAG, "Bluetooth bridge connected");
        } else {
            advertise();
        }
        return 0;
    case BLE_GAP_EVENT_DISCONNECT:
        ESP_LOGI(TAG, "Bluetooth bridge disconnected: %d", event->disconnect.reason);
        advertise();
        return 0;
    case BLE_GAP_EVENT_ADV_COMPLETE:
        advertise();
        return 0;
    case BLE_GAP_EVENT_SUBSCRIBE:
        return 0;
    case BLE_GAP_EVENT_MTU:
        ESP_LOGI(TAG, "Bluetooth MTU updated: %d", event->mtu.value);
        return 0;
    case BLE_GAP_EVENT_ENC_CHANGE:
        ESP_LOGI(TAG, "Bluetooth encryption changed: %d", event->enc_change.status);
        return 0;
    default:
        return 0;
    }
}

static void on_sync(void)
{
    int rc = ble_hs_util_ensure_addr(0);
    if (rc == 0) rc = ble_hs_id_infer_auto(0, &s_own_addr_type);
    if (rc != 0) {
        ESP_LOGE(TAG, "Unable to select Bluetooth address: %d", rc);
        return;
    }
    advertise();
}

static void host_task(void *argument)
{
    (void)argument;
    nimble_port_run();
    nimble_port_freertos_deinit();
}

esp_err_t ble_bridge_prepare(void)
{
    if (s_prepared) return ESP_OK;
    esp_err_t error = nimble_port_init();
    if (error != ESP_OK) return error;
    ble_hs_cfg.sync_cb = on_sync;
    ble_hs_cfg.store_status_cb = ble_store_util_status_rr;
    ble_hs_cfg.sm_io_cap = BLE_HS_IO_NO_INPUT_OUTPUT;
    ble_hs_cfg.sm_bonding = 1;
    ble_hs_cfg.sm_sc = 1;
    ble_hs_cfg.sm_our_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;
    ble_hs_cfg.sm_their_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;
    ble_svc_gap_init();
    ble_svc_gatt_init();
    int rc = ble_gatts_count_cfg(SERVICES);
    if (rc == 0) rc = ble_gatts_add_svcs(SERVICES);
    if (rc == 0) rc = ble_svc_gap_device_name_set("Dotii");
    if (rc != 0) return ESP_FAIL;
    ble_store_config_init();
    s_prepared = true;
    ESP_LOGI(TAG, "Bluetooth controller reserved");
    return ESP_OK;
}

esp_err_t ble_bridge_start(void)
{
    if (!s_prepared) return ESP_ERR_INVALID_STATE;
    if (s_started) return ESP_OK;
    nimble_port_freertos_init(host_task);
    s_started = true;
    ESP_LOGI(TAG, "Bluetooth bridge started");
    return ESP_OK;
}
