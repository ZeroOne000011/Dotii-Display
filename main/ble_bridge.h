#pragma once

#include <stdbool.h>

#include "esp_err.h"

esp_err_t ble_bridge_prepare(void);
esp_err_t ble_bridge_start(void);
esp_err_t ble_bridge_clear_bonds(void);
bool ble_bridge_is_connected(void);
void ble_bridge_report_decision(const char *request_id, bool allow);
