#include "device_config.h"

#include <string.h>

#include "esp_log.h"
#include "nvs.h"
#include "sdkconfig.h"

static const char *TAG = "device_config";
static const char *NAMESPACE = "dotii_cfg";
static device_config_values_t s_config;

static void load_string(nvs_handle_t handle, const char *key, char *target, size_t capacity)
{
    size_t length = capacity;
    esp_err_t error = nvs_get_str(handle, key, target, &length);
    if (error != ESP_OK && error != ESP_ERR_NVS_NOT_FOUND) {
        ESP_LOGW(TAG, "Unable to read %s: %s", key, esp_err_to_name(error));
    }
}

esp_err_t device_config_init(void)
{
    memset(&s_config, 0, sizeof(s_config));
    strlcpy(s_config.wifi_ssid, CONFIG_STATE_DISPLAY_WIFI_SSID, sizeof(s_config.wifi_ssid));
    strlcpy(s_config.wifi_password, CONFIG_STATE_DISPLAY_WIFI_PASSWORD, sizeof(s_config.wifi_password));
    strlcpy(s_config.bridge_url, CONFIG_STATE_DISPLAY_BRIDGE_URL, sizeof(s_config.bridge_url));
    strlcpy(s_config.bridge_token, CONFIG_STATE_DISPLAY_BRIDGE_TOKEN, sizeof(s_config.bridge_token));

    nvs_handle_t handle;
    esp_err_t error = nvs_open(NAMESPACE, NVS_READONLY, &handle);
    if (error == ESP_ERR_NVS_NOT_FOUND) {
        ESP_LOGI(TAG, "Using build-time defaults until Bluetooth provisioning completes");
        return ESP_OK;
    }
    if (error != ESP_OK) return error;
    load_string(handle, "ssid", s_config.wifi_ssid, sizeof(s_config.wifi_ssid));
    load_string(handle, "wifi_pass", s_config.wifi_password, sizeof(s_config.wifi_password));
    load_string(handle, "bridge_url", s_config.bridge_url, sizeof(s_config.bridge_url));
    load_string(handle, "bridge_tok", s_config.bridge_token, sizeof(s_config.bridge_token));
    uint8_t provisioned = 0;
    (void)nvs_get_u8(handle, "provisioned", &provisioned);
    s_config.provisioned = provisioned == 1;
    nvs_close(handle);
    ESP_LOGI(TAG, "Runtime configuration loaded (provisioned=%s)", s_config.provisioned ? "yes" : "no");
    return ESP_OK;
}

const device_config_values_t *device_config_get(void)
{
    return &s_config;
}

esp_err_t device_config_save(const device_config_values_t *values)
{
    if (values == NULL) return ESP_ERR_INVALID_ARG;
    nvs_handle_t handle;
    esp_err_t error = nvs_open(NAMESPACE, NVS_READWRITE, &handle);
    if (error != ESP_OK) return error;
    if ((error = nvs_set_str(handle, "ssid", values->wifi_ssid)) == ESP_OK &&
        (error = nvs_set_str(handle, "wifi_pass", values->wifi_password)) == ESP_OK &&
        (error = nvs_set_str(handle, "bridge_url", values->bridge_url)) == ESP_OK &&
        (error = nvs_set_str(handle, "bridge_tok", values->bridge_token)) == ESP_OK &&
        (error = nvs_set_u8(handle, "provisioned", 1)) == ESP_OK) {
        error = nvs_commit(handle);
    }
    nvs_close(handle);
    return error;
}

