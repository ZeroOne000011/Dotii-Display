idf_component_register(
    SRCS
        "main.c"
        "app_state.c"
        "connectivity.c"
        "device_config.c"
        "ble_bridge.c"
        "board_input.c"
        "state_ui.c"
        "generated/ui_font_detail_20.c"
        "generated/ui_font_fixed_20.c"
        "generated/ui_font_chinese_semibold_24.c"
        "generated/ui_font_bambu_semibold_24.c"
        "generated/ui_font_digits_64.c"
        "generated/ui_icons.c"
    INCLUDE_DIRS "." "generated"
    REQUIRES
        esp_event
        esp_app_format
        esp_http_client
        esp_netif
        esp_timer
        esp_wifi
        bt
        espressif__cjson
        mbedtls
        nvs_flash
        waveshare__esp32_s3_touch_amoled_1_75
)

# LVGL 9.4 + GCC 15 can emit a benign warning when IRAM declarations and
# definitions receive different unique section suffixes. Limit the workaround
# to LVGL so warnings remain strict for our application and other components.
idf_component_get_property(lvgl_lib lvgl__lvgl COMPONENT_LIB)
target_compile_options(${lvgl_lib} PRIVATE -Wno-error=attributes)
