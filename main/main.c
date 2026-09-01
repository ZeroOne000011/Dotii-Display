#include "app_state.h"
#include "board_input.h"
#include "ble_bridge.h"
#include "bsp/esp-bsp.h"
#include "connectivity.h"
#include "device_config.h"
#include "esp_app_desc.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_sleep.h"
#include "esp_system.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "state_ui.h"

static const char *TAG = "state_display";

static const char *reset_reason_name(esp_reset_reason_t reason)
{
    switch (reason) {
    case ESP_RST_POWERON: return "power-on";
    case ESP_RST_EXT: return "external-pin";
    case ESP_RST_SW: return "software";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_INT_WDT: return "interrupt-watchdog";
    case ESP_RST_TASK_WDT: return "task-watchdog";
    case ESP_RST_WDT: return "watchdog";
    case ESP_RST_DEEPSLEEP: return "deep-sleep";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_SDIO: return "sdio";
    case ESP_RST_USB: return "usb";
    case ESP_RST_JTAG: return "jtag";
    case ESP_RST_EFUSE: return "efuse";
    case ESP_RST_PWR_GLITCH: return "power-glitch";
    case ESP_RST_CPU_LOCKUP: return "cpu-lockup";
    case ESP_RST_UNKNOWN:
    default: return "unknown";
    }
}

static bool reset_reason_is_abnormal(esp_reset_reason_t reason)
{
    return reason == ESP_RST_PANIC || reason == ESP_RST_INT_WDT ||
           reason == ESP_RST_TASK_WDT || reason == ESP_RST_WDT ||
           reason == ESP_RST_BROWNOUT || reason == ESP_RST_PWR_GLITCH ||
           reason == ESP_RST_CPU_LOCKUP;
}

static void log_reset_diagnostics(void)
{
    const esp_reset_reason_t current = esp_reset_reason();
    uint8_t last_abnormal = ESP_RST_UNKNOWN;
    nvs_handle_t handle;
    esp_err_t err = nvs_open("diagnostics", NVS_READWRITE, &handle);
    if (err == ESP_OK) {
        (void)nvs_get_u8(handle, "last_reset", &last_abnormal);
        if (reset_reason_is_abnormal(current) && last_abnormal != (uint8_t)current) {
            if (nvs_set_u8(handle, "last_reset", (uint8_t)current) == ESP_OK) {
                (void)nvs_commit(handle);
                last_abnormal = (uint8_t)current;
            }
        }
        nvs_close(handle);
    } else {
        ESP_LOGW(TAG, "Reset diagnostic NVS unavailable: %s", esp_err_to_name(err));
    }
    ESP_LOGI(TAG, "Reset reason=%s (%d), last abnormal=%s (%u)",
             reset_reason_name(current), current,
             reset_reason_name((esp_reset_reason_t)last_abnormal), last_abnormal);
    const uint32_t wake_causes = esp_sleep_get_wakeup_causes();
    if (wake_causes != 0) {
        ESP_LOGI(TAG, "Sleep wake causes=0x%lX ext1_mask=0x%llX",
                 (unsigned long)wake_causes,
                 (unsigned long long)esp_sleep_get_ext1_wakeup_status());
    }
}

void app_main(void)
{
    esp_err_t nvs_error = nvs_flash_init();
    if (nvs_error == ESP_ERR_NVS_NO_FREE_PAGES || nvs_error == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    } else {
        ESP_ERROR_CHECK(nvs_error);
    }
    log_reset_diagnostics();
    ESP_ERROR_CHECK(device_config_init());

    QueueHandle_t queue = app_state_queue_create();
    ESP_ERROR_CHECK(queue == NULL ? ESP_ERR_NO_MEM : ESP_OK);
    /* Wi-Fi owns the primary data path. Initialize both radios before the
       display claims its internal DMA buffers so coexistence has contiguous
       controller memory without sacrificing the screen pipeline. */
    connectivity_start();
    esp_err_t ble_error = ble_bridge_prepare();
    if (ble_error == ESP_OK) ble_error = ble_bridge_start();
    if (ble_error != ESP_OK) {
        ESP_LOGE(TAG, "Bluetooth bridge unavailable: %s", esp_err_to_name(ble_error));
    }

    lv_display_t *display = bsp_display_start();
    ESP_ERROR_CHECK(display == NULL ? ESP_FAIL : ESP_OK);
    ESP_ERROR_CHECK(bsp_display_lock(1000));
    state_ui_start(queue);
    bsp_display_unlock();

    board_input_start();
    ESP_LOGI(TAG, "Dotii %s started", esp_app_get_description()->version);
}
