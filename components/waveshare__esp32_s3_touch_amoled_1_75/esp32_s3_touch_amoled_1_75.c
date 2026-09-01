#include <stdio.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_io_additions.h"

#include "esp_err.h"
#include "esp_log.h"
#include "esp_check.h"
#include "esp_attr.h"
#include "esp_vfs_fat.h"
#include "esp_spiffs.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "esp_heap_caps.h"

#include "esp_lcd_co5300.h"
#include "esp_lcd_touch_cst9217.h"

#include "esp_codec_dev_defaults.h"
#include "bsp/esp32_s3_touch_amoled_1_75.h"
#include "bsp_err_check.h"
#include "bsp/display.h"
#include "bsp/touch.h"

static const char *TAG = "ESP32-S3-Touch-AMOLED-1.75";

static i2c_master_bus_handle_t i2c_handle = NULL; // I2C Handle
static bool i2c_initialized = false;
static esp_io_expander_handle_t io_expander = NULL; // IO expander tca9554 handle
static lv_indev_t *disp_indev = NULL;
sdmmc_card_t *bsp_sdcard = NULL; // Global uSD card handler
static esp_lcd_touch_handle_t tp = NULL;
static esp_lcd_panel_handle_t panel_handle = NULL; // LCD panel handle
static esp_lcd_panel_io_handle_t io_handle = NULL;
uint8_t brightness;
static i2s_chan_handle_t i2s_tx_chan = NULL;
static i2s_chan_handle_t i2s_rx_chan = NULL;
static const audio_codec_data_if_t *i2s_data_if = NULL; /* Codec data interface */

#define LCD_TRANSFORM_TILE_SIZE 12
#define LCD_TRANSFORM_PIXELS (BSP_LCD_H_RES * BSP_LCD_V_RES)
#define LCD_TRANSFORM_CENTER_FP ((BSP_LCD_H_RES - 1) << 15)
#define LCD_TRANSFORM_RADIUS2 ((BSP_LCD_H_RES - 1) * (BSP_LCD_H_RES - 1))
#define LCD_TRANSFORM_ANGLE_DEFAULT_TENTHS 840
#define LCD_TRANSFORM_ANGLE_MIN_TENTHS 800
#define LCD_TRANSFORM_ANGLE_MAX_TENTHS 1000

typedef struct {
    uint16_t *logical_fb;
    uint16_t *dma_tile;
    SemaphoreHandle_t tile_done;
    lv_display_t *disp;
    volatile bool wait_intermediate;
    int16_t angle_tenths;
    int32_t cos_q16;
    int32_t sin_q16;
    bsp_display_sweep_t next_sweep;
    bool dirty_valid;
    int dirty_x_start;
    int dirty_y_start;
    int dirty_x_end;
    int dirty_y_end;
    bool force_full_refresh;
} lcd_transform_t;

static lcd_transform_t s_lcd_transform = {
    .angle_tenths = LCD_TRANSFORM_ANGLE_DEFAULT_TENTHS,
    .cos_q16 = 0,
    .sin_q16 = 65536,
    .next_sweep = BSP_DISPLAY_SWEEP_LEFT,
};

static inline uint16_t lcd_rgb565_from_wire(uint16_t value)
{
    return __builtin_bswap16(value);
}

static inline uint16_t lcd_rgb565_to_wire(uint16_t value)
{
    return __builtin_bswap16(value);
}

static void lcd_transform_set_coefficients(int16_t angle_tenths)
{
    if (angle_tenths == 900) {
        s_lcd_transform.cos_q16 = 0;
        s_lcd_transform.sin_q16 = 65536;
        return;
    }
    const float radians = (float)angle_tenths * 0.0017453292519943296f;
    s_lcd_transform.cos_q16 = (int32_t)lroundf(cosf(radians) * 65536.0f);
    s_lcd_transform.sin_q16 = (int32_t)lroundf(sinf(radians) * 65536.0f);
}

static bool IRAM_ATTR lcd_transform_color_done_cb(esp_lcd_panel_io_handle_t panel_io,
                                                   esp_lcd_panel_io_event_data_t *edata,
                                                   void *user_ctx)
{
    (void)panel_io;
    (void)edata;
    (void)user_ctx;
    if (s_lcd_transform.wait_intermediate) {
        BaseType_t need_yield = pdFALSE;
        xSemaphoreGiveFromISR(s_lcd_transform.tile_done, &need_yield);
        return need_yield == pdTRUE;
    }
    return s_lcd_transform.disp != NULL &&
           esp_lv_adapter_display_notify_color_trans_done_from_isr(s_lcd_transform.disp);
}

static esp_err_t lcd_transform_send_tile(esp_lcd_panel_handle_t panel,
                                         int x_start, int y_start,
                                         int x_end, int y_end,
                                         bool intermediate)
{
    /* CO5300 QSPI RGB565 writes must keep both address boundaries on two-pixel
       coordinates. An odd physical width shifts the packed row phase and
       produces the repeating diagonal/sawtooth corruption seen on partial
       clock, camera and text updates. */
    if (((x_start | y_start | x_end | y_end) & 1) != 0) {
        ESP_LOGE(TAG, "Unaligned transformed LCD tile: (%d,%d)-(%d,%d)",
                 x_start, y_start, x_end, y_end);
        return ESP_ERR_INVALID_ARG;
    }
    while (xSemaphoreTake(s_lcd_transform.tile_done, 0) == pdTRUE) {
    }
    s_lcd_transform.wait_intermediate = intermediate;
    esp_err_t ret = esp_lcd_panel_draw_bitmap(panel, x_start, y_start, x_end, y_end,
                                               s_lcd_transform.dma_tile);
    if (ret != ESP_OK) {
        s_lcd_transform.wait_intermediate = false;
        return ret;
    }
    if (intermediate && xSemaphoreTake(s_lcd_transform.tile_done, pdMS_TO_TICKS(500)) != pdTRUE) {
        s_lcd_transform.wait_intermediate = false;
        ESP_LOGE(TAG, "Timed out waiting for transformed LCD tile");
        return ESP_ERR_TIMEOUT;
    }
    return ESP_OK;
}

static void lcd_transform_copy_logical(int x_start, int y_start, int x_end, int y_end,
                                       const void *color_map)
{
    const uint16_t *source = (const uint16_t *)color_map;
    const int width = x_end - x_start;
    const int height = y_end - y_start;
    for (int row = 0; row < height; ++row) {
        uint16_t *destination = s_lcd_transform.logical_fb +
                                (size_t)(y_start + row) * BSP_LCD_H_RES + x_start;
        for (int column = 0; column < width; ++column) {
            destination[column] = lcd_rgb565_from_wire(source[(size_t)row * width + column]);
        }
    }
}

static inline uint16_t lcd_transform_sample_bilinear(int32_t source_x_fp, int32_t source_y_fp)
{
    const int32_t maximum_fp = (BSP_LCD_H_RES - 1) << 16;
    if (source_x_fp < 0 || source_y_fp < 0 ||
        source_x_fp > maximum_fp || source_y_fp > maximum_fp) {
        return 0;
    }

    int x0 = source_x_fp >> 16;
    int y0 = source_y_fp >> 16;
    int x1 = x0 < BSP_LCD_H_RES - 1 ? x0 + 1 : x0;
    int y1 = y0 < BSP_LCD_V_RES - 1 ? y0 + 1 : y0;
    uint32_t fx = (uint32_t)(source_x_fp >> 8) & 0xFFU;
    uint32_t fy = (uint32_t)(source_y_fp >> 8) & 0xFFU;
    if (x1 == x0) fx = 0;
    if (y1 == y0) fy = 0;

    const uint16_t p00 = s_lcd_transform.logical_fb[(size_t)y0 * BSP_LCD_H_RES + x0];
    const uint16_t p10 = s_lcd_transform.logical_fb[(size_t)y0 * BSP_LCD_H_RES + x1];
    const uint16_t p01 = s_lcd_transform.logical_fb[(size_t)y1 * BSP_LCD_H_RES + x0];
    const uint16_t p11 = s_lcd_transform.logical_fb[(size_t)y1 * BSP_LCD_H_RES + x1];
    const uint32_t w00 = (256U - fx) * (256U - fy);
    const uint32_t w10 = fx * (256U - fy);
    const uint32_t w01 = (256U - fx) * fy;
    const uint32_t w11 = fx * fy;

    const uint32_t red = (((p00 >> 11) * w00) + ((p10 >> 11) * w10) +
                          ((p01 >> 11) * w01) + ((p11 >> 11) * w11) + 32768U) >> 16;
    const uint32_t green = ((((p00 >> 5) & 0x3FU) * w00) + (((p10 >> 5) & 0x3FU) * w10) +
                            (((p01 >> 5) & 0x3FU) * w01) + (((p11 >> 5) & 0x3FU) * w11) + 32768U) >> 16;
    const uint32_t blue = (((p00 & 0x1FU) * w00) + ((p10 & 0x1FU) * w10) +
                           ((p01 & 0x1FU) * w01) + ((p11 & 0x1FU) * w11) + 32768U) >> 16;
    return (uint16_t)((red << 11) | (green << 5) | blue);
}

static void lcd_transform_render_area(int x_start, int y_start, int width, int height)
{
    const int32_t center_fp = LCD_TRANSFORM_CENTER_FP;
    const int32_t cos_q16 = s_lcd_transform.cos_q16;
    const int32_t sin_q16 = s_lcd_transform.sin_q16;
    size_t output_index = 0;

    for (int physical_y = y_start; physical_y < y_start + height; ++physical_y) {
        const int32_t dx_fp = (x_start << 16) - center_fp;
        const int32_t dy_fp = (physical_y << 16) - center_fp;
        int32_t source_x_fp = center_fp + (int32_t)(((int64_t)cos_q16 * dx_fp +
                                                     (int64_t)sin_q16 * dy_fp) >> 16);
        int32_t source_y_fp = center_fp + (int32_t)((-(int64_t)sin_q16 * dx_fp +
                                                     (int64_t)cos_q16 * dy_fp) >> 16);
        const int physical_dy2 = physical_y * 2 - (BSP_LCD_V_RES - 1);

        for (int physical_x = x_start; physical_x < x_start + width; ++physical_x) {
            const int physical_dx2 = physical_x * 2 - (BSP_LCD_H_RES - 1);
            uint16_t color = 0;
            if (physical_dx2 * physical_dx2 + physical_dy2 * physical_dy2 <=
                LCD_TRANSFORM_RADIUS2) {
                color = lcd_transform_sample_bilinear(source_x_fp, source_y_fp);
            }
            s_lcd_transform.dma_tile[output_index++] = lcd_rgb565_to_wire(color);
            source_x_fp += cos_q16;
            source_y_fp -= sin_q16;
        }
    }
    assert(output_index <= (size_t)BSP_LCD_H_RES * LCD_TRANSFORM_TILE_SIZE);
}

static esp_err_t lcd_transform_flush_rows(esp_lcd_panel_handle_t panel, bool reverse)
{
    int cursor = reverse ? BSP_LCD_V_RES : 0;
    while (reverse ? cursor > 0 : cursor < BSP_LCD_V_RES) {
        const int remaining = reverse ? cursor : BSP_LCD_V_RES - cursor;
        const int size = remaining < LCD_TRANSFORM_TILE_SIZE ? remaining : LCD_TRANSFORM_TILE_SIZE;
        const int y_start = reverse ? cursor - size : cursor;
        const int y_end = y_start + size;
        lcd_transform_render_area(0, y_start, BSP_LCD_H_RES, size);
        const bool intermediate = reverse ? y_start > 0 : y_end < BSP_LCD_V_RES;
        esp_err_t ret = lcd_transform_send_tile(panel, 0, y_start, BSP_LCD_H_RES, y_end,
                                                intermediate);
        if (ret != ESP_OK) return ret;
        cursor = reverse ? y_start : y_end;
    }
    return ESP_OK;
}

static esp_err_t lcd_transform_flush_columns(esp_lcd_panel_handle_t panel, bool reverse)
{
    int cursor = reverse ? BSP_LCD_H_RES : 0;
    while (reverse ? cursor > 0 : cursor < BSP_LCD_H_RES) {
        const int remaining = reverse ? cursor : BSP_LCD_H_RES - cursor;
        const int size = remaining < LCD_TRANSFORM_TILE_SIZE ? remaining : LCD_TRANSFORM_TILE_SIZE;
        const int x_start = reverse ? cursor - size : cursor;
        const int x_end = x_start + size;
        lcd_transform_render_area(x_start, 0, size, BSP_LCD_V_RES);
        const bool intermediate = reverse ? x_start > 0 : x_end < BSP_LCD_H_RES;
        esp_err_t ret = lcd_transform_send_tile(panel, x_start, 0, x_end, BSP_LCD_V_RES,
                                                intermediate);
        if (ret != ESP_OK) return ret;
        cursor = reverse ? x_start : x_end;
    }
    return ESP_OK;
}

static esp_err_t lcd_transform_flush_full(esp_lcd_panel_handle_t panel)
{
    /* At a dock angle near +90 degrees, logical horizontal motion maps to
       physical panel rows and logical vertical motion maps to columns. */
    const bsp_display_sweep_t sweep = s_lcd_transform.next_sweep;
    s_lcd_transform.next_sweep = BSP_DISPLAY_SWEEP_LEFT;
    switch (sweep) {
    case BSP_DISPLAY_SWEEP_RIGHT:
        return lcd_transform_flush_rows(panel, false);
    case BSP_DISPLAY_SWEEP_UP:
        return lcd_transform_flush_columns(panel, false);
    case BSP_DISPLAY_SWEEP_DOWN:
        return lcd_transform_flush_columns(panel, true);
    case BSP_DISPLAY_SWEEP_LEFT:
    default:
        return lcd_transform_flush_rows(panel, true);
    }
}

static void lcd_transform_accumulate_dirty(int x_start, int y_start, int x_end, int y_end)
{
    if (!s_lcd_transform.dirty_valid) {
        s_lcd_transform.dirty_x_start = x_start;
        s_lcd_transform.dirty_y_start = y_start;
        s_lcd_transform.dirty_x_end = x_end;
        s_lcd_transform.dirty_y_end = y_end;
        s_lcd_transform.dirty_valid = true;
        return;
    }
    if (x_start < s_lcd_transform.dirty_x_start) s_lcd_transform.dirty_x_start = x_start;
    if (y_start < s_lcd_transform.dirty_y_start) s_lcd_transform.dirty_y_start = y_start;
    if (x_end > s_lcd_transform.dirty_x_end) s_lcd_transform.dirty_x_end = x_end;
    if (y_end > s_lcd_transform.dirty_y_end) s_lcd_transform.dirty_y_end = y_end;
}

static void lcd_transform_logical_to_physical(int logical_x, int logical_y,
                                              int32_t *physical_x_fp,
                                              int32_t *physical_y_fp)
{
    const int32_t center_fp = LCD_TRANSFORM_CENTER_FP;
    const int32_t dx_fp = (logical_x << 16) - center_fp;
    const int32_t dy_fp = (logical_y << 16) - center_fp;
    *physical_x_fp = center_fp + (int32_t)(((int64_t)s_lcd_transform.cos_q16 * dx_fp -
                                            (int64_t)s_lcd_transform.sin_q16 * dy_fp) >> 16);
    *physical_y_fp = center_fp + (int32_t)(((int64_t)s_lcd_transform.sin_q16 * dx_fp +
                                            (int64_t)s_lcd_transform.cos_q16 * dy_fp) >> 16);
}

static esp_err_t lcd_transform_flush_dirty(esp_lcd_panel_handle_t panel)
{
    /* Bilinear sampling reads the next logical pixel, so include a two-pixel
       logical guard plus a small physical rounding guard around the rotated
       dirty rectangle. This keeps animated faces local without leaving stale
       glow pixels behind. */
    const int logical_x_start = s_lcd_transform.dirty_x_start > 2 ?
                                s_lcd_transform.dirty_x_start - 2 : 0;
    const int logical_y_start = s_lcd_transform.dirty_y_start > 2 ?
                                s_lcd_transform.dirty_y_start - 2 : 0;
    const int logical_x_end = s_lcd_transform.dirty_x_end < BSP_LCD_H_RES - 2 ?
                              s_lcd_transform.dirty_x_end + 2 : BSP_LCD_H_RES;
    const int logical_y_end = s_lcd_transform.dirty_y_end < BSP_LCD_V_RES - 2 ?
                              s_lcd_transform.dirty_y_end + 2 : BSP_LCD_V_RES;
    const int corners[4][2] = {
        {logical_x_start, logical_y_start}, {logical_x_end, logical_y_start},
        {logical_x_start, logical_y_end}, {logical_x_end, logical_y_end},
    };
    int32_t min_x_fp = INT32_MAX;
    int32_t min_y_fp = INT32_MAX;
    int32_t max_x_fp = INT32_MIN;
    int32_t max_y_fp = INT32_MIN;
    for (size_t index = 0; index < 4; ++index) {
        int32_t physical_x_fp;
        int32_t physical_y_fp;
        lcd_transform_logical_to_physical(corners[index][0], corners[index][1],
                                          &physical_x_fp, &physical_y_fp);
        if (physical_x_fp < min_x_fp) min_x_fp = physical_x_fp;
        if (physical_y_fp < min_y_fp) min_y_fp = physical_y_fp;
        if (physical_x_fp > max_x_fp) max_x_fp = physical_x_fp;
        if (physical_y_fp > max_y_fp) max_y_fp = physical_y_fp;
    }

    int x_start = (min_x_fp >> 16) - 2;
    int y_start = (min_y_fp >> 16) - 2;
    int x_end = ((max_x_fp + 0xFFFF) >> 16) + 2;
    int y_end = ((max_y_fp + 0xFFFF) >> 16) + 2;
    if (x_start < 0) x_start = 0;
    if (y_start < 0) y_start = 0;
    if (x_end > BSP_LCD_H_RES) x_end = BSP_LCD_H_RES;
    if (y_end > BSP_LCD_V_RES) y_end = BSP_LCD_V_RES;

    /* The LVGL rounder aligns the logical invalidation, but arbitrary-angle
       rotation can turn its physical bounding box odd again. Align the final
       exclusive rectangle here, immediately before CO5300 address-window
       programming. BSP_LCD_*_RES is even, so clamping preserves alignment. */
    x_start &= ~1;
    y_start &= ~1;
    x_end = (x_end + 1) & ~1;
    y_end = (y_end + 1) & ~1;
    if (x_end > BSP_LCD_H_RES) x_end = BSP_LCD_H_RES;
    if (y_end > BSP_LCD_V_RES) y_end = BSP_LCD_V_RES;
    if (x_start >= x_end || y_start >= y_end) return ESP_OK;

    for (int cursor = y_start; cursor < y_end;) {
        const int size = y_end - cursor < LCD_TRANSFORM_TILE_SIZE ?
                         y_end - cursor : LCD_TRANSFORM_TILE_SIZE;
        const int tile_end = cursor + size;
        lcd_transform_render_area(x_start, cursor, x_end - x_start, size);
        esp_err_t ret = lcd_transform_send_tile(panel, x_start, cursor, x_end, tile_end,
                                                tile_end < y_end);
        if (ret != ESP_OK) return ret;
        cursor = tile_end;
    }
    return ESP_OK;
}

static esp_err_t lcd_transform_draw_bitmap(lv_display_t *disp,
                                           esp_lcd_panel_handle_t panel,
                                           int x_start, int y_start, int x_end, int y_end,
                                           const void *color_map, void *user_ctx)
{
    (void)user_ctx;
    if (s_lcd_transform.logical_fb == NULL || s_lcd_transform.dma_tile == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    lcd_transform_copy_logical(x_start, y_start, x_end, y_end, color_map);
    lcd_transform_accumulate_dirty(x_start, y_start, x_end, y_end);
    if (!lv_display_flush_is_last(disp)) {
        return ESP_ERR_NOT_ALLOWED;
    }
    const bool force_full_refresh = s_lcd_transform.force_full_refresh;
    s_lcd_transform.force_full_refresh = false;
    const bool full_refresh = s_lcd_transform.dirty_valid &&
                              s_lcd_transform.dirty_x_start <= 0 &&
                              s_lcd_transform.dirty_y_start <= 0 &&
                              s_lcd_transform.dirty_x_end >= BSP_LCD_H_RES &&
                              s_lcd_transform.dirty_y_end >= BSP_LCD_V_RES;
    esp_err_t ret = force_full_refresh ? lcd_transform_flush_full(panel) :
                    full_refresh ? lcd_transform_flush_full(panel) :
                                   lcd_transform_flush_dirty(panel);
    s_lcd_transform.dirty_valid = false;
    return ret;
}

static esp_err_t lcd_transform_touch_read(esp_lcd_touch_handle_t touch,
                                          esp_lcd_touch_point_data_t *points,
                                          uint8_t *count, uint8_t max_count,
                                          void *user_ctx)
{
    (void)user_ctx;
    esp_err_t ret = esp_lcd_touch_read_data(touch);
    if (ret != ESP_OK) return ret;
    ret = esp_lcd_touch_get_data(touch, points, count, max_count);
    if (ret != ESP_OK) return ret;

    const int32_t center_fp = LCD_TRANSFORM_CENTER_FP;
    const int32_t cos_q16 = s_lcd_transform.cos_q16;
    const int32_t sin_q16 = s_lcd_transform.sin_q16;
    for (uint8_t index = 0; index < *count; ++index) {
        const int32_t physical_x_fp = (points[index].x << 16) - center_fp;
        const int32_t physical_y_fp = (points[index].y << 16) - center_fp;
        int32_t logical_x = (center_fp + (int32_t)(((int64_t)cos_q16 * physical_x_fp +
                                                    (int64_t)sin_q16 * physical_y_fp) >> 16) + 32768) >> 16;
        int32_t logical_y = (center_fp + (int32_t)((-(int64_t)sin_q16 * physical_x_fp +
                                                    (int64_t)cos_q16 * physical_y_fp) >> 16) + 32768) >> 16;
        if (logical_x < 0) logical_x = 0;
        if (logical_x >= BSP_LCD_H_RES) logical_x = BSP_LCD_H_RES - 1;
        if (logical_y < 0) logical_y = 0;
        if (logical_y >= BSP_LCD_V_RES) logical_y = BSP_LCD_V_RES - 1;
        points[index].x = logical_x;
        points[index].y = logical_y;
    }
    return ESP_OK;
}

#define BSP_ES7210_CODEC_ADDR ES7210_CODEC_DEFAULT_ADDR
#define BSP_I2S_GPIO_CFG       \
    {                          \
        .mclk = BSP_I2S_MCLK,  \
        .bclk = BSP_I2S_SCLK,  \
        .ws = BSP_I2S_LCLK,    \
        .dout = BSP_I2S_DOUT,  \
        .din = BSP_I2S_DSIN,   \
        .invert_flags = {      \
            .mclk_inv = false, \
            .bclk_inv = false, \
            .ws_inv = false,   \
        },                     \
    }

static const co5300_lcd_init_cmd_t lcd_init_cmds[] = {
    {0xFE, (uint8_t[]){0x20}, 1, 0},
    {0x19, (uint8_t[]){0x10}, 1, 0},
    {0x1C, (uint8_t[]){0xA0}, 1, 0},

    {0xFE, (uint8_t[]){0x00}, 1, 0},
    {0xC4, (uint8_t[]){0x80}, 1, 0},
    {0x3A, (uint8_t[]){0x55}, 1, 0},
    {0x35, (uint8_t[]){0x00}, 1, 0},
    {0x53, (uint8_t[]){0x20}, 1, 0},
    {0x51, (uint8_t[]){0xFF}, 1, 0},
    {0x63, (uint8_t[]){0xFF}, 1, 0},
    {0x2A, (uint8_t[]){0x00, 0x06, 0x01, 0xD7}, 4, 0},
    {0x2B, (uint8_t[]){0x00, 0x00, 0x01, 0xD1}, 4, 600},
    {0x11, NULL, 0, 600},
    {0x29, NULL, 0, 0},
};

#define BSP_I2S_DUPLEX_MONO_CFG(_sample_rate)                                                         \
    {                                                                                                 \
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(_sample_rate),                                          \
        .slot_cfg = I2S_STD_PHILIP_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO), \
        .gpio_cfg = BSP_I2S_GPIO_CFG,                                                                 \
    }

/**************************************************************************************************
 *
 * I2C Function
 *
 **************************************************************************************************/
esp_err_t bsp_i2c_init(void)
{
    /* I2C was initialized before */
    if (i2c_initialized)
    {
        return ESP_OK;
    }

    i2c_master_bus_config_t i2c_bus_conf = {
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .sda_io_num = BSP_I2C_SDA,
        .scl_io_num = BSP_I2C_SCL,
        .i2c_port = BSP_I2C_NUM,
    };
    BSP_ERROR_CHECK_RETURN_ERR(i2c_new_master_bus(&i2c_bus_conf, &i2c_handle));

    i2c_initialized = true;

    return ESP_OK;
}

esp_err_t bsp_i2c_deinit(void)
{
    BSP_ERROR_CHECK_RETURN_ERR(i2c_del_master_bus(i2c_handle));
    i2c_initialized = false;
    return ESP_OK;
}

i2c_master_bus_handle_t bsp_i2c_get_handle(void)
{
    bsp_i2c_init();
    return i2c_handle;
}

esp_err_t bsp_spiffs_mount(void)
{
    esp_vfs_spiffs_conf_t conf = {
        .base_path = CONFIG_BSP_SPIFFS_MOUNT_POINT,
        .partition_label = CONFIG_BSP_SPIFFS_PARTITION_LABEL,
        .max_files = CONFIG_BSP_SPIFFS_MAX_FILES,
#ifdef CONFIG_BSP_SPIFFS_FORMAT_ON_MOUNT_FAIL
        .format_if_mount_failed = true,
#else
        .format_if_mount_failed = false,
#endif
    };

    esp_err_t ret_val = esp_vfs_spiffs_register(&conf);

    BSP_ERROR_CHECK_RETURN_ERR(ret_val);

    size_t total = 0, used = 0;
    ret_val = esp_spiffs_info(conf.partition_label, &total, &used);
    if (ret_val != ESP_OK)
    {
        ESP_LOGE(TAG, "Failed to get SPIFFS partition information (%s)", esp_err_to_name(ret_val));
    }
    else
    {
        ESP_LOGI(TAG, "Partition size: total: %d, used: %d", total, used);
    }

    return ret_val;
}

esp_err_t bsp_spiffs_unmount(void)
{
    return esp_vfs_spiffs_unregister(CONFIG_BSP_SPIFFS_PARTITION_LABEL);
}

esp_err_t bsp_sdcard_mount(void)
{
    const esp_vfs_fat_sdmmc_mount_config_t mount_config = {
#ifdef CONFIG_BSP_SD_FORMAT_ON_MOUNT_FAIL
        .format_if_mount_failed = true,
#else
        .format_if_mount_failed = false,
#endif
        .max_files = 5,
        .allocation_unit_size = 16 * 1024};

    const sdmmc_host_t host = SDMMC_HOST_DEFAULT();
    const sdmmc_slot_config_t slot_config = {
        .clk = BSP_SD_CLK,
        .cmd = BSP_SD_CMD,
        .d0 = BSP_SD_D0,
        .d1 = GPIO_NUM_NC,
        .d2 = GPIO_NUM_NC,
        .d3 = GPIO_NUM_NC,
        .d4 = GPIO_NUM_NC,
        .d5 = GPIO_NUM_NC,
        .d6 = GPIO_NUM_NC,
        .d7 = GPIO_NUM_NC,
        .cd = SDMMC_SLOT_NO_CD,
        .wp = SDMMC_SLOT_NO_WP,
        .width = 1,
        .flags = 0,
    };

#if !CONFIG_FATFS_LONG_FILENAMES
    ESP_LOGW(TAG, "Warning: Long filenames on SD card are disabled in menuconfig!");
#endif

    return esp_vfs_fat_sdmmc_mount(BSP_SD_MOUNT_POINT, &host, &slot_config, &mount_config, &bsp_sdcard);
}

esp_err_t bsp_sdcard_unmount(void)
{
    return esp_vfs_fat_sdcard_unmount(BSP_SD_MOUNT_POINT, bsp_sdcard);
}

/**************************************************************************************************
 *
 * I2S Audio Function
 *
 **************************************************************************************************/
esp_err_t bsp_audio_init(const i2s_std_config_t *i2s_config)
{
    esp_err_t ret = ESP_FAIL;
    if (i2s_tx_chan && i2s_rx_chan) {
        /* Audio was initialized before */
        return ESP_OK;
    }

    /* Setup I2S peripheral */
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(CONFIG_BSP_I2S_NUM, I2S_ROLE_MASTER);
    chan_cfg.auto_clear = true; // Auto clear the legacy data in the DMA buffer
    BSP_ERROR_CHECK_RETURN_ERR(i2s_new_channel(&chan_cfg, &i2s_tx_chan, &i2s_rx_chan));

    /* Setup I2S channels */
    const i2s_std_config_t std_cfg_default = BSP_I2S_DUPLEX_MONO_CFG(22050);
    const i2s_std_config_t *p_i2s_cfg = &std_cfg_default;
    if (i2s_config != NULL) {
        p_i2s_cfg = i2s_config;
    }

    if (i2s_tx_chan != NULL) {
        ESP_GOTO_ON_ERROR(i2s_channel_init_std_mode(i2s_tx_chan, p_i2s_cfg), err, TAG, "I2S channel initialization failed");
        ESP_GOTO_ON_ERROR(i2s_channel_enable(i2s_tx_chan), err, TAG, "I2S enabling failed");
    }
    if (i2s_rx_chan != NULL) {
        ESP_GOTO_ON_ERROR(i2s_channel_init_std_mode(i2s_rx_chan, p_i2s_cfg), err, TAG, "I2S channel initialization failed");
        ESP_GOTO_ON_ERROR(i2s_channel_enable(i2s_rx_chan), err, TAG, "I2S enabling failed");
    }

    audio_codec_i2s_cfg_t i2s_cfg = {
        .port = CONFIG_BSP_I2S_NUM,
        .rx_handle = i2s_rx_chan,
        .tx_handle = i2s_tx_chan,
    };
    i2s_data_if = audio_codec_new_i2s_data(&i2s_cfg);
    BSP_NULL_CHECK_GOTO(i2s_data_if, err);

    return ESP_OK;

err:
    if (i2s_tx_chan) {
        i2s_del_channel(i2s_tx_chan);
    }
    if (i2s_rx_chan) {
        i2s_del_channel(i2s_rx_chan);
    }

    return ret;
}

esp_codec_dev_handle_t bsp_audio_codec_speaker_init(void)
{
    if (i2s_data_if == NULL) {
        /* Initilize I2C */
        BSP_ERROR_CHECK_RETURN_NULL(bsp_i2c_init());
        /* Configure I2S peripheral and Power Amplifier */
        BSP_ERROR_CHECK_RETURN_NULL(bsp_audio_init(NULL));
    }
    assert(i2s_data_if);

    const audio_codec_gpio_if_t *gpio_if = audio_codec_new_gpio();

    audio_codec_i2c_cfg_t i2c_cfg = {
        .port = BSP_I2C_NUM,
        .addr = ES8311_CODEC_DEFAULT_ADDR,
        .bus_handle = i2c_handle,
    };
    const audio_codec_ctrl_if_t *i2c_ctrl_if = audio_codec_new_i2c_ctrl(&i2c_cfg);
    BSP_NULL_CHECK(i2c_ctrl_if, NULL);

    esp_codec_dev_hw_gain_t gain = {
        .pa_voltage = 5.0,
        .codec_dac_voltage = 3.3,
    };

    es8311_codec_cfg_t es8311_cfg = {
        .ctrl_if = i2c_ctrl_if,
        .gpio_if = gpio_if,
        .codec_mode = ESP_CODEC_DEV_WORK_MODE_DAC,
        .pa_pin = BSP_POWER_AMP_IO,
        .pa_reverted = false,
        .master_mode = false,
        .use_mclk = true,
        .digital_mic = false,
        .invert_mclk = false,
        .invert_sclk = false,
        .hw_gain = gain,
    };
    const audio_codec_if_t *es8311_dev = es8311_codec_new(&es8311_cfg);
    BSP_NULL_CHECK(es8311_dev, NULL);

    esp_codec_dev_cfg_t codec_dev_cfg = {
        .dev_type = ESP_CODEC_DEV_TYPE_OUT,
        .codec_if = es8311_dev,
        .data_if = i2s_data_if,
    };
    return esp_codec_dev_new(&codec_dev_cfg);
}

esp_codec_dev_handle_t bsp_audio_codec_microphone_init(void)
{
    if (i2s_data_if == NULL) {
        /* Initilize I2C */
        BSP_ERROR_CHECK_RETURN_NULL(bsp_i2c_init());
        /* Configure I2S peripheral and Power Amplifier */
        BSP_ERROR_CHECK_RETURN_NULL(bsp_audio_init(NULL));
    }
    assert(i2s_data_if);

    audio_codec_i2c_cfg_t i2c_cfg = {
        .port = BSP_I2C_NUM,
        .addr = BSP_ES7210_CODEC_ADDR,
        .bus_handle = i2c_handle,
    };
    const audio_codec_ctrl_if_t *i2c_ctrl_if = audio_codec_new_i2c_ctrl(&i2c_cfg);
    BSP_NULL_CHECK(i2c_ctrl_if, NULL);

    es7210_codec_cfg_t es7210_cfg = {
        .ctrl_if = i2c_ctrl_if,
    };
    const audio_codec_if_t *es7210_dev = es7210_codec_new(&es7210_cfg);
    BSP_NULL_CHECK(es7210_dev, NULL);

    esp_codec_dev_cfg_t codec_es7210_dev_cfg = {
        .dev_type = ESP_CODEC_DEV_TYPE_IN,
        .codec_if = es7210_dev,
        .data_if = i2s_data_if,
    };
    return esp_codec_dev_new(&codec_es7210_dev_cfg);
}

#define LCD_CMD_BITS (8)
#define LCD_PARAM_BITS (8)
#define LCD_LEDC_CH (CONFIG_BSP_DISPLAY_BRIGHTNESS_LEDC_CH)
#define LVGL_TICK_PERIOD_MS (CONFIG_BSP_DISPLAY_LVGL_TICK)
#define LVGL_MAX_SLEEP_MS (CONFIG_BSP_DISPLAY_LVGL_MAX_SLEEP)

esp_err_t bsp_display_brightness_init(void)
{
    bsp_display_brightness_set(100);
    return ESP_OK;
}

esp_err_t bsp_display_brightness_set(int brightness_percent)
{
    if (panel_handle == NULL)
    {
        ESP_LOGE(TAG, "Panel handle is not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    if (brightness_percent < 0 || brightness_percent > 100)
    {
        ESP_LOGE(TAG, "Invalid brightness percentage. Should be between 0 and 100.");
        return ESP_ERR_INVALID_ARG;
    }

    brightness = (uint8_t)(brightness_percent * 255 / 100);

    uint32_t lcd_cmd = 0x51;
    lcd_cmd &= 0xff;
    lcd_cmd <<= 8;
    lcd_cmd |= 0x02 << 24;
    uint8_t param = brightness;
    esp_lcd_panel_io_tx_param(io_handle, lcd_cmd, &param, 1);

    return ESP_OK;
}

int bsp_display_brightness_get(void)
{
    if (panel_handle == NULL)
    {
        ESP_LOGE(TAG, "Panel handle is not initialized");
        return -1;
    }

    return brightness * 100 / 255;
}

esp_err_t bsp_display_backlight_off(void)
{
    ESP_LOGI(TAG, "Backlight off");
    return bsp_display_brightness_set(0);
}

esp_err_t bsp_display_backlight_on(void)
{
    ESP_LOGI(TAG, "Backlight on");
    return bsp_display_brightness_set(100);
}

esp_err_t bsp_display_enter_sleep(void)
{
    if (panel_handle == NULL || io_handle == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    ESP_RETURN_ON_ERROR(bsp_display_brightness_set(0), TAG, "set AMOLED brightness failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_disp_on_off(panel_handle, false), TAG, "display off failed");

    /* CO5300 uses the QSPI command envelope used elsewhere in this BSP.
       0x10 is the MIPI-DCS sleep-in command; a reset on the next boot performs
       the matching sleep-out sequence. */
    uint32_t sleep_in = (0x02U << 24) | (0x10U << 8);
    ESP_RETURN_ON_ERROR(esp_lcd_panel_io_tx_param(io_handle, sleep_in, NULL, 0),
                        TAG, "AMOLED sleep-in failed");
    vTaskDelay(pdMS_TO_TICKS(120));
    return ESP_OK;
}
#if LVGL_VERSION_MAJOR >= 9
static void rounder_event_cb(lv_event_t *e)
{
    lv_area_t *area = (lv_area_t *)lv_event_get_param(e);
    uint16_t x1 = area->x1;
    uint16_t x2 = area->x2;

    uint16_t y1 = area->y1;
    uint16_t y2 = area->y2;

    // round the start of coordinate down to the nearest 2M number
    area->x1 = (x1 >> 1) << 1;
    area->y1 = (y1 >> 1) << 1;
    // round the end of coordinate up to the nearest 2N+1 number
    area->x2 = ((x2 >> 1) << 1) + 1;
    area->y2 = ((y2 >> 1) << 1) + 1;
}
#else
static void bsp_lvgl_rounder_cb(lv_disp_drv_t *disp_drv, lv_area_t *area)
{
    uint16_t x1 = area->x1;
    uint16_t x2 = area->x2;

    uint16_t y1 = area->y1;
    uint16_t y2 = area->y2;

    // round the start of coordinate down to the nearest 2M number
    area->x1 = (x1 >> 1) << 1;
    area->y1 = (y1 >> 1) << 1;
    // round the end of coordinate up to the nearest 2N+1 number
    area->x2 = ((x2 >> 1) << 1) + 1;
    area->y2 = ((y2 >> 1) << 1) + 1;
}
#endif
esp_err_t bsp_display_new(const bsp_display_config_t *config, esp_lcd_panel_handle_t *ret_panel, esp_lcd_panel_io_handle_t *ret_io)
{
    esp_err_t ret = ESP_OK;
    assert(config != NULL && config->max_transfer_sz > 0);

    ESP_LOGI(TAG, "Initialize SPI bus");
    const spi_bus_config_t buscfg = CO5300_PANEL_BUS_QSPI_CONFIG(BSP_LCD_PCLK,
                                                                 BSP_LCD_DATA0,
                                                                 BSP_LCD_DATA1,
                                                                 BSP_LCD_DATA2,
                                                                 BSP_LCD_DATA3,
                                                                 config->max_transfer_sz);
    ESP_ERROR_CHECK(spi_bus_initialize(BSP_LCD_SPI_NUM, &buscfg, SPI_DMA_CH_AUTO));

    esp_lcd_panel_io_spi_config_t io_config = CO5300_PANEL_IO_QSPI_CONFIG(BSP_LCD_CS, NULL, NULL);
    io_config.trans_queue_depth = CONFIG_BSP_LCD_TRANS_QUEUE_DEPTH;
    co5300_vendor_config_t vendor_config = {
        .init_cmds = lcd_init_cmds,
        .init_cmds_size = sizeof(lcd_init_cmds) / sizeof(lcd_init_cmds[0]),
        .flags = {
            .use_qspi_interface = 1,
        },
    };
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)BSP_LCD_SPI_NUM, &io_config, &io_handle));
    const esp_lcd_panel_dev_config_t panel_config = {
        .reset_gpio_num = BSP_LCD_RST,
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .bits_per_pixel = BSP_LCD_BITS_PER_PIXEL,
        .vendor_config = &vendor_config,
    };
    ESP_ERROR_CHECK(esp_lcd_new_panel_co5300(io_handle, &panel_config, &panel_handle));
    esp_lcd_panel_set_gap(panel_handle, 0x06, 0);
    esp_lcd_panel_reset(panel_handle);
    esp_lcd_panel_init(panel_handle);
    esp_lcd_panel_disp_on_off(panel_handle, true);

    if (ret_panel)
    {
        *ret_panel = panel_handle;
    }
    if (ret_io)
    {
        *ret_io = io_handle;
    }
    return ret;
}

esp_err_t bsp_touch_new(const bsp_display_cfg_t *cfg, esp_lcd_touch_handle_t *ret_touch)
{
    assert(cfg != NULL);
    /* Initilize I2C */
    BSP_ERROR_CHECK_RETURN_ERR(bsp_i2c_init());

    /* Initialize touch */
    const esp_lcd_touch_config_t tp_cfg = {
        .x_max = BSP_LCD_H_RES,
        .y_max = BSP_LCD_V_RES,
        .rst_gpio_num = BSP_LCD_TOUCH_RST, // Shared with LCD reset
        .int_gpio_num = BSP_LCD_TOUCH_INT,
        .levels = {
            .reset = 0,
            .interrupt = 0,
        },
        .flags = {
            .swap_xy = cfg->touch_flags.swap_xy,
            .mirror_x = cfg->touch_flags.mirror_x,
            .mirror_y = cfg->touch_flags.mirror_y,
        },
    };
    esp_lcd_panel_io_handle_t tp_io_handle = NULL;
    esp_lcd_panel_io_i2c_config_t tp_io_config = ESP_LCD_TOUCH_IO_I2C_CST9217_CONFIG();
    tp_io_config.scl_speed_hz = CONFIG_BSP_I2C_CLK_SPEED_HZ;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_i2c(i2c_handle, &tp_io_config, &tp_io_handle), TAG, "");
    return esp_lcd_touch_new_i2c_cst9217(tp_io_handle, &tp_cfg, ret_touch);
}

/**************************************************************************************************
 *
 * IO Expander Function
 *
 **************************************************************************************************/
esp_io_expander_handle_t bsp_io_expander_init(void)
{
    BSP_ERROR_CHECK_RETURN_ERR(bsp_i2c_init());
    if (!io_expander)
    {
        BSP_ERROR_CHECK_RETURN_NULL(esp_io_expander_new_i2c_tca9554(i2c_handle, BSP_IO_EXPANDER_I2C_ADDRESS, &io_expander));
    }
    return io_expander;
}

static lv_display_t *bsp_display_lcd_init(const bsp_display_cfg_t *cfg)
{
    assert(cfg != NULL);
    const bsp_display_config_t disp_config = {
        .max_transfer_sz = BSP_LCD_H_RES * BSP_LCD_V_RES * BSP_LCD_BITS_PER_PIXEL / 8,
    };

    BSP_ERROR_CHECK_RETURN_NULL(bsp_display_new(&disp_config, &panel_handle, &io_handle));

    ESP_LOGD(TAG, "Add LCD screen");
    esp_lv_adapter_display_config_t disp_cfg = {
        .panel = panel_handle,
        .panel_io = io_handle,
        .profile = {
            .interface = ESP_LV_ADAPTER_PANEL_IF_OTHER,
            .rotation = cfg->rotation,
            .hor_res = BSP_LCD_H_RES,
            .ver_res = BSP_LCD_V_RES,
            /* QSPI DMA cannot transmit directly from PSRAM on ESP32-S3. Two
               12-line LVGL buffers plus one 12-line transform tile use about
               33 KiB and preserve the prior Wi-Fi internal-memory budget. */
            .buffer_height = LCD_TRANSFORM_TILE_SIZE,
            .use_psram = false,
            .enable_ppa_accel = false,
            .require_double_buffer = true,
        },
        .tear_avoid_mode = cfg->tear_avoid_mode,
    };

    lv_display_t *disp = esp_lv_adapter_register_display(&disp_cfg);
    if (!disp)
    {
        return NULL;
    }

    const esp_lv_adapter_draw_bitmap_callbacks_t draw_callbacks = {
        .custom_draw_bitmap = lcd_transform_draw_bitmap,
    };
    ESP_ERROR_CHECK(esp_lv_adapter_set_draw_bitmap_callbacks(disp, &draw_callbacks, NULL));

#if LVGL_VERSION_MAJOR >= 9
    lv_display_add_event_cb(disp, rounder_event_cb, LV_EVENT_INVALIDATE_AREA, NULL);
#else
    lv_disp_t *disp_v8 = (lv_disp_t *)disp;
    if (disp_v8 && disp_v8->driver)
    {
        disp_v8->driver->rounder_cb = bsp_lvgl_rounder_cb;
    }
#endif

    return disp;
}

static lv_indev_t *bsp_display_indev_init(const bsp_display_cfg_t *cfg, lv_display_t *disp)
{
    assert(cfg != NULL);
    BSP_ERROR_CHECK_RETURN_NULL(bsp_touch_new(cfg, &tp));
    assert(tp);

    esp_lv_adapter_touch_config_t touch_cfg = ESP_LV_ADAPTER_TOUCH_DEFAULT_CONFIG(disp, tp);
    touch_cfg.callbacks.custom_touch_read = lcd_transform_touch_read;

    return esp_lv_adapter_register_touch(&touch_cfg);
}
/**********************************************************************************************************
 *
 * Display Function
 *
 **********************************************************************************************************/
lv_display_t *bsp_display_start(void)
{
    bsp_display_cfg_t cfg = {
        .lv_adapter_cfg = ESP_LV_ADAPTER_DEFAULT_CONFIG(),
        .rotation = ESP_LV_ADAPTER_ROTATE_0,
        .tear_avoid_mode = ESP_LV_ADAPTER_TEAR_AVOID_MODE_NONE,
        .touch_flags = {
            .swap_xy = 0,
            .mirror_x = 1,
            .mirror_y = 1}};
    return bsp_display_start_with_config(&cfg);
}


lv_display_t *bsp_display_start_with_config(bsp_display_cfg_t *cfg)
{
    lv_display_t *disp;

    assert(cfg != NULL);
    BSP_ERROR_CHECK_RETURN_NULL(esp_lv_adapter_init(&cfg->lv_adapter_cfg));

    s_lcd_transform.logical_fb = heap_caps_calloc(LCD_TRANSFORM_PIXELS, sizeof(uint16_t),
                                                   MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    s_lcd_transform.dma_tile = heap_caps_malloc(BSP_LCD_H_RES * LCD_TRANSFORM_TILE_SIZE *
                                                sizeof(uint16_t),
                                                MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
    s_lcd_transform.tile_done = xSemaphoreCreateBinary();
    if (s_lcd_transform.logical_fb == NULL || s_lcd_transform.dma_tile == NULL ||
        s_lcd_transform.tile_done == NULL) {
        ESP_LOGE(TAG, "Unable to allocate software display transform buffers");
        return NULL;
    }
    lcd_transform_set_coefficients(s_lcd_transform.angle_tenths);
    ESP_ERROR_CHECK(esp_lv_adapter_set_default_display_idf_callback_registration_enabled(false));

    BSP_NULL_CHECK(disp = bsp_display_lcd_init(cfg), NULL);

    s_lcd_transform.disp = disp;
    const esp_lcd_panel_io_callbacks_t io_callbacks = {
        .on_color_trans_done = lcd_transform_color_done_cb,
    };
    ESP_ERROR_CHECK(esp_lcd_panel_io_register_event_callbacks(io_handle, &io_callbacks, NULL));

    BSP_NULL_CHECK(disp_indev = bsp_display_indev_init(cfg, disp), NULL);

    /* Keep CO5300 in its documented native scan direction. All user-visible
       rotation is performed in software, avoiding the controller's undefined
       MADCTL axis-swap behaviour and edge aliasing. */
    BSP_ERROR_CHECK_RETURN_NULL(bsp_display_rotation_set(BSP_DISPLAY_ROTATE_0));
    lv_obj_t *boot_screen = lv_display_get_screen_active(disp);
    lv_obj_set_style_bg_color(boot_screen, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(boot_screen, LV_OPA_COVER, 0);

    BSP_ERROR_CHECK_RETURN_NULL(bsp_display_brightness_init());

    ESP_ERROR_CHECK(esp_lv_adapter_start());

    return disp;
}

lv_indev_t *bsp_display_get_input_dev(void)
{
    return disp_indev;
}

esp_err_t bsp_display_rotation_set(bsp_display_rotation_t rotation)
{
    if (panel_handle == NULL || io_handle == NULL)
    {
        ESP_LOGE(TAG, "Panel or IO handle is not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    uint8_t madctl = 0x00;
    bool touch_swap_xy = false;
    bool touch_mirror_x = true;
    bool touch_mirror_y = true;

    switch (rotation)
    {
    case BSP_DISPLAY_ROTATE_0:
        madctl = 0x00;
        break;
    case BSP_DISPLAY_ROTATE_90:
        madctl = 0x60;
        touch_swap_xy = true;
        touch_mirror_x = false;
        touch_mirror_y = true;
        break;
    case BSP_DISPLAY_ROTATE_180:
        madctl = 0xC0;
        touch_mirror_x = false;
        touch_mirror_y = false;
        break;
    case BSP_DISPLAY_ROTATE_270:
        madctl = 0xA0;
        touch_swap_xy = true;
        touch_mirror_x = true;
        touch_mirror_y = false;
        break;
    default:
        ESP_LOGE(TAG, "Invalid rotation value: %d", rotation);
        return ESP_ERR_INVALID_ARG;
    }

    ESP_LOGI(TAG, "Set panel scan direction: %d (MADCTL=0x%02X, native gap=6,0)",
             rotation, madctl);

    /* Production starts this helper with ROTATE_0 and keeps the panel's native
       (6,0) window. Non-zero cases remain available only for bounded hardware
       diagnostics; user-visible rotation is handled by the software compositor. */
    uint32_t lcd_cmd = 0x36;
    lcd_cmd &= 0xff;
    lcd_cmd <<= 8;
    lcd_cmd |= 0x02 << 24;
    ESP_RETURN_ON_ERROR(esp_lcd_panel_io_tx_param(io_handle, lcd_cmd, &madctl, 1),
                        TAG, "MADCTL write failed");
    if (tp == NULL) return ESP_OK;

    /* Keep the touch coordinate space aligned with the panel's MADCTL
       transform.  LVGL remains at rotation 0 so its partial invalidation
       rectangles and the panel address window always describe the same area. */
    ESP_RETURN_ON_ERROR(esp_lcd_touch_set_swap_xy(tp, touch_swap_xy), TAG, "touch swap failed");
    ESP_RETURN_ON_ERROR(esp_lcd_touch_set_mirror_x(tp, touch_mirror_x), TAG, "touch mirror X failed");
    ESP_RETURN_ON_ERROR(esp_lcd_touch_set_mirror_y(tp, touch_mirror_y), TAG, "touch mirror Y failed");
    return ESP_OK;
}

esp_err_t bsp_display_transform_set_angle(int16_t angle_tenths)
{
    if (angle_tenths < LCD_TRANSFORM_ANGLE_MIN_TENTHS ||
        angle_tenths > LCD_TRANSFORM_ANGLE_MAX_TENTHS) {
        return ESP_ERR_INVALID_ARG;
    }
    const bool changed = s_lcd_transform.angle_tenths != angle_tenths;
    s_lcd_transform.angle_tenths = angle_tenths;
    lcd_transform_set_coefficients(angle_tenths);
    if (changed) {
        ESP_LOGI(TAG, "Display transform angle=%.1f deg", angle_tenths / 10.0);
    }
    return ESP_OK;
}

esp_err_t bsp_display_transform_set_next_sweep(bsp_display_sweep_t direction)
{
    if (direction < BSP_DISPLAY_SWEEP_LEFT || direction > BSP_DISPLAY_SWEEP_DOWN) {
        return ESP_ERR_INVALID_ARG;
    }
    s_lcd_transform.next_sweep = direction;
    return ESP_OK;
}

void bsp_display_transform_force_full_refresh(void)
{
    s_lcd_transform.force_full_refresh = true;
}

esp_err_t bsp_display_lock(uint32_t timeout_ms)
{
    return esp_lv_adapter_lock(timeout_ms);
}

void bsp_display_unlock(void)
{
    esp_lv_adapter_unlock();
}
