#pragma once

#include <stdbool.h>
#include "esp_err.h"

typedef struct {
    char wifi_ssid[33];
    char wifi_password[65];
    char bridge_url[256];
    char bridge_token[65];
    bool provisioned;
} device_config_values_t;

esp_err_t device_config_init(void);
const device_config_values_t *device_config_get(void);
esp_err_t device_config_save(const device_config_values_t *values);

