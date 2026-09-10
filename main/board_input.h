#pragma once

#include "esp_err.h"

void board_input_prepare_after_wake(void);
esp_err_t board_input_start(void);
void board_input_request_sleep(void);
void board_input_request_shutdown(void);
