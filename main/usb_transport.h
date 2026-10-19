#pragma once
#include <stdbool.h>
#include "cJSON.h"
#include "esp_err.h"

esp_err_t usb_transport_start(void);
cJSON *usb_transport_receive(void);
void usb_transport_send(cJSON *message);
