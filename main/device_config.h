#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

#define DEVICE_LINK_MODE_WIFI 0
#define DEVICE_LINK_MODE_BLE  1

typedef struct {
    char wifi_ssid[33];
    char wifi_password[65];
    char bridge_url[256];
    char bridge_token[65];
    bool provisioned;
    uint8_t link_mode; /* DEVICE_LINK_MODE_*；用户显式选择，重置配网不清除 */
} device_config_values_t;

esp_err_t device_config_init(void);
const device_config_values_t *device_config_get(void);
esp_err_t device_config_save(const device_config_values_t *values);
esp_err_t device_config_clear_provisioning(void);
