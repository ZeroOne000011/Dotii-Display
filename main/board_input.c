#include "board_input.h"

#include <stdbool.h>
#include <stdint.h>

#include "bsp/esp-bsp.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_io_expander.h"
#include "esp_log.h"
#include "esp_sleep.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"
#include "state_ui.h"

#define BUTTON_A_GPIO GPIO_NUM_0
#define TOUCH_INTERRUPT_GPIO GPIO_NUM_11
#define BUTTON_LONG_MS 800
#define BUTTON_B_MASK IO_EXPANDER_PIN_NUM_4
#define AXP2101_ADDRESS 0x34
#define AXP2101_STATUS1 0x00
#define AXP2101_STATUS2 0x01
#define AXP2101_COMMON_CONFIG 0x10
#define AXP2101_POWER_ON_STATUS 0x20
#define AXP2101_POWER_OFF_STATUS 0x21
#define AXP2101_ADC_CHANNEL_CTRL 0x30
#define AXP2101_BAT_VOLTAGE_H 0x34
#define AXP2101_BAT_VOLTAGE_L 0x35
#define AXP2101_SYS_VOLTAGE_H 0x3A
#define AXP2101_SYS_VOLTAGE_L 0x3B
#define AXP2101_TS_PIN_CTRL 0x50
#define AXP2101_BAT_DETECT_CTRL 0x68
#define AXP2101_BAT_PERCENT 0xA4
#define PMU_STATUS_INTERVAL_MS 250
#define PMU_POWER_DEBOUNCE_MS 750
#define PMU_BATTERY_INTERVAL_MS 10000
#define PMU_FULL_PERCENT 100
#define PMU_CHARGE_RESUME_PERCENT 97

static const char *TAG = "board_input";
static i2c_master_dev_handle_t s_pmu;
static esp_io_expander_handle_t s_expander;
static volatile bool s_sleep_requested;
static volatile bool s_shutdown_requested;

static bool pmu_read(uint8_t reg, uint8_t *value)
{
    return s_pmu != NULL &&
           i2c_master_transmit_receive(s_pmu, &reg, 1, value, 1, 100) == ESP_OK;
}

static bool pmu_write(uint8_t reg, uint8_t value)
{
    uint8_t data[2] = {reg, value};
    return s_pmu != NULL && i2c_master_transmit(s_pmu, data, sizeof(data), 100) == ESP_OK;
}

static bool pmu_update_bits(uint8_t reg, uint8_t mask, uint8_t value)
{
    uint8_t current = 0;
    if (!pmu_read(reg, &current)) return false;
    const uint8_t updated = (uint8_t)((current & ~mask) | (value & mask));
    return updated == current || pmu_write(reg, updated);
}

static bool pmu_read_adc(uint8_t high_reg, uint8_t low_reg, uint8_t high_mask,
                         uint16_t *millivolts)
{
    uint8_t high = 0;
    uint8_t low = 0;
    if (!pmu_read(high_reg, &high) || !pmu_read(low_reg, &low)) return false;
    *millivolts = (uint16_t)(((uint16_t)(high & high_mask) << 8) | low);
    return true;
}

static void pmu_start(void)
{
    i2c_master_bus_handle_t bus = bsp_i2c_get_handle();
    if (bus == NULL) {
        ESP_LOGW(TAG, "BSP I2C bus unavailable; battery reading is disabled");
        return;
    }
    i2c_device_config_t config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = AXP2101_ADDRESS,
        .scl_speed_hz = 400000,
    };
    if (i2c_master_bus_add_device(bus, &config, &s_pmu) != ESP_OK) {
        ESP_LOGW(TAG, "AXP2101 unavailable; battery reading is disabled");
        s_pmu = NULL;
        return;
    }
    uint8_t power_on = 0;
    uint8_t power_off = 0;
    if (pmu_read(AXP2101_POWER_ON_STATUS, &power_on) &&
        pmu_read(AXP2101_POWER_OFF_STATUS, &power_off)) {
        ESP_LOGI(TAG,
                 "AXP2101 power-on=0x%02X power-off=0x%02X (vsys_uv=%d dcdc_uv=%d)",
                 power_on, power_off, (power_off & 0x08) != 0, (power_off & 0x20) != 0);
    }

    /*
     * The board has no battery NTC on TS. Match Waveshare's AXP2101 example:
     * treat TS as a fixed input, enable battery detection, and enable the ADC
     * channels needed for supply diagnostics. Charging current/voltage are
     * intentionally left untouched because the attached cell capacity is not
     * known to the firmware.
     */
    const bool pmu_configured =
        pmu_update_bits(AXP2101_TS_PIN_CTRL, 0x1F, 0x10) &&
        pmu_update_bits(AXP2101_BAT_DETECT_CTRL, 0x01, 0x01) &&
        pmu_update_bits(AXP2101_ADC_CHANNEL_CTRL, 0x0B, 0x09);
    if (!pmu_configured) {
        ESP_LOGW(TAG, "AXP2101 battery diagnostic configuration failed");
    }
}

static void pwr_button_start(void)
{
    s_expander = bsp_io_expander_init();
    if (s_expander == NULL ||
        esp_io_expander_set_dir(s_expander, BUTTON_B_MASK, IO_EXPANDER_INPUT) != ESP_OK) {
        ESP_LOGW(TAG, "TCA9554 EXIO4 unavailable; PWR key is disabled");
        s_expander = NULL;
    }
}

static void call_ui(void (*handler)(void))
{
    if (bsp_display_lock(100) == ESP_OK) {
        handler();
        bsp_display_unlock();
    }
}

static void prepare_display_for_power_down(void)
{
    if (bsp_display_lock(1000) == ESP_OK) {
        esp_err_t err = bsp_display_enter_sleep();
        if (err != ESP_OK) ESP_LOGW(TAG, "AMOLED sleep command failed: %s", esp_err_to_name(err));
        bsp_display_unlock();
    }
}

static void enter_deep_sleep(void)
{
    prepare_display_for_power_down();
    esp_err_t wifi_result = esp_wifi_stop();
    if (wifi_result != ESP_OK && wifi_result != ESP_ERR_WIFI_NOT_INIT) {
        ESP_LOGW(TAG, "Wi-Fi stop before deep-sleep failed: %s", esp_err_to_name(wifi_result));
    }

    const uint64_t wake_mask = (1ULL << BUTTON_A_GPIO) | (1ULL << TOUCH_INTERRUPT_GPIO);
    ESP_ERROR_CHECK(esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_ALL));
    ESP_ERROR_CHECK(esp_sleep_enable_ext1_wakeup_io(wake_mask, ESP_EXT1_WAKEUP_ANY_LOW));
    ESP_LOGI(TAG, "Entering deep-sleep; wake GPIO0=%d GPIO11=%d",
             gpio_get_level(BUTTON_A_GPIO), gpio_get_level(TOUCH_INTERRUPT_GPIO));
    esp_deep_sleep_start();
}

static void power_off(void)
{
    prepare_display_for_power_down();
    ESP_LOGI(TAG, "Requesting AXP2101 soft power-off");
    if (!pmu_update_bits(AXP2101_COMMON_CONFIG, 0x01, 0x01)) {
        ESP_LOGE(TAG, "AXP2101 soft power-off failed; falling back to deep-sleep");
        enter_deep_sleep();
    }
    /* A successful write cuts the rails. If it does not, avoid leaving a
       fully active but black device. */
    vTaskDelay(pdMS_TO_TICKS(1000));
    ESP_LOGE(TAG, "AXP2101 power-off did not cut the rail; falling back to deep-sleep");
    enter_deep_sleep();
}

static void input_task(void *argument)
{
    (void)argument;
    gpio_config_t button_config = {
        .pin_bit_mask = 1ULL << BUTTON_A_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&button_config);
    pmu_start();
    pwr_button_start();

    bool a_pressed = false;
    bool a_long_sent = false;
    int64_t a_pressed_at = 0;
    bool b_pressed = false;
    bool b_long_sent = false;
    int64_t b_pressed_at = 0;
    int64_t last_pmu_read = 0;
    int64_t last_battery_read = -PMU_BATTERY_INTERVAL_MS;
    int64_t power_candidate_since = 0;
    int64_t charging_candidate_since = 0;
    uint8_t battery_percent = 0xFF;
    bool external_power = false;
    bool charging = false;
    bool power_candidate = false;
    bool power_candidate_valid = false;
    bool charging_candidate = false;
    bool charging_candidate_valid = false;
    bool charge_complete_latched = false;
    bool first_pmu_sample = true;
    bool power_ui_pending = false;

    while (true) {
        bool now_pressed = gpio_get_level(BUTTON_A_GPIO) == 0;
        int64_t now_ms = esp_timer_get_time() / 1000;
        if (now_pressed && !a_pressed) {
            a_pressed = true;
            a_long_sent = false;
            a_pressed_at = now_ms;
        } else if (now_pressed && a_pressed && !a_long_sent &&
                   now_ms - a_pressed_at >= BUTTON_LONG_MS) {
            a_long_sent = true;
            call_ui(state_ui_button_a_long);
        } else if (!now_pressed && a_pressed) {
            if (!a_long_sent && now_ms - a_pressed_at >= 40) {
                call_ui(state_ui_button_a_short);
            }
            a_pressed = false;
        }

        if (s_expander != NULL) {
            uint32_t levels = 0;
            if (esp_io_expander_get_level(s_expander, BUTTON_B_MASK, &levels) == ESP_OK) {
                /* Waveshare's conditioning circuit makes PWR/EXIO4 active high. */
                bool b_now_pressed = (levels & BUTTON_B_MASK) != 0;
                if (b_now_pressed && !b_pressed) {
                    b_pressed = true;
                    b_long_sent = false;
                    b_pressed_at = now_ms;
                } else if (b_now_pressed && b_pressed && !b_long_sent &&
                           now_ms - b_pressed_at >= BUTTON_LONG_MS) {
                    b_long_sent = true;
                    call_ui(state_ui_button_b_long);
                } else if (!b_now_pressed && b_pressed) {
                    if (!b_long_sent && now_ms - b_pressed_at >= 40) {
                        call_ui(state_ui_button_b_short);
                    }
                    b_pressed = false;
                }
            }
        }

        if (s_pmu != NULL && now_ms - last_pmu_read >= PMU_STATUS_INTERVAL_MS) {
            uint8_t status1 = 0;
            uint8_t status2 = 0;
            bool publish_power = false;
            bool status_valid = false;
            bool raw_external_power = false;
            bool raw_charging = false;
            bool battery_present = false;
            bool batfet_open = false;
            if (pmu_read(AXP2101_STATUS1, &status1) && pmu_read(AXP2101_STATUS2, &status2)) {
                status_valid = true;
                raw_external_power = (status1 & 0x20) != 0;
                raw_charging = ((status2 >> 5) & 0x03) == 0x01;
                battery_present = (status1 & 0x08) != 0;
                batfet_open = (status1 & 0x10) != 0;
                if (!power_candidate_valid || raw_external_power != power_candidate) {
                    power_candidate = raw_external_power;
                    power_candidate_valid = true;
                    power_candidate_since = now_ms;
                } else if (raw_external_power != external_power &&
                           now_ms - power_candidate_since >= PMU_POWER_DEBOUNCE_MS) {
                    external_power = raw_external_power;
                    publish_power = true;
                }
            }
            last_pmu_read = now_ms;

            if (now_ms - last_battery_read >= PMU_BATTERY_INTERVAL_MS) {
                uint8_t percent = 0xFF;
                uint16_t battery_mv = 0;
                uint16_t system_mv = 0;
                if (pmu_read(AXP2101_BAT_PERCENT, &percent) && percent <= 100 &&
                    percent != battery_percent) {
                    battery_percent = percent;
                    publish_power = true;
                }
                if (pmu_read_adc(AXP2101_BAT_VOLTAGE_H, AXP2101_BAT_VOLTAGE_L,
                                 0x1F, &battery_mv) &&
                    pmu_read_adc(AXP2101_SYS_VOLTAGE_H, AXP2101_SYS_VOLTAGE_L,
                                 0x3F, &system_mv)) {
                    ESP_LOGI(TAG, "AXP2101 battery=%u%% vbat=%umV vsys=%umV",
                             percent <= 100 ? percent : battery_percent,
                             battery_mv, system_mv);
                }
                last_battery_read = now_ms;
            }

            if (status_valid) {
                if (!raw_external_power ||
                    (battery_percent <= PMU_CHARGE_RESUME_PERCENT && battery_percent <= 100)) {
                    charge_complete_latched = false;
                } else if (battery_percent >= PMU_FULL_PERCENT && battery_percent <= 100) {
                    charge_complete_latched = true;
                }

                const bool requested_charging =
                    raw_external_power && raw_charging && !charge_complete_latched;
                if (!charging_candidate_valid || requested_charging != charging_candidate) {
                    charging_candidate = requested_charging;
                    charging_candidate_valid = true;
                    charging_candidate_since = now_ms;
                } else if (requested_charging != charging &&
                           now_ms - charging_candidate_since >= PMU_POWER_DEBOUNCE_MS) {
                    charging = requested_charging;
                    publish_power = true;
                }

                if (first_pmu_sample || publish_power) {
                    ESP_LOGI(TAG,
                             "AXP2101 status1=0x%02X status2=0x%02X vbus=%d battery=%d batfet_open=%d raw_charging=%d ui_charging=%d full_latched=%d",
                             status1, status2, raw_external_power, battery_present,
                             batfet_open, raw_charging, charging, charge_complete_latched);
                    if (!battery_present) {
                        ESP_LOGW(TAG, "No battery detected; unplugging VBUS cannot be sustained");
                    }
                    first_pmu_sample = false;
                }
            }
            if (publish_power) power_ui_pending = true;
            if (power_ui_pending && bsp_display_lock(100) == ESP_OK) {
                state_ui_set_power(battery_percent, external_power, charging);
                power_ui_pending = false;
                bsp_display_unlock();
            }
        }
        if (s_shutdown_requested) {
            s_shutdown_requested = false;
            power_off();
        }
        if (s_sleep_requested) {
            s_sleep_requested = false;
            enter_deep_sleep();
        }
        vTaskDelay(pdMS_TO_TICKS(25));
    }
}

void board_input_start(void)
{
    xTaskCreate(input_task, "board_input", 4096, NULL, 6, NULL);
}

void board_input_request_sleep(void)
{
    s_sleep_requested = true;
}

void board_input_request_shutdown(void)
{
    s_shutdown_requested = true;
}
