

#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_lcd_types.h"
#include "esp_err.h"

/* LCD color formats */
#define ESP_LCD_COLOR_FORMAT_RGB565    (1)
#define ESP_LCD_COLOR_FORMAT_RGB888    (2)

/* LCD display color format */
#define BSP_LCD_COLOR_FORMAT        (ESP_LCD_COLOR_FORMAT_RGB565)
/* LCD display color bytes endianess */
#define BSP_LCD_BIGENDIAN           (0)
/* LCD display color bits */
#define BSP_LCD_BITS_PER_PIXEL      (16)
/* LCD display color space */
#define BSP_LCD_COLOR_SPACE         (LCD_RGB_ELEMENT_ORDER_RGB)
/* LCD display definition */
#define BSP_LCD_H_RES              (466)
#define BSP_LCD_V_RES              (466)

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief BSP display configuration structure
 *
 */
typedef struct {
    int max_transfer_sz;    /*!< Maximum transfer size, in bytes. */
} bsp_display_config_t;

typedef enum {
    BSP_DISPLAY_ROTATE_0   = 0,
    BSP_DISPLAY_ROTATE_90  = 1,
    BSP_DISPLAY_ROTATE_180 = 2,
    BSP_DISPLAY_ROTATE_270 = 3,
} bsp_display_rotation_t;

/** Logical direction in which the next transformed full-frame refresh is revealed. */
typedef enum {
    BSP_DISPLAY_SWEEP_LEFT,
    BSP_DISPLAY_SWEEP_RIGHT,
    BSP_DISPLAY_SWEEP_UP,
    BSP_DISPLAY_SWEEP_DOWN,
} bsp_display_sweep_t;

/**
 * @brief Create new display panel
 *
 * For maximum flexibility, this function performs only reset and initialization of the display.
 * You must turn on the display explicitly by calling esp_lcd_panel_disp_on_off().
 * The display's backlight is not turned on either. You can use bsp_display_backlight_on/off(),
 * bsp_display_brightness_set() (on supported boards) or implement your own backlight control.
 *
 * If you want to free resources allocated by this function, you can use esp_lcd API, ie.:
 *
 * \code{.c}
 * esp_lcd_panel_del(panel);
 * esp_lcd_panel_io_del(io);
 * spi_bus_free(spi_num_from_configuration);
 * \endcode
 *
 * @param[in]  config    display configuration
 * @param[out] ret_panel esp_lcd panel handle
 * @param[out] ret_io    esp_lcd IO handle
 * @return
 *      - ESP_OK         On success
 *      - Else           esp_lcd failure
 */
esp_err_t bsp_display_new(const bsp_display_config_t *config, esp_lcd_panel_handle_t *ret_panel, esp_lcd_panel_io_handle_t *ret_io);

/**
 * @brief Initialize display's brightness
 *
 * Brightness is controlled with PWM signal to a pin controlling backlight.
 *
 * @return
 *      - ESP_OK                On success
 *      - ESP_ERR_INVALID_ARG   Parameter error
 */
esp_err_t bsp_display_brightness_init(void);

esp_err_t bsp_display_rotation_set(bsp_display_rotation_t rotation);

/**
 * @brief Set the software display transform angle in tenths of a degree.
 *
 * The configured angle is always used, independent of external-power state,
 * while the panel remains in its native scan direction.
 */
esp_err_t bsp_display_transform_set_angle(int16_t angle_tenths);

/**
 * @brief Select the logical reveal direction for the next transformed refresh.
 *
 * The direction is consumed by one full-frame refresh and then returns to the
 * normal leftward page-forward direction.
 */
esp_err_t bsp_display_transform_set_next_sweep(bsp_display_sweep_t direction);

/**
 * @brief Force the next transformed flush to redraw the complete frame.
 *
 * This is used after a scroll operation. Scrolling moves already-rendered
 * pixels, so a rotated partial refresh can otherwise leave stale strips in
 * the newly exposed area. The forced refresh uses the sweep direction selected
 * by bsp_display_transform_set_next_sweep().
 */
void bsp_display_transform_force_full_refresh(void);

/**
 * @brief Set display's brightness
 *
 * Brightness is controlled with PWM signal to a pin controlling backlight.
 * Brightness must be already initialized by calling bsp_display_brightness_init() or bsp_display_new()
 *
 * @param[in] brightness_percent Brightness in [%]
 * @return
 *      - ESP_OK                On success
 *      - ESP_ERR_INVALID_ARG   Parameter error
 */
esp_err_t bsp_display_brightness_set(int brightness_percent);

int bsp_display_brightness_get(void);

/**
 * @brief Turn on display backlight
 *
 * Brightness is controlled with PWM signal to a pin controlling backlight.
 * Brightness must be already initialized by calling bsp_display_brightness_init() or bsp_display_new()
 *
 * @return
 *      - ESP_OK                On success
 *      - ESP_ERR_INVALID_ARG   Parameter error
 */
esp_err_t bsp_display_backlight_on(void);

/**
 * @brief Turn off display backlight
 *
 * Brightness is controlled with PWM signal to a pin controlling backlight.
 * Brightness must be already initialized by calling bsp_display_brightness_init() or bsp_display_new()
 *
 * @return
 *      - ESP_OK                On success
 *      - ESP_ERR_INVALID_ARG   Parameter error
 */
esp_err_t bsp_display_backlight_off(void);

/**
 * @brief Turn the AMOLED pixels off and place the CO5300 controller in sleep-in mode.
 *
 * The panel is reset and initialized again after ESP32 deep-sleep wakeup.
 */
esp_err_t bsp_display_enter_sleep(void);

#ifdef __cplusplus
}
#endif
