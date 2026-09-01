#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

void connectivity_start(void);
void connectivity_request_refresh(void);
bool connectivity_is_wifi_connected(void);
void connectivity_get_summary(char *buffer, size_t buffer_size);
void connectivity_get_ip(char *buffer, size_t buffer_size);
void connectivity_get_bridge_summary(char *buffer, size_t buffer_size);
const uint8_t *connectivity_custom_image_data(uint32_t revision);
const uint8_t *connectivity_bambu_camera_data(uint32_t revision);
bool connectivity_bambu_command(const char *action);
