#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

void state_ui_start(QueueHandle_t snapshot_queue);
void state_ui_button_a_short(void);
void state_ui_button_a_long(void);
void state_ui_button_b_short(void);
void state_ui_button_b_long(void);
void state_ui_set_power(uint8_t percent, bool external_power, bool charging);
