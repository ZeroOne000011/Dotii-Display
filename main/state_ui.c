#include "state_ui.h"

#include <stdio.h>
#include <string.h>
#include <time.h>

#include "app_state.h"
#include "board_input.h"
#include "bsp/display.h"
#include "connectivity.h"
#include "esp_attr.h"
#include "esp_app_desc.h"
#include "esp_heap_caps.h"
#include "esp_system.h"
#include "lvgl.h"
#include "src/misc/cache/instance/lv_image_cache.h"
#include "nvs.h"
#include "sdkconfig.h"
#include "ui_icons.h"

LV_FONT_DECLARE(ui_font_chinese_semibold_24);
LV_FONT_DECLARE(ui_font_bambu_semibold_24);
LV_FONT_DECLARE(ui_font_digits_64);
LV_FONT_DECLARE(ui_font_detail_20);
LV_FONT_DECLARE(ui_font_fixed_20);

#define SCREEN_SIZE 466
#define SAFE_SIZE 358
#define COLOR_BG 0x000000
#define COLOR_SURFACE 0x141A19
#define COLOR_TEXT 0xF1F8F5
#define COLOR_MUTED 0x8E9B97
#define COLOR_BLUE 0x5DA9FF
#define COLOR_BLUE_DARK 0x2F65FF
#define COLOR_BLUE_USAGE 0x3A8DFF
#define COLOR_ORANGE 0xFF9D3D
#define COLOR_CYAN 0x20D6E6
#define COLOR_GREEN 0x42D98C
#define COLOR_BAMBU 0x00AE42
#define COLOR_WARNING 0xF2C66D
#define COLOR_DANGER 0xFF766F
#define PAGE_COUNT 4
#define TITLE_HEIGHT 30
#define DISPLAY_ANGLE_DEFAULT_TENTHS 840
#define DISPLAY_ANGLE_MIN_TENTHS 800
#define DISPLAY_ANGLE_MAX_TENTHS 1000

static QueueHandle_t s_snapshot_queue;
/* The UI reads this snapshot from task context; it is not a DMA buffer. */
EXT_RAM_BSS_ATTR static codex_snapshot_t s_snapshot;
static lv_obj_t *s_main;
static lv_obj_t *s_main_content;
static lv_obj_t *s_plus_main;
static lv_obj_t *s_plus_five_arc;
static lv_obj_t *s_plus_weekly_arc;
static lv_obj_t *s_plus_five_percent;
static lv_obj_t *s_plus_weekly_percent;
static lv_obj_t *s_plus_five_reset;
static lv_obj_t *s_plus_weekly_reset;
static lv_obj_t *s_plus_weekly_reset_day;
static lv_obj_t *s_plus_time;
static lv_obj_t *s_plus_status;
static lv_obj_t *s_plus_status_pill;
static lv_obj_t *s_detail;
static lv_obj_t *s_bambu_main;
static lv_obj_t *s_bambu_detail;
static lv_obj_t *s_custom;
static lv_obj_t *s_dotii;
static lv_obj_t *s_control;
static lv_obj_t *s_settings;
static lv_obj_t *s_power;
static lv_obj_t *s_before_power;
static lv_obj_t *s_before_control;
static lv_obj_t *s_before_screen_saver;
static lv_obj_t *s_current;
static lv_obj_t *s_main_arc;
static lv_obj_t *s_percent;
static lv_obj_t *s_status;
static lv_obj_t *s_status_pill;
static lv_obj_t *s_status_dot;
static lv_obj_t *s_weekly_tokens;
static lv_obj_t *s_reset_day;
static lv_obj_t *s_reset_time;
static lv_obj_t *s_time_main;
static lv_obj_t *s_time_detail;
static lv_obj_t *s_time_bambu_main;
static lv_obj_t *s_time_bambu_detail;
static lv_obj_t *s_bambu_arc;
static lv_obj_t *s_bambu_status;
static lv_obj_t *s_bambu_percent;
static lv_obj_t *s_bambu_remaining;
static lv_obj_t *s_bambu_finish;
static lv_obj_t *s_bambu_status_pill;
static lv_obj_t *s_bambu_status_dot;
static lv_obj_t *s_bambu_camera;
static lv_obj_t *s_bambu_camera_empty;
static lv_obj_t *s_bambu_filename;
static lv_obj_t *s_bambu_filament;
static lv_obj_t *s_bambu_layer;
static lv_obj_t *s_bambu_pause;
static lv_obj_t *s_bambu_pause_icon;
static lv_obj_t *s_bambu_stop;
static lv_obj_t *s_bambu_stop_progress;
static lv_obj_t *s_detail_title;
static lv_obj_t *s_detail_meta;
static lv_obj_t *s_user_message;
static lv_obj_t *s_assistant_message;
static lv_obj_t *s_assistant_speaker;
static lv_obj_t *s_assistant_bubble;
static lv_obj_t *s_detail_chat;
static lv_obj_t *s_settings_list;
static lv_obj_t *s_detail_footer;
static lv_obj_t *s_battery_label;
static lv_obj_t *s_settings_wifi;
static lv_obj_t *s_settings_ip;
static lv_obj_t *s_settings_bridge;
static lv_obj_t *s_brightness_slider;
static lv_obj_t *s_custom_ring;
static lv_obj_t *s_custom_image;
static lv_obj_t *s_custom_title;
static lv_obj_t *s_custom_value;
static lv_obj_t *s_custom_body;
static lv_obj_t *s_custom_footer;
static lv_obj_t *s_dotii_left_eye;
static lv_obj_t *s_dotii_right_eye;
static lv_obj_t *s_dotii_mouth;
static lv_obj_t *s_dotii_accent_left;
static lv_obj_t *s_dotii_accent_right;
static lv_obj_t *s_dotii_accent_center;
static lv_obj_t *s_codex_quick;
static lv_obj_t *s_bambu_quick;
static lv_obj_t *s_custom_quick;
static lv_obj_t *s_dotii_quick;
static lv_obj_t *s_page_dots[PAGE_COUNT][PAGE_COUNT];
static lv_point_t s_press_point;
static uint8_t s_battery = 0xFF;
static int s_brightness = 64;
static int16_t s_display_angle_tenths = DISPLAY_ANGLE_DEFAULT_TENTHS;
static uint32_t s_display_revision;
static uint32_t s_screen_off_timeout_seconds = 60;
static uint32_t s_sleep_timeout_seconds = 300;
static uint32_t s_charging_screen_off_timeout_seconds = 60;
static uint32_t s_charging_sleep_timeout_seconds = 300;
static display_screen_off_page_t s_screen_off_page = DISPLAY_SCREEN_OFF_PAGE_NONE;
static bool s_external_power;
static uint32_t s_last_activity;
static uint32_t s_last_press_event;
static bool s_screen_on = true;
static bool s_screen_saver_active;
static bool s_ignore_next_dotii_click;
static bool s_wake_touch_in_progress;
static bool s_screen_load_pending;
static lv_obj_t *s_pending_screen;
typedef enum {
    TRANSITION_PAGE_FORWARD,
    TRANSITION_PAGE_BACK,
    TRANSITION_CONTROL_DOWN,
    TRANSITION_CONTROL_UP,
    TRANSITION_DIRECT,
} transition_t;
static transition_t s_pending_transition;
static lv_font_t s_ui_font;
static lv_image_dsc_t s_custom_image_dsc;
static uint32_t s_custom_image_revision;
static lv_image_dsc_t s_bambu_camera_dsc;
static uint32_t s_bambu_camera_revision;
static char *s_rendered_conversation;
static codex_task_detail_t *s_detail_task;
static size_t s_detail_task_index;
static size_t s_detail_task_count;
static char s_detail_thread_id[64];
static uint32_t s_dotii_touch_started;
static uint32_t s_dotii_state_started;
static uint32_t s_dotii_state_token;
static uint32_t s_dotii_state_duration_ms;
static dotii_expression_t s_dotii_state_expression;
static bool s_dotii_state_assigned;
static bool s_dotii_state_seen;

typedef struct {
    lv_obj_t *screen;
    lv_obj_t *target;
} page_scroll_pair_t;

static page_scroll_pair_t s_page_scroll_pairs[2];
static size_t s_page_scroll_pair_count;

static bool screen_enabled(lv_obj_t *screen);
static bool page_enabled(uint8_t page);
static lv_obj_t *page_screen(uint8_t page);
static lv_obj_t *first_enabled_screen(void);
static lv_obj_t *enabled_return_screen(lv_obj_t *screen);
static void render_codex_detail(void);
static void render_dotii_expression(void);
static lv_color_t color(uint32_t hex);
static void set_screen_background(lv_obj_t *screen);
static void screen_off(void);
static void screen_wake(void);
static bool enter_screen_saver(void);
static bool exit_screen_saver(void);
static lv_obj_t *make_label(lv_obj_t *parent, const char *text,
                            const lv_font_t *font, uint32_t text_color);

static lv_color_t color(uint32_t hex)
{
    return lv_color_hex(hex);
}

static void set_screen_background(lv_obj_t *screen)
{
    lv_obj_remove_flag(screen, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(screen, color(COLOR_BG), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
    lv_obj_set_style_text_color(screen, color(COLOR_TEXT), 0);
    lv_obj_set_style_text_font(screen, &ui_font_detail_20, 0);
}

static lv_obj_t *make_label(lv_obj_t *parent, const char *text, const lv_font_t *font, uint32_t text_color)
{
    lv_obj_t *label = lv_label_create(parent);
    lv_label_set_text(label, text);
    lv_obj_set_style_text_font(label, font, 0);
    lv_obj_set_style_text_color(label, color(text_color), 0);
    return label;
}

/* Centering a scaled label by its glyph bounds is unstable because Chinese
   glyph bearings and the transform scale both change the visual center.  Give
   every page title a fixed box and scale around that box's geometric center. */
static lv_obj_t *make_centered_title(lv_obj_t *parent, const char *text,
                                     const lv_font_t *font, int32_t width,
                                     int32_t scale, int32_t outline_width,
                                     int32_t y)
{
    lv_obj_t *title = make_label(parent, text, font, COLOR_TEXT);
    lv_obj_set_size(title, width, TITLE_HEIGHT);
    lv_obj_set_style_text_align(title, LV_TEXT_ALIGN_CENTER, 0);
    if (outline_width > 0) {
        lv_obj_set_style_text_outline_stroke_color(title, color(COLOR_TEXT), 0);
        lv_obj_set_style_text_outline_stroke_width(title, outline_width, 0);
        lv_obj_set_style_text_outline_stroke_opa(title, LV_OPA_COVER, 0);
    }
    if (scale != 256) lv_obj_set_style_transform_scale(title, scale, 0);
    lv_obj_set_style_transform_pivot_x(title, width / 2, 0);
    lv_obj_set_style_transform_pivot_y(title, TITLE_HEIGHT / 2, 0);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, y);
    return title;
}

static lv_obj_t *make_safe(lv_obj_t *screen)
{
    lv_obj_t *safe = lv_obj_create(screen);
    lv_obj_remove_style_all(safe);
    lv_obj_set_size(safe, SAFE_SIZE, SAFE_SIZE);
    lv_obj_center(safe);
    lv_obj_remove_flag(safe, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(safe, LV_OBJ_FLAG_CLICKABLE);
    return safe;
}

static void page_scroll_step(page_scroll_pair_t *pair, bool down)
{
    if (pair == NULL || pair->target == NULL) return;
    lv_obj_update_layout(pair->target);
    if ((down && lv_obj_get_scroll_bottom(pair->target) <= 0) ||
        (!down && lv_obj_get_scroll_top(pair->target) <= 0)) {
        return;
    }
    int32_t step = lv_obj_get_height(pair->target) * 3 / 4;
    int32_t destination = lv_obj_get_scroll_y(pair->target) + (down ? step : -step);
    /* Scrolling moves already-rendered content. Force one complete transformed
       frame for every vertically scrolling page; a rotated partial refresh can
       otherwise leave stale/jagged strips in the newly exposed area. The
       physical reveal direction follows the vertical gesture. */
    bsp_display_transform_set_next_sweep(down ? BSP_DISPLAY_SWEEP_UP :
                                            BSP_DISPLAY_SWEEP_DOWN);
    bsp_display_transform_force_full_refresh();
    lv_obj_scroll_to_y(pair->target, destination, LV_ANIM_OFF);
}

static bool page_scroll_current(bool down)
{
    for (size_t index = 0; index < s_page_scroll_pair_count; ++index) {
        page_scroll_pair_t *pair = &s_page_scroll_pairs[index];
        if (pair->screen == s_current) {
            page_scroll_step(pair, down);
            return true;
        }
    }
    return false;
}

static void register_page_scroll_target(lv_obj_t *screen, lv_obj_t *target)
{
    if (s_page_scroll_pair_count >= sizeof(s_page_scroll_pairs) / sizeof(s_page_scroll_pairs[0])) return;
    page_scroll_pair_t *pair = &s_page_scroll_pairs[s_page_scroll_pair_count++];
    pair->screen = screen;
    pair->target = target;
    /* Native free scrolling is disabled. Each vertical gesture advances a
       deterministic 75% viewport step through page_scroll_step(). */
    lv_obj_remove_flag(target, LV_OBJ_FLAG_SCROLLABLE);
}

static void load_screen_commit(void *user_data)
{
    (void)user_data;
    lv_obj_t *screen = s_pending_screen;
    if (screen == NULL) {
        s_screen_load_pending = false;
        return;
    }

    if (s_pending_transition == TRANSITION_DIRECT) {
        lv_screen_load(screen);
        lv_obj_invalidate(screen);
        lv_refr_now(lv_obj_get_display(screen));
        s_screen_load_pending = false;
        return;
    }

    lv_screen_load_anim(screen,
                        s_pending_transition == TRANSITION_CONTROL_DOWN ? LV_SCREEN_LOAD_ANIM_OVER_BOTTOM :
                        s_pending_transition == TRANSITION_CONTROL_UP ? LV_SCREEN_LOAD_ANIM_OUT_TOP :
                        s_pending_transition == TRANSITION_PAGE_FORWARD ? LV_SCREEN_LOAD_ANIM_MOVE_LEFT :
                        LV_SCREEN_LOAD_ANIM_MOVE_RIGHT,
                        240, 0, false);
}

static void load_screen_with_transition(lv_obj_t *screen, transition_t transition)
{
    if (screen == NULL || screen == s_current || s_screen_load_pending) return;

    const bsp_display_sweep_t sweep =
        transition == TRANSITION_PAGE_BACK ? BSP_DISPLAY_SWEEP_RIGHT :
        transition == TRANSITION_CONTROL_DOWN ? BSP_DISPLAY_SWEEP_DOWN :
        transition == TRANSITION_CONTROL_UP ? BSP_DISPLAY_SWEEP_UP :
        BSP_DISPLAY_SWEEP_LEFT;
    bsp_display_transform_set_next_sweep(sweep);
    transition = TRANSITION_DIRECT;

    s_current = screen;
    s_last_activity = lv_tick_get();
    s_screen_load_pending = true;
    s_pending_screen = screen;
    s_pending_transition = transition;
    /* Gestures bubble through their old object tree.  Deferring the actual
       switch until that dispatch has finished prevents the old and new screen
       from being flushed during the same gesture transaction. */
    if (lv_async_call(load_screen_commit, NULL) != LV_RESULT_OK) {
        load_screen_commit(NULL);
    }
}

static void load_screen(lv_obj_t *screen, bool forward)
{
    load_screen_with_transition(screen, forward ? TRANSITION_PAGE_FORWARD : TRANSITION_PAGE_BACK);
}

static void screen_loaded_event(lv_event_t *event)
{
    if (lv_event_get_code(event) != LV_EVENT_SCREEN_LOADED) return;
    /* The load animation has already submitted its final frame.  Forcing a
       second full-screen refresh here creates a visible hitch at the end of
       every swipe and is unnecessary with reliable DMA strip buffers. */
    s_pending_screen = NULL;
    s_screen_load_pending = false;
}

static void activity_event(lv_event_t *event)
{
    lv_event_code_t code = lv_event_get_code(event);
    if (code == LV_EVENT_PRESSED) {
        if (s_screen_saver_active) {
            const bool was_dotii_saver = s_current == s_dotii;
            exit_screen_saver();
            s_ignore_next_dotii_click = was_dotii_saver;
            s_wake_touch_in_progress = true;
            return;
        }
        if (!s_screen_on) {
            screen_wake();
            s_wake_touch_in_progress = true;
            return;
        }
        lv_indev_t *input = lv_indev_active();
        if (input != NULL) lv_indev_get_point(input, &s_press_point);
        uint32_t now = lv_tick_get();
        if (s_last_press_event != 0 && lv_tick_elaps(s_last_press_event) < 80) return;
        s_last_press_event = now;
        s_last_activity = now;
    } else if (code == LV_EVENT_RELEASED || code == LV_EVENT_PRESS_LOST) {
        s_wake_touch_in_progress = false;
    }
}

static void add_activity_event(lv_obj_t *screen)
{
    lv_obj_add_event_cb(screen, activity_event, LV_EVENT_ALL, NULL);
    lv_obj_add_event_cb(screen, screen_loaded_event, LV_EVENT_SCREEN_LOADED, NULL);
}

static void return_from_current(void)
{
    if (s_current == s_detail) load_screen(enabled_return_screen(page_screen(0)), false);
    else if (s_current == s_bambu_detail) load_screen(enabled_return_screen(s_bambu_main), false);
    else if (s_current == s_settings) load_screen(first_enabled_screen(), false);
    else if (s_current == s_power) load_screen(enabled_return_screen(s_before_power), false);
}

static void swipe_event(lv_event_t *event)
{
    if (lv_event_get_code(event) != LV_EVENT_GESTURE) return;
    if (s_wake_touch_in_progress) return;
    lv_indev_t *input = lv_indev_active();
    if (input == NULL) return;

    lv_dir_t direction = lv_indev_get_gesture_dir(input);
    if (s_current == s_control && direction == LV_DIR_TOP) {
        load_screen_with_transition(enabled_return_screen(s_before_control), TRANSITION_CONTROL_UP);
    } else if (s_current != s_control && s_current != s_power &&
               direction == LV_DIR_BOTTOM && s_press_point.y <= 80) {
        s_before_control = s_current;
        load_screen_with_transition(s_control, TRANSITION_CONTROL_DOWN);
    } else if (direction == LV_DIR_TOP && page_scroll_current(true)) {
        /* One upward swipe advances one viewport step. */
    } else if (direction == LV_DIR_BOTTOM && page_scroll_current(false)) {
        /* One downward swipe returns one viewport step. */
    } else if ((s_current == s_main || s_current == s_plus_main) && direction == LV_DIR_LEFT) {
        s_detail_task_index = 0;
        s_detail_thread_id[0] = '\0';
        render_codex_detail();
        load_screen(s_detail, true);
    } else if (s_current == s_detail && direction == LV_DIR_LEFT) {
        if (s_detail_task_index + 1 < s_detail_task_count) {
            bsp_display_transform_set_next_sweep(BSP_DISPLAY_SWEEP_LEFT);
            s_detail_task_index++;
            s_detail_thread_id[0] = '\0';
            render_codex_detail();
        }
    } else if (s_current == s_detail && direction == LV_DIR_RIGHT) {
        if (s_detail_task_index > 0) {
            bsp_display_transform_set_next_sweep(BSP_DISPLAY_SWEEP_RIGHT);
            s_detail_task_index--;
            s_detail_thread_id[0] = '\0';
            render_codex_detail();
        } else {
            return_from_current();
        }
    } else if (s_current == s_bambu_main && direction == LV_DIR_LEFT) {
        load_screen(s_bambu_detail, true);
    } else if (direction == LV_DIR_RIGHT) {
        return_from_current();
    }
}

static void quick_clicked(lv_event_t *event)
{
    lv_obj_t *target = (lv_obj_t *)lv_event_get_user_data(event);
    if (lv_event_get_code(event) == LV_EVENT_CLICKED) {
        if (target == s_main) target = page_screen(0);
        load_screen_with_transition(target, TRANSITION_CONTROL_UP);
    }
}

static void brightness_changed(lv_event_t *event)
{
    if (lv_event_get_code(event) != LV_EVENT_VALUE_CHANGED) return;
    s_brightness = lv_slider_get_value(lv_event_get_target_obj(event));
    if (s_screen_on) bsp_display_brightness_set(s_brightness);
}

static void screen_off(void)
{
    if (!s_screen_on) return;
    bsp_display_brightness_set(0);
    s_screen_on = false;
}

static void screen_wake(void)
{
    if (s_screen_on) return;
    bsp_display_brightness_set(s_brightness);
    s_screen_on = true;
    s_last_activity = lv_tick_get();
}

static lv_obj_t *screen_saver_target(void)
{
    /* An explicit saver choice is independent from the normal page-cycle
       switches: disabling a page should not silently change the selected
       ambient page. */
    if (s_screen_off_page == DISPLAY_SCREEN_OFF_PAGE_CUSTOM) {
        return s_custom;
    }
    if (s_screen_off_page == DISPLAY_SCREEN_OFF_PAGE_DOTII) {
        return s_dotii;
    }
    return NULL;
}

static bool enter_screen_saver(void)
{
    if (s_screen_saver_active) return true;
    lv_obj_t *target = screen_saver_target();
    if (target == NULL || s_current == NULL) return false;
    s_before_screen_saver = s_current;
    s_screen_saver_active = true;
    if (target != s_current) {
        /* Loading the saver must not restart the inactivity clock: the
           configured sleep deadline is measured from the user's last input. */
        const uint32_t last_activity = s_last_activity;
        load_screen_with_transition(target, TRANSITION_DIRECT);
        s_last_activity = last_activity;
    } else {
        lv_obj_invalidate(target);
    }
    return true;
}

static bool exit_screen_saver(void)
{
    if (!s_screen_saver_active) return false;
    s_screen_saver_active = false;
    lv_obj_t *return_screen = enabled_return_screen(s_before_screen_saver);
    s_before_screen_saver = NULL;
    s_last_activity = lv_tick_get();
    if (return_screen != NULL && return_screen != s_current) {
        load_screen_with_transition(return_screen, TRANSITION_DIRECT);
    } else if (s_current != NULL) {
        lv_obj_invalidate(s_current);
    }
    return true;
}

static void load_persisted_settings(void)
{
    nvs_handle_t handle;
    if (nvs_open("display_ui", NVS_READONLY, &handle) != ESP_OK) return;
    if (nvs_get_u32(handle, "sleep_sec", &s_sleep_timeout_seconds) != ESP_OK) {
        static const uint32_t legacy_seconds[] = {5, 10, 30, 60, 300, 0, 300};
        uint8_t legacy_option = 4;
        if (nvs_get_u8(handle, "timeout", &legacy_option) == ESP_OK && legacy_option < 7) {
            s_sleep_timeout_seconds = legacy_seconds[legacy_option];
        }
    }
    if (s_sleep_timeout_seconds == 5) s_sleep_timeout_seconds = 10;
    if (nvs_get_u32(handle, "off_sec", &s_screen_off_timeout_seconds) != ESP_OK) {
        s_screen_off_timeout_seconds = s_sleep_timeout_seconds == 0 ? 60 :
                                       s_sleep_timeout_seconds;
    }
    if (s_screen_off_timeout_seconds == 5) s_screen_off_timeout_seconds = 10;
    if (s_screen_off_timeout_seconds == 0 && s_sleep_timeout_seconds != 0) {
        s_screen_off_timeout_seconds = s_sleep_timeout_seconds;
    } else if (s_screen_off_timeout_seconds != 0 && s_sleep_timeout_seconds != 0 &&
               s_sleep_timeout_seconds < s_screen_off_timeout_seconds) {
        s_screen_off_timeout_seconds = s_sleep_timeout_seconds;
    }
    if (nvs_get_u32(handle, "chg_off_sec", &s_charging_screen_off_timeout_seconds) != ESP_OK) {
        s_charging_screen_off_timeout_seconds = s_screen_off_timeout_seconds;
    }
    if (nvs_get_u32(handle, "chg_sleep_sec", &s_charging_sleep_timeout_seconds) != ESP_OK) {
        s_charging_sleep_timeout_seconds = s_sleep_timeout_seconds;
    }
    if (s_charging_screen_off_timeout_seconds == 5) s_charging_screen_off_timeout_seconds = 10;
    if (s_charging_sleep_timeout_seconds == 5) s_charging_sleep_timeout_seconds = 10;
    if (s_charging_screen_off_timeout_seconds == 0 && s_charging_sleep_timeout_seconds != 0) {
        s_charging_screen_off_timeout_seconds = s_charging_sleep_timeout_seconds;
    } else if (s_charging_screen_off_timeout_seconds != 0 && s_charging_sleep_timeout_seconds != 0 &&
               s_charging_sleep_timeout_seconds < s_charging_screen_off_timeout_seconds) {
        s_charging_screen_off_timeout_seconds = s_charging_sleep_timeout_seconds;
    }
    uint8_t screen_off_page = DISPLAY_SCREEN_OFF_PAGE_NONE;
    if (nvs_get_u8(handle, "off_page", &screen_off_page) == ESP_OK &&
        screen_off_page <= DISPLAY_SCREEN_OFF_PAGE_DOTII) {
        s_screen_off_page = (display_screen_off_page_t)screen_off_page;
    }
    int16_t angle;
    if (nvs_get_i16(handle, "dock_angle", &angle) == ESP_OK &&
        angle >= DISPLAY_ANGLE_MIN_TENTHS && angle <= DISPLAY_ANGLE_MAX_TENTHS) {
        s_display_angle_tenths = angle;
    }
    nvs_get_u32(handle, "display_rev", &s_display_revision);
    nvs_close(handle);
}

static void save_display_settings(void)
{
    nvs_handle_t handle;
    if (nvs_open("display_ui", NVS_READWRITE, &handle) != ESP_OK) return;
    esp_err_t angle_result = nvs_set_i16(handle, "dock_angle", s_display_angle_tenths);
    esp_err_t revision_result = nvs_set_u32(handle, "display_rev", s_display_revision);
    esp_err_t off_result = nvs_set_u32(handle, "off_sec", s_screen_off_timeout_seconds);
    esp_err_t sleep_result = nvs_set_u32(handle, "sleep_sec", s_sleep_timeout_seconds);
    esp_err_t charging_off_result = nvs_set_u32(handle, "chg_off_sec", s_charging_screen_off_timeout_seconds);
    esp_err_t charging_sleep_result = nvs_set_u32(handle, "chg_sleep_sec", s_charging_sleep_timeout_seconds);
    esp_err_t screen_off_page_result = nvs_set_u8(handle, "off_page", (uint8_t)s_screen_off_page);
    if (angle_result == ESP_OK && revision_result == ESP_OK &&
        off_result == ESP_OK && sleep_result == ESP_OK && charging_off_result == ESP_OK &&
        charging_sleep_result == ESP_OK && screen_off_page_result == ESP_OK) nvs_commit(handle);
    nvs_close(handle);
}

static void refresh_clicked(lv_event_t *event)
{
    if (lv_event_get_code(event) == LV_EVENT_CLICKED) connectivity_request_refresh();
}

static void restart_anim_exec(void *object, int32_t value)
{
    lv_bar_set_value((lv_obj_t *)object, value, LV_ANIM_OFF);
}

static void restart_hold_anim_exec(void *object, int32_t value)
{
    lv_color_t mixed = lv_color_mix(color(0x286348), color(0x173D2D), (uint8_t)value);
    lv_obj_set_style_bg_color((lv_obj_t *)object, mixed, 0);
    lv_obj_set_style_bg_color((lv_obj_t *)object, mixed, LV_STATE_PRESSED);
}

static void shutdown_hold_anim_exec(void *object, int32_t value)
{
    lv_color_t mixed = lv_color_mix(color(0x682A28), color(0x3A1D1D), (uint8_t)value);
    lv_obj_set_style_bg_color((lv_obj_t *)object, mixed, 0);
    lv_obj_set_style_bg_color((lv_obj_t *)object, mixed, LV_STATE_PRESSED);
}

static void restart_anim_done(lv_anim_t *animation)
{
    (void)animation;
    esp_restart();
}

static void restart_event(lv_event_t *event)
{
    lv_event_code_t code = lv_event_get_code(event);
    lv_obj_t *button = lv_event_get_current_target(event);
    if (code == LV_EVENT_PRESSED) {
        lv_anim_t animation;
        lv_anim_init(&animation);
        lv_anim_set_var(&animation, button);
        lv_anim_set_values(&animation, 0, 255);
        lv_anim_set_duration(&animation, 1200);
        lv_anim_set_exec_cb(&animation, restart_hold_anim_exec);
        lv_anim_set_completed_cb(&animation, restart_anim_done);
        lv_anim_start(&animation);
    } else if (code == LV_EVENT_RELEASED || code == LV_EVENT_PRESS_LOST) {
        lv_anim_delete(button, restart_hold_anim_exec);
        lv_obj_set_style_bg_color(button, color(0x173D2D), 0);
        lv_obj_set_style_bg_color(button, color(0x214F3A), LV_STATE_PRESSED);
    }
}

static void shutdown_anim_done(lv_anim_t *animation)
{
    (void)animation;
    board_input_request_shutdown();
}

static void shutdown_event(lv_event_t *event)
{
    lv_event_code_t code = lv_event_get_code(event);
    lv_obj_t *button = lv_event_get_current_target(event);
    if (code == LV_EVENT_PRESSED) {
        lv_anim_t animation;
        lv_anim_init(&animation);
        lv_anim_set_var(&animation, button);
        lv_anim_set_values(&animation, 0, 255);
        lv_anim_set_duration(&animation, 1200);
        lv_anim_set_exec_cb(&animation, shutdown_hold_anim_exec);
        lv_anim_set_completed_cb(&animation, shutdown_anim_done);
        lv_anim_start(&animation);
    } else if (code == LV_EVENT_RELEASED || code == LV_EVENT_PRESS_LOST) {
        lv_anim_delete(button, shutdown_hold_anim_exec);
        lv_obj_set_style_bg_color(button, color(0x3A1D1D), 0);
        lv_obj_set_style_bg_color(button, color(0x572725), LV_STATE_PRESSED);
    }
}

static lv_obj_t *make_metric(lv_obj_t *parent, const char *value, const char *caption, int x)
{
    lv_obj_t *box = lv_obj_create(parent);
    lv_obj_remove_style_all(box);
    lv_obj_set_size(box, 132, 56);
    lv_obj_set_pos(box, x, 346);
    lv_obj_remove_flag(box, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t *value_label = make_label(box, value, &lv_font_montserrat_22, COLOR_TEXT);
    lv_obj_align(value_label, LV_ALIGN_TOP_MID, 0, 0);
    /* Fixed captions use one compact font instead of crossing the four large
       conversation-font fragments.  This keeps glyph metrics and bitmap
       decoding identical across the whole label. */
    lv_obj_t *caption_label = make_label(box, caption, &s_ui_font, COLOR_MUTED);
    lv_obj_set_style_transform_scale(caption_label, 282, 0);
    lv_obj_align(caption_label, LV_ALIGN_BOTTOM_MID, 0, 1);
    return value_label;
}

static void make_reset_metric(lv_obj_t *parent, int x)
{
    lv_obj_t *box = lv_obj_create(parent);
    lv_obj_remove_style_all(box);
    lv_obj_set_size(box, 132, 56);
    lv_obj_set_pos(box, x, 346);
    lv_obj_remove_flag(box, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *value_row = lv_obj_create(box);
    lv_obj_remove_style_all(value_row);
    lv_obj_set_size(value_row, 132, 25);
    lv_obj_align(value_row, LV_ALIGN_TOP_MID, 4, 0);
    lv_obj_set_layout(value_row, LV_LAYOUT_FLEX);
    lv_obj_set_flex_flow(value_row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(value_row, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(value_row, 4, 0);
    lv_obj_remove_flag(value_row, LV_OBJ_FLAG_SCROLLABLE);

    s_reset_day = make_label(value_row, "08-18", &lv_font_montserrat_22, COLOR_TEXT);
    s_reset_time = make_label(value_row, "10:00", &lv_font_montserrat_18, COLOR_TEXT);
    lv_obj_t *caption = make_label(box, "额度重置", &s_ui_font, COLOR_MUTED);
    lv_obj_set_style_transform_scale(caption, 282, 0);
    lv_obj_align(caption, LV_ALIGN_BOTTOM_MID, 0, 1);
}

static bool page_enabled(uint8_t page)
{
    if (!s_snapshot.valid) return page < 2;
    if (page == 0) return s_snapshot.codex_enabled;
    if (page == 1) return s_snapshot.bambu_enabled;
    if (page == 2) return s_snapshot.custom_enabled;
    return s_snapshot.dotii_enabled;
}

static lv_obj_t *page_screen(uint8_t page)
{
    if (page == 0) return s_snapshot.codex_ui_dual_limit ? s_plus_main : s_main;
    if (page == 1) return s_bambu_main;
    if (page == 2) return s_custom;
    return s_dotii;
}

static lv_obj_t *first_enabled_screen(void)
{
    for (uint8_t page = 0; page < PAGE_COUNT; ++page) {
        if (page_enabled(page)) return page_screen(page);
    }
    return page_screen(0);
}

static bool screen_enabled(lv_obj_t *screen)
{
    if (screen == s_main || screen == s_plus_main || screen == s_detail) return page_enabled(0);
    if (screen == s_bambu_main || screen == s_bambu_detail) return page_enabled(1);
    if (screen == s_custom) return page_enabled(2);
    if (screen == s_dotii) return page_enabled(3);
    return true;
}

static lv_obj_t *enabled_return_screen(lv_obj_t *screen)
{
    if (screen == s_main || screen == s_plus_main) return page_screen(0);
    return screen != NULL && screen_enabled(screen) ? screen : first_enabled_screen();
}

static void update_page_dots(void)
{
    static const uint32_t active_colors[PAGE_COUNT] = {COLOR_BLUE, COLOR_BAMBU, COLOR_WARNING, COLOR_CYAN};
    for (uint8_t row = 0; row < PAGE_COUNT; ++row) {
        int total_width = 0;
        uint8_t count = 0;
        for (uint8_t page = 0; page < PAGE_COUNT; ++page) {
            if (!page_enabled(page)) continue;
            total_width += page == row ? 16 : 5;
            count++;
        }
        if (count > 1) total_width += (count - 1) * 8;
        int cursor = -total_width / 2;
        for (uint8_t page = 0; page < PAGE_COUNT; ++page) {
            lv_obj_t *dot = s_page_dots[row][page];
            if (dot == NULL) continue;
            if (!page_enabled(page)) {
                lv_obj_add_flag(dot, LV_OBJ_FLAG_HIDDEN);
                continue;
            }
            lv_obj_remove_flag(dot, LV_OBJ_FLAG_HIDDEN);
            int width = page == row ? 16 : 5;
            lv_obj_set_size(dot, width, 5);
            lv_obj_set_style_bg_color(dot, color(page == row ? active_colors[row] : 0x59605E), 0);
            lv_obj_set_style_radius(dot, page == row ? 3 : LV_RADIUS_CIRCLE, 0);
            lv_obj_align(dot, LV_ALIGN_BOTTOM_MID, cursor + width / 2, -29);
            cursor += width + 8;
        }
    }
}

static void make_page_dots(lv_obj_t *parent, uint8_t active_page)
{
    for (uint8_t page = 0; page < PAGE_COUNT; ++page) {
        lv_obj_t *dot = lv_obj_create(parent);
        lv_obj_remove_style_all(dot);
        lv_obj_set_style_bg_opa(dot, LV_OPA_COVER, 0);
        lv_obj_remove_flag(dot, LV_OBJ_FLAG_CLICKABLE);
        s_page_dots[active_page][page] = dot;
    }
    update_page_dots();
}

static void build_main(void)
{
    /* Waveshare's known-good LVGL demo builds directly on the display's
       current active screen.  Reusing it also guarantees that the first
       complete invalidation belongs to the screen attached by the BSP. */
    s_main = lv_screen_active();
    lv_obj_clean(s_main);
    set_screen_background(s_main);
    add_activity_event(s_main);

    s_main_content = lv_obj_create(s_main);
    lv_obj_remove_style_all(s_main_content);
    lv_obj_set_size(s_main_content, SCREEN_SIZE, SCREEN_SIZE);
    lv_obj_center(s_main_content);
    lv_obj_remove_flag(s_main_content, LV_OBJ_FLAG_SCROLLABLE);

    s_main_arc = lv_arc_create(s_main_content);
    lv_obj_set_size(s_main_arc, SCREEN_SIZE, SCREEN_SIZE);
    lv_obj_center(s_main_arc);
    lv_arc_set_range(s_main_arc, 0, 100);
    lv_arc_set_value(s_main_arc, 68);
    lv_arc_set_bg_angles(s_main_arc, 140, 400);
    lv_obj_set_style_arc_width(s_main_arc, 26, LV_PART_MAIN);
    lv_obj_set_style_arc_color(s_main_arc, color(0x232928), LV_PART_MAIN);
    lv_obj_set_style_arc_width(s_main_arc, 26, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(s_main_arc, color(COLOR_BLUE), LV_PART_INDICATOR);
    lv_obj_remove_style(s_main_arc, NULL, LV_PART_KNOB);
    lv_obj_remove_flag(s_main_arc, LV_OBJ_FLAG_CLICKABLE);

    lv_obj_t *title = make_label(s_main_content, "Codex", &lv_font_montserrat_28, COLOR_TEXT);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 48);
    s_time_main = make_label(s_main_content, "--:--", &lv_font_montserrat_20, COLOR_MUTED);
    lv_obj_align(s_time_main, LV_ALIGN_TOP_MID, 0, 82);

    lv_obj_t *kicker = make_label(s_main_content, "周剩余", &ui_font_chinese_semibold_24, COLOR_MUTED);
    lv_obj_align(kicker, LV_ALIGN_CENTER, 0, -82);
    s_percent = make_label(s_main_content, "68%", &ui_font_digits_64, COLOR_TEXT);
    lv_obj_set_style_transform_scale(s_percent, 282, 0);
    lv_obj_align(s_percent, LV_ALIGN_CENTER, 0, -19);

    s_status_pill = lv_obj_create(s_main_content);
    lv_obj_set_size(s_status_pill, 138, 48);
    lv_obj_set_style_radius(s_status_pill, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(s_status_pill, color(0x152A42), 0);
    lv_obj_set_style_bg_opa(s_status_pill, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(s_status_pill, 0, 0);
    lv_obj_set_style_pad_hor(s_status_pill, 6, 0);
    lv_obj_set_style_pad_ver(s_status_pill, 0, 0);
    lv_obj_set_style_pad_column(s_status_pill, 4, 0);
    lv_obj_set_layout(s_status_pill, LV_LAYOUT_FLEX);
    lv_obj_set_flex_flow(s_status_pill, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(s_status_pill, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_remove_flag(s_status_pill, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(s_status_pill, LV_OBJ_FLAG_CLICKABLE);
    s_status_dot = lv_obj_create(s_status_pill);
    lv_obj_remove_style_all(s_status_dot);
    lv_obj_set_size(s_status_dot, 14, 14);
    lv_obj_set_style_radius(s_status_dot, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(s_status_dot, color(COLOR_GREEN), 0);
    lv_obj_set_style_bg_opa(s_status_dot, LV_OPA_COVER, 0);
    s_status = make_label(s_status_pill, "工作中", &s_ui_font, COLOR_GREEN);
    lv_obj_set_size(s_status, 82, 22);
    lv_obj_set_style_text_align(s_status, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_transform_scale(s_status, 282, 0);
    lv_obj_set_style_text_outline_stroke_color(s_status, color(COLOR_GREEN), 0);
    lv_obj_set_style_text_outline_stroke_width(s_status, 2, 0);
    lv_obj_set_style_text_outline_stroke_opa(s_status, LV_OPA_COVER, 0);
    lv_obj_align(s_status_pill, LV_ALIGN_CENTER, 0, 52);

    s_weekly_tokens = make_metric(s_main_content, "18.4k", "近7日 Token", 92);
    make_reset_metric(s_main_content, 242);

    lv_obj_t *divider = lv_obj_create(s_main_content);
    lv_obj_remove_style_all(divider);
    lv_obj_set_size(divider, 1, 48);
    lv_obj_set_style_bg_color(divider, color(0x38403E), 0);
    lv_obj_set_style_bg_opa(divider, LV_OPA_COVER, 0);
    lv_obj_set_pos(divider, 233, 350);
    lv_obj_remove_flag(divider, LV_OBJ_FLAG_CLICKABLE);

    make_page_dots(s_main_content, 0);

    add_activity_event(s_main_content);
    lv_obj_add_event_cb(s_main, swipe_event, LV_EVENT_GESTURE, NULL);
}

static lv_obj_t *make_usage_arc(lv_obj_t *parent, int32_t start_angle,
                                int32_t end_angle, uint32_t accent)
{
    lv_obj_t *arc = lv_arc_create(parent);
    /* Match the classic Codex ring geometry exactly. */
    lv_obj_set_size(arc, SCREEN_SIZE, SCREEN_SIZE);
    lv_obj_center(arc);
    lv_arc_set_range(arc, 0, 100);
    lv_arc_set_value(arc, 0);
    lv_arc_set_bg_angles(arc, start_angle, end_angle);
    lv_obj_set_style_arc_width(arc, 26, LV_PART_MAIN);
    lv_obj_set_style_arc_color(arc, color(0x222725), LV_PART_MAIN);
    lv_obj_set_style_arc_rounded(arc, true, LV_PART_MAIN);
    lv_obj_set_style_arc_width(arc, 26, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(arc, color(accent), LV_PART_INDICATOR);
    lv_obj_set_style_arc_rounded(arc, true, LV_PART_INDICATOR);
    lv_obj_remove_style(arc, NULL, LV_PART_KNOB);
    lv_obj_remove_flag(arc, LV_OBJ_FLAG_CLICKABLE);
    return arc;
}

static void make_five_hour_title(lv_obj_t *parent, int32_t y)
{
    lv_obj_t *row = lv_obj_create(parent);
    lv_obj_remove_style_all(row);
    lv_obj_set_size(row, 180, 32);
    lv_obj_set_layout(row, LV_LAYOUT_FLEX);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_CENTER,
                          LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(row, 0, 0);
    lv_obj_remove_flag(row, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(row, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_align(row, LV_ALIGN_TOP_MID, 0, y);

    make_label(row, "5H", &lv_font_montserrat_22, COLOR_ORANGE);
    lv_obj_t *title_gap = lv_obj_create(row);
    lv_obj_remove_style_all(title_gap);
    lv_obj_set_size(title_gap, 2, 1);
    make_label(row, "剩余", &ui_font_chinese_semibold_24, COLOR_ORANGE);
}

static lv_obj_t *make_usage_reset_row(lv_obj_t *parent, int32_t y,
                                      lv_obj_t **weekday_label)
{
    lv_obj_t *row = lv_obj_create(parent);
    lv_obj_remove_style_all(row);
    lv_obj_set_size(row, 320, 28);
    lv_obj_set_layout(row, LV_LAYOUT_FLEX);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_CENTER,
                          LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(row, 0, 0);
    lv_obj_remove_flag(row, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(row, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_align(row, LV_ALIGN_TOP_MID, 0, y);

    if (weekday_label != NULL) {
        *weekday_label = make_label(row, "", &s_ui_font, COLOR_MUTED);
    }
    lv_obj_t *value = make_label(row, "--", &lv_font_montserrat_20, COLOR_MUTED);
    lv_obj_t *suffix_gap = lv_obj_create(row);
    lv_obj_remove_style_all(suffix_gap);
    lv_obj_set_size(suffix_gap, 6, 1);
    make_label(row, "重置", &s_ui_font, COLOR_MUTED);
    return value;
}

static void build_plus_main(void)
{
    s_plus_main = lv_obj_create(NULL);
    set_screen_background(s_plus_main);
    add_activity_event(s_plus_main);

    s_plus_five_arc = make_usage_arc(s_plus_main, 188, 352, COLOR_ORANGE);
    s_plus_weekly_arc = make_usage_arc(s_plus_main, 8, 172, COLOR_BLUE_USAGE);
    /* Anchor the blue indicator at the left endpoint. As the remaining value
     * falls, its right endpoint retreats from right to left. */
    lv_arc_set_mode(s_plus_weekly_arc, LV_ARC_MODE_REVERSE);

    make_five_hour_title(s_plus_main, 64);
    s_plus_five_percent = make_label(s_plus_main, "--", &ui_font_digits_64, COLOR_TEXT);
    lv_obj_set_style_transform_scale(s_plus_five_percent, 260, 0);
    lv_obj_align(s_plus_five_percent, LV_ALIGN_TOP_MID, 0, 104);
    s_plus_five_reset = make_usage_reset_row(s_plus_main, 166, NULL);

    s_plus_status_pill = lv_obj_create(s_plus_main);
    lv_obj_set_size(s_plus_status_pill, 224, 48);
    lv_obj_align(s_plus_status_pill, LV_ALIGN_CENTER, 0, 0);
    lv_obj_set_style_radius(s_plus_status_pill, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(s_plus_status_pill, color(0x152A42), 0);
    lv_obj_set_style_bg_opa(s_plus_status_pill, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(s_plus_status_pill, 0, 0);
    lv_obj_set_style_pad_all(s_plus_status_pill, 0, 0);
    lv_obj_remove_flag(s_plus_status_pill, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(s_plus_status_pill, LV_OBJ_FLAG_CLICKABLE);

    /* Font transforms do not participate in LVGL flex measurement.  Center a
       dedicated row, then apply the small optical corrections measured on the
       physical round display instead of centering the unscaled glyph boxes. */
    lv_obj_t *status_content = lv_obj_create(s_plus_status_pill);
    lv_obj_remove_style_all(status_content);
    lv_obj_set_size(status_content, 210, 40);
    lv_obj_align(status_content, LV_ALIGN_CENTER, -6, 0);
    lv_obj_set_style_pad_all(status_content, 0, 0);
    lv_obj_set_style_pad_column(status_content, 0, 0);
    lv_obj_set_layout(status_content, LV_LAYOUT_FLEX);
    lv_obj_set_flex_flow(status_content, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(status_content, LV_FLEX_ALIGN_CENTER,
                          LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);

    s_plus_time = make_label(status_content, "--:--", &s_ui_font, COLOR_TEXT);
    lv_obj_set_height(s_plus_time, 26);
    lv_obj_set_style_text_align(s_plus_time, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_transform_scale(s_plus_time, 310, 0);
    lv_obj_set_style_text_outline_stroke_color(s_plus_time, color(COLOR_TEXT), 0);
    lv_obj_set_style_text_outline_stroke_width(s_plus_time, 2, 0);
    lv_obj_set_style_text_outline_stroke_opa(s_plus_time, LV_OPA_COVER, 0);

    lv_obj_t *status_left_gap = lv_obj_create(status_content);
    lv_obj_remove_style_all(status_left_gap);
    lv_obj_set_size(status_left_gap, 10, 1);

    lv_obj_t *status_separator = make_label(status_content, "·", &s_ui_font,
                                            COLOR_MUTED);
    lv_obj_set_height(status_separator, 30);
    lv_obj_set_style_text_align(status_separator, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_transform_scale(status_separator, 460, 0);
    lv_obj_set_style_translate_y(status_separator, -4, 0);

    lv_obj_t *status_right_gap = lv_obj_create(status_content);
    lv_obj_remove_style_all(status_right_gap);
    lv_obj_set_size(status_right_gap, 12, 1);

    s_plus_status = make_label(status_content, "工作中", &s_ui_font, COLOR_GREEN);
    lv_obj_set_height(s_plus_status, 26);
    lv_obj_set_style_text_align(s_plus_status, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_transform_scale(s_plus_status, 310, 0);
    lv_obj_set_style_text_outline_stroke_color(s_plus_status, color(COLOR_GREEN), 0);
    lv_obj_set_style_text_outline_stroke_width(s_plus_status, 2, 0);
    lv_obj_set_style_text_outline_stroke_opa(s_plus_status, LV_OPA_COVER, 0);

    lv_obj_t *weekly_title = make_label(s_plus_main, "周剩余", &ui_font_chinese_semibold_24,
                                        COLOR_BLUE_USAGE);
    lv_obj_align(weekly_title, LV_ALIGN_TOP_MID, 0, 278);
    s_plus_weekly_percent = make_label(s_plus_main, "--", &ui_font_digits_64, COLOR_TEXT);
    lv_obj_set_style_transform_scale(s_plus_weekly_percent, 260, 0);
    lv_obj_align(s_plus_weekly_percent, LV_ALIGN_TOP_MID, 0, 318);
    s_plus_weekly_reset = make_usage_reset_row(s_plus_main, 378, &s_plus_weekly_reset_day);

    lv_obj_add_event_cb(s_plus_main, swipe_event, LV_EVENT_GESTURE, NULL);
}

static void set_usage_reset_text(lv_obj_t *label, const char *reset_date, bool time_only)
{
    if (reset_date == NULL || reset_date[0] == '\0') {
        lv_label_set_text(label, "--");
        return;
    }
    const char *value = reset_date;
    if (time_only) {
        const char *space = strchr(reset_date, ' ');
        if (space != NULL && space[1] != '\0') value = space + 1;
    }
    lv_label_set_text(label, value);
}

static void set_weekly_reset_text(lv_obj_t *day_label, lv_obj_t *time_label,
                                  const char *reset_date)
{
    if (reset_date == NULL || reset_date[0] == '\0') {
        lv_label_set_text(day_label, "--");
        lv_label_set_text(time_label, "");
        return;
    }

    int month = 0;
    int day = 0;
    int hour = 0;
    int minute = 0;
    if (sscanf(reset_date, "%d-%d %d:%d", &month, &day, &hour, &minute) != 4 ||
        month < 1 || month > 12 || day < 1 || day > 31 ||
        hour < 0 || hour > 23 || minute < 0 || minute > 59) {
        lv_label_set_text(day_label, "--");
        lv_label_set_text(time_label, reset_date);
        return;
    }

    time_t now = time(NULL);
    struct tm local;
    localtime_r(&now, &local);
    if (local.tm_year < 120) {
        lv_label_set_text(day_label, "--");
    } else {
        struct tm reset = local;
        reset.tm_mon = month - 1;
        reset.tm_mday = day;
        reset.tm_hour = hour;
        reset.tm_min = minute;
        reset.tm_sec = 0;
        reset.tm_isdst = -1;
        time_t reset_at = mktime(&reset);
        double offset = difftime(reset_at, now);
        if (offset < -15552000.0) {
            reset.tm_year += 1;
            reset_at = mktime(&reset);
        } else if (offset > 15552000.0) {
            reset.tm_year -= 1;
            reset_at = mktime(&reset);
        }
        static const char *const weekdays[] = {
            "周日", "周一", "周二", "周三", "周四", "周五", "周六",
        };
        struct tm reset_local;
        localtime_r(&reset_at, &reset_local);
        lv_label_set_text(day_label, weekdays[reset_local.tm_wday]);
    }

    char time_text[6];
    snprintf(time_text, sizeof(time_text), "%02d:%02d", hour, minute);
    lv_label_set_text(time_label, time_text);
}

static lv_obj_t *make_bubble(lv_obj_t *parent, const char *speaker, bool user)
{
    lv_obj_t *row = lv_obj_create(parent);
    lv_obj_remove_style_all(row);
    lv_obj_set_width(row, LV_PCT(100));
    lv_obj_set_height(row, LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_START);
    lv_obj_remove_flag(row, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(row, LV_OBJ_FLAG_CLICKABLE);

    lv_obj_t *bubble = lv_obj_create(row);
    lv_obj_set_width(bubble, 334);
    lv_obj_set_height(bubble, LV_SIZE_CONTENT);
    lv_obj_set_style_radius(bubble, 20, 0);
    lv_obj_set_style_bg_color(bubble, color(user ? 0x17283B : COLOR_SURFACE), 0);
    lv_obj_set_style_bg_opa(bubble, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(bubble, user ? 0 : 1, 0);
    lv_obj_set_style_border_color(bubble, color(0x303735), 0);
    lv_obj_set_style_pad_all(bubble, 13, 0);
    lv_obj_set_flex_flow(bubble, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(bubble, 5, 0);
    lv_obj_t *who = make_label(bubble, speaker, &s_ui_font, user ? COLOR_BLUE : COLOR_TEXT);
    lv_obj_set_style_bg_color(who, color(user ? 0x17283B : COLOR_SURFACE), 0);
    lv_obj_set_style_bg_opa(who, LV_OPA_COVER, 0);
    if (!user) {
        s_assistant_speaker = who;
        s_assistant_bubble = bubble;
    }
    lv_obj_set_width(who, LV_PCT(100));
    lv_obj_t *message = make_label(bubble, "--", &ui_font_detail_20, user ? 0xD8EBFF : 0xD8E2DE);
    lv_obj_set_style_bg_color(message, color(user ? 0x17283B : COLOR_SURFACE), 0);
    lv_obj_set_style_bg_opa(message, LV_OPA_COVER, 0);
    lv_label_set_long_mode(message, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(message, LV_PCT(100));
    lv_obj_set_style_text_line_space(message, 3, 0);
    return message;
}

static bool update_assistant_messages(const char *text)
{
    const char *render_text = text != NULL && text[0] ? text : "暂无 Codex 消息";
    if (s_rendered_conversation != NULL && strcmp(s_rendered_conversation, render_text) == 0) return false;
    size_t render_size = strlen(render_text) + 1;
    char *cache = heap_caps_realloc(s_rendered_conversation, render_size,
                                    MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (cache != NULL) {
        s_rendered_conversation = cache;
        memcpy(s_rendered_conversation, render_text, render_size);
    }

    while (lv_obj_get_child_count(s_assistant_bubble) > 1) {
        lv_obj_delete(lv_obj_get_child(s_assistant_bubble, 1));
    }

    const char *cursor = render_text;
    bool first = true;
    while (*cursor != '\0') {
        const char *separator = strchr(cursor, '\x1E');
        size_t length = separator != NULL ? (size_t)(separator - cursor) : strlen(cursor);
        if (!first) {
            lv_obj_t *line = lv_obj_create(s_assistant_bubble);
            lv_obj_remove_style_all(line);
            lv_obj_set_size(line, LV_PCT(100), 1);
            lv_obj_set_style_bg_color(line, color(0x44504C), 0);
            lv_obj_set_style_bg_opa(line, LV_OPA_70, 0);
            lv_obj_set_style_margin_top(line, 5, 0);
            lv_obj_set_style_margin_bottom(line, 5, 0);
            lv_obj_remove_flag(line, LV_OBJ_FLAG_SCROLLABLE);
        }
        lv_obj_t *message = make_label(s_assistant_bubble, "", &ui_font_detail_20, 0xD8E2DE);
        lv_obj_set_style_bg_color(message, color(COLOR_SURFACE), 0);
        lv_obj_set_style_bg_opa(message, LV_OPA_COVER, 0);
        lv_label_set_long_mode(message, LV_LABEL_LONG_WRAP);
        lv_obj_set_width(message, LV_PCT(100));
        lv_obj_set_style_text_line_space(message, 3, 0);
        lv_label_set_text_fmt(message, "%.*s", (int)length, cursor);
        if (first) s_assistant_message = message;
        first = false;
        if (separator == NULL) break;
        cursor = separator + 1;
    }
    lv_obj_update_layout(s_detail_chat);
    lv_obj_invalidate(s_detail_chat);
    return true;
}

static void position_codex_detail(codex_task_status_t status)
{
    lv_obj_update_layout(s_detail_chat);
    if (status == CODEX_STATUS_WORKING || status == CODEX_STATUS_WAITING) {
        int32_t bottom = lv_obj_get_scroll_bottom(s_detail_chat);
        lv_obj_scroll_to_y(s_detail_chat, lv_obj_get_scroll_y(s_detail_chat) + bottom, LV_ANIM_OFF);
        return;
    }
    lv_obj_t *assistant_row = lv_obj_get_parent(s_assistant_bubble);
    lv_obj_scroll_to_y(s_detail_chat, lv_obj_get_y(assistant_row), LV_ANIM_OFF);
}

static void render_codex_detail(void)
{
    if (s_detail_task == NULL) return;
    codex_task_status_t previous_status = s_detail_task->status;
    codex_conversation_mode_t previous_mode = s_detail_task->conversation_mode;
    time_t previous_updated_at = s_detail_task->updated_at;
    s_detail_task_count = app_state_task_count();
    if (s_detail_task_count == 0) return;

    bool entering_detail = s_current != s_detail;
    if (s_detail_thread_id[0] != '\0') {
        for (size_t index = 0; index < s_detail_task_count; index++) {
            if (app_state_task_copy(index, s_detail_task) &&
                strcmp(s_detail_task->thread_id, s_detail_thread_id) == 0) {
                s_detail_task_index = index;
                break;
            }
        }
    }
    if (s_detail_task_index >= s_detail_task_count) s_detail_task_index = s_detail_task_count - 1;
    if (!app_state_task_copy(s_detail_task_index, s_detail_task)) return;
    bool thread_changed = strcmp(s_detail_thread_id, s_detail_task->thread_id) != 0;
    bool task_changed = previous_status != s_detail_task->status ||
                        previous_mode != s_detail_task->conversation_mode ||
                        previous_updated_at != s_detail_task->updated_at;
    strlcpy(s_detail_thread_id, s_detail_task->thread_id, sizeof(s_detail_thread_id));

    lv_label_set_text(s_detail_title, s_detail_task->title[0] ? s_detail_task->title : "暂无任务");
    lv_label_set_text_fmt(s_detail_meta, "%s · %lu 分钟",
                          app_state_status_text(s_detail_task->status),
                          (unsigned long)(s_detail_task->duration_seconds / 60));
    lv_label_set_text(s_user_message, s_detail_task->last_user_message != NULL &&
                      s_detail_task->last_user_message[0] ?
                      s_detail_task->last_user_message : "暂无用户消息");
    lv_label_set_text(s_assistant_speaker,
                      s_detail_task->conversation_mode == CODEX_CONVERSATION_PROGRESS ?
                          "Codex 中间消息" :
                      s_detail_task->conversation_mode == CODEX_CONVERSATION_HISTORY ?
                          "Codex 最终回复" : "Codex 最终回复");
    bool conversation_changed = update_assistant_messages(s_detail_task->conversation_text);
    if (entering_detail || thread_changed || task_changed || conversation_changed) {
        position_codex_detail(s_detail_task->status);
    }

    static const char *task_numbers[] = {"一", "二", "三", "四", "五", "六"};
    if (s_detail_task_count > 1) {
        lv_label_set_text_fmt(s_detail_footer, "任务%s · %lu 消息",
                              task_numbers[s_detail_task_index],
                              (unsigned long)s_detail_task->message_count);
    } else {
        lv_label_set_text_fmt(s_detail_footer, "%lu 消息",
                              (unsigned long)s_detail_task->message_count);
    }
    if (s_current == s_detail) lv_obj_invalidate(s_detail);
}

static void build_detail(void)
{
    s_detail = lv_obj_create(NULL);
    set_screen_background(s_detail);
    add_activity_event(s_detail);
    lv_obj_add_event_cb(s_detail, swipe_event, LV_EVENT_GESTURE, NULL);
    lv_obj_t *safe = make_safe(s_detail);

    s_time_detail = make_label(safe, "--:--", &lv_font_montserrat_16, COLOR_MUTED);
    lv_obj_align(s_time_detail, LV_ALIGN_TOP_MID, 0, -34);
    s_detail_title = make_centered_title(safe, "开发 Codex 状态页面",
                                         &ui_font_detail_20, 336, 282, 1, 2);
    lv_label_set_long_mode(s_detail_title, LV_LABEL_LONG_DOT);
    lv_obj_set_style_bg_color(s_detail_title, color(COLOR_BG), 0);
    lv_obj_set_style_bg_opa(s_detail_title, LV_OPA_COVER, 0);
    s_detail_meta = make_label(s_detail, "工作中 · 12 分钟", &s_ui_font, COLOR_BLUE);
    lv_obj_set_style_bg_color(s_detail_meta, color(COLOR_BG), 0);
    lv_obj_set_style_bg_opa(s_detail_meta, LV_OPA_COVER, 0);
    lv_obj_align(s_detail_meta, LV_ALIGN_TOP_MID, 0, 24);

    s_detail_chat = lv_obj_create(safe);
    lv_obj_remove_style_all(s_detail_chat);
    lv_obj_set_size(s_detail_chat, 350, 324);
    lv_obj_set_style_bg_color(s_detail_chat, color(COLOR_BG), 0);
    lv_obj_set_style_bg_opa(s_detail_chat, LV_OPA_COVER, 0);
    lv_obj_align(s_detail_chat, LV_ALIGN_TOP_MID, 0, 40);
    lv_obj_set_flex_flow(s_detail_chat, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(s_detail_chat, 10, 0);
    lv_obj_add_flag(s_detail_chat, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scroll_dir(s_detail_chat, LV_DIR_VER);
    s_user_message = make_bubble(s_detail_chat, "你", true);
    s_assistant_message = make_bubble(s_detail_chat, "Codex", false);

    s_detail_footer = make_label(s_detail, "12 消息", &s_ui_font, COLOR_MUTED);
    lv_obj_set_style_bg_color(s_detail_footer, color(COLOR_BG), 0);
    lv_obj_set_style_bg_opa(s_detail_footer, LV_OPA_COVER, 0);
    lv_obj_set_size(s_detail_footer, 260, 24);
    lv_obj_set_style_text_align(s_detail_footer, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_pad_top(s_detail_footer, 2, 0);
    lv_obj_align(s_detail_footer, LV_ALIGN_BOTTOM_MID, 0, -14);
    register_page_scroll_target(s_detail, s_detail_chat);
}

static void bambu_pause_event(lv_event_t *event)
{
    if (lv_event_get_code(event) != LV_EVENT_CLICKED) return;
    connectivity_bambu_command(s_snapshot.bambu_status == BAMBU_STATUS_PAUSED ? "resume" : "pause");
}

static void bambu_stop_anim_done(lv_anim_t *animation)
{
    (void)animation;
    connectivity_bambu_command("stop");
    lv_bar_set_value(s_bambu_stop_progress, 0, LV_ANIM_OFF);
}

static void bambu_stop_event(lv_event_t *event)
{
    lv_event_code_t code = lv_event_get_code(event);
    if (code == LV_EVENT_PRESSED && !lv_obj_has_state(s_bambu_stop, LV_STATE_DISABLED)) {
        lv_anim_t animation;
        lv_anim_init(&animation);
        lv_anim_set_var(&animation, s_bambu_stop_progress);
        lv_anim_set_values(&animation, 0, 100);
        lv_anim_set_duration(&animation, 1200);
        lv_anim_set_exec_cb(&animation, restart_anim_exec);
        lv_anim_set_completed_cb(&animation, bambu_stop_anim_done);
        lv_anim_start(&animation);
    } else if (code == LV_EVENT_RELEASED || code == LV_EVENT_PRESS_LOST) {
        lv_anim_delete(s_bambu_stop_progress, restart_anim_exec);
        lv_bar_set_value(s_bambu_stop_progress, 0, LV_ANIM_OFF);
    }
}

static lv_obj_t *make_bambu_metric(lv_obj_t *parent, const char *caption, int x)
{
    lv_obj_t *box = lv_obj_create(parent);
    lv_obj_remove_style_all(box);
    lv_obj_set_size(box, 132, 56);
    lv_obj_set_pos(box, x, 346);
    lv_obj_remove_flag(box, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t *value = make_label(box, "--", &lv_font_montserrat_22, COLOR_TEXT);
    lv_obj_set_width(value, 132);
    lv_obj_set_style_text_align(value, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(value, LV_ALIGN_TOP_MID, 0, 0);
    lv_obj_t *label = make_label(box, caption, &s_ui_font, COLOR_MUTED);
    lv_obj_set_style_transform_scale(label, 282, 0);
    lv_obj_align(label, LV_ALIGN_BOTTOM_MID, 0, 1);
    return value;
}

static void build_bambu(void)
{
    s_bambu_main = lv_obj_create(NULL);
    set_screen_background(s_bambu_main);
    add_activity_event(s_bambu_main);
    lv_obj_add_event_cb(s_bambu_main, swipe_event, LV_EVENT_GESTURE, NULL);
    lv_obj_t *content = lv_obj_create(s_bambu_main);
    lv_obj_remove_style_all(content);
    lv_obj_set_size(content, SCREEN_SIZE, SCREEN_SIZE);
    lv_obj_center(content);
    lv_obj_remove_flag(content, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(content, LV_OBJ_FLAG_CLICKABLE);

    s_bambu_arc = lv_arc_create(content);
    lv_obj_set_size(s_bambu_arc, SCREEN_SIZE, SCREEN_SIZE);
    lv_obj_center(s_bambu_arc);
    lv_arc_set_range(s_bambu_arc, 0, 100);
    lv_arc_set_value(s_bambu_arc, 0);
    lv_arc_set_bg_angles(s_bambu_arc, 140, 400);
    lv_obj_set_style_arc_width(s_bambu_arc, 26, LV_PART_MAIN);
    lv_obj_set_style_arc_color(s_bambu_arc, color(0x092A16), LV_PART_MAIN);
    lv_obj_set_style_arc_width(s_bambu_arc, 26, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(s_bambu_arc, color(COLOR_BAMBU), LV_PART_INDICATOR);
    lv_obj_remove_style(s_bambu_arc, NULL, LV_PART_KNOB);
    lv_obj_remove_flag(s_bambu_arc, LV_OBJ_FLAG_CLICKABLE);

    lv_obj_t *title = make_label(content, "Bambu", &lv_font_montserrat_28, COLOR_TEXT);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 48);
    s_time_bambu_main = make_label(content, "--:--", &lv_font_montserrat_20, COLOR_MUTED);
    lv_obj_align(s_time_bambu_main, LV_ALIGN_TOP_MID, 0, 82);

    lv_obj_t *kicker = make_label(content, "打印进度", &ui_font_bambu_semibold_24, COLOR_MUTED);
    lv_obj_align(kicker, LV_ALIGN_CENTER, 0, -82);
    s_bambu_percent = make_label(content, "--", &ui_font_digits_64, COLOR_TEXT);
    lv_obj_set_style_transform_scale(s_bambu_percent, 282, 0);
    lv_obj_align(s_bambu_percent, LV_ALIGN_CENTER, 0, -19);

    s_bambu_status_pill = lv_obj_create(content);
    lv_obj_set_size(s_bambu_status_pill, 138, 48);
    lv_obj_set_style_radius(s_bambu_status_pill, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(s_bambu_status_pill, color(0x082913), 0);
    lv_obj_set_style_bg_opa(s_bambu_status_pill, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(s_bambu_status_pill, 0, 0);
    lv_obj_set_style_pad_hor(s_bambu_status_pill, 6, 0);
    lv_obj_set_style_pad_ver(s_bambu_status_pill, 0, 0);
    lv_obj_set_style_pad_column(s_bambu_status_pill, 4, 0);
    lv_obj_set_layout(s_bambu_status_pill, LV_LAYOUT_FLEX);
    lv_obj_set_flex_flow(s_bambu_status_pill, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(s_bambu_status_pill, LV_FLEX_ALIGN_CENTER,
                          LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_remove_flag(s_bambu_status_pill, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(s_bambu_status_pill, LV_OBJ_FLAG_CLICKABLE);
    s_bambu_status_dot = lv_obj_create(s_bambu_status_pill);
    lv_obj_remove_style_all(s_bambu_status_dot);
    lv_obj_set_size(s_bambu_status_dot, 14, 14);
    lv_obj_set_style_radius(s_bambu_status_dot, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(s_bambu_status_dot, color(COLOR_BAMBU), 0);
    lv_obj_set_style_bg_opa(s_bambu_status_dot, LV_OPA_COVER, 0);
    s_bambu_status = make_label(s_bambu_status_pill, "离线", &s_ui_font, COLOR_BAMBU);
    lv_obj_set_size(s_bambu_status, 82, 22);
    lv_obj_set_style_text_align(s_bambu_status, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_transform_scale(s_bambu_status, 282, 0);
    lv_obj_set_style_text_outline_stroke_color(s_bambu_status, color(COLOR_BAMBU), 0);
    lv_obj_set_style_text_outline_stroke_width(s_bambu_status, 2, 0);
    lv_obj_set_style_text_outline_stroke_opa(s_bambu_status, LV_OPA_COVER, 0);
    lv_obj_align(s_bambu_status_pill, LV_ALIGN_CENTER, 0, 52);

    s_bambu_remaining = make_bambu_metric(content, "剩余时间", 92);
    s_bambu_finish = make_bambu_metric(content, "预计完成", 242);

    lv_obj_t *divider = lv_obj_create(content);
    lv_obj_remove_style_all(divider);
    lv_obj_set_size(divider, 1, 48);
    lv_obj_set_style_bg_color(divider, color(0x38403E), 0);
    lv_obj_set_style_bg_opa(divider, LV_OPA_COVER, 0);
    lv_obj_set_pos(divider, 233, 350);
    lv_obj_remove_flag(divider, LV_OBJ_FLAG_CLICKABLE);

    make_page_dots(content, 1);

    s_bambu_detail = lv_obj_create(NULL);
    set_screen_background(s_bambu_detail);
    add_activity_event(s_bambu_detail);
    lv_obj_add_event_cb(s_bambu_detail, swipe_event, LV_EVENT_GESTURE, NULL);
    lv_obj_t *safe = make_safe(s_bambu_detail);
    s_time_bambu_detail = make_label(safe, "--:--", &lv_font_montserrat_16, COLOR_MUTED);
    lv_obj_align(s_time_bambu_detail, LV_ALIGN_TOP_MID, 0, -34);
    /* The title intentionally extends above the 358 px layout box. Keep it on
     * the full-screen layer so the safe container cannot clip its top edge. */
    make_centered_title(s_bambu_detail, "打印详情", &ui_font_bambu_semibold_24, 336, 256, 1, 50);

    lv_obj_t *camera_box = lv_obj_create(safe);
    lv_obj_set_size(camera_box, 350, 197);
    lv_obj_align(camera_box, LV_ALIGN_TOP_MID, 0, 34);
    lv_obj_set_style_radius(camera_box, 18, 0);
    lv_obj_set_style_clip_corner(camera_box, true, 0);
    lv_obj_set_style_bg_color(camera_box, color(0x0A0F0D), 0);
    lv_obj_set_style_border_width(camera_box, 1, 0);
    lv_obj_set_style_border_color(camera_box, color(COLOR_BAMBU), 0);
    lv_obj_set_style_border_opa(camera_box, LV_OPA_40, 0);
    lv_obj_set_style_pad_all(camera_box, 0, 0);
    lv_obj_remove_flag(camera_box, LV_OBJ_FLAG_SCROLLABLE);
    s_bambu_camera = lv_image_create(camera_box);
    lv_image_set_scale(s_bambu_camera, 280);
    lv_obj_center(s_bambu_camera);
    lv_obj_add_flag(s_bambu_camera, LV_OBJ_FLAG_HIDDEN);
    s_bambu_camera_empty = make_label(camera_box, "相机画面暂不可用", &s_ui_font, COLOR_MUTED);
    lv_obj_set_style_transform_scale(s_bambu_camera_empty, 282, 0);
    lv_obj_center(s_bambu_camera_empty);

    s_bambu_filename = make_label(safe, "--", &ui_font_detail_20, COLOR_TEXT);
    lv_obj_set_size(s_bambu_filename, 320, 28);
    lv_label_set_long_mode(s_bambu_filename, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_align(s_bambu_filename, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(s_bambu_filename, LV_ALIGN_TOP_MID, 0, 239);
    s_bambu_filament = make_label(safe, "耗材 --", &s_ui_font, COLOR_MUTED);
    lv_obj_set_style_transform_scale(s_bambu_filament, 282, 0);
    lv_obj_align(s_bambu_filament, LV_ALIGN_TOP_LEFT, 18, 270);
    s_bambu_layer = make_label(safe, "层数 --", &s_ui_font, COLOR_MUTED);
    lv_obj_set_style_transform_scale(s_bambu_layer, 282, 0);
    lv_obj_align(s_bambu_layer, LV_ALIGN_TOP_RIGHT, -18, 270);

    /* The actions intentionally sit below the layout box. Parent them to the
     * screen while preserving their existing absolute positions. */
    s_bambu_pause = lv_button_create(s_bambu_detail);
    lv_obj_set_size(s_bambu_pause, 70, 70);
    lv_obj_align(s_bambu_pause, LV_ALIGN_BOTTOM_LEFT, 144, -30);
    lv_obj_set_style_radius(s_bambu_pause, 20, 0);
    lv_obj_set_style_bg_color(s_bambu_pause, color(0x082913), 0);
    lv_obj_set_style_border_width(s_bambu_pause, 1, 0);
    lv_obj_set_style_border_color(s_bambu_pause, color(COLOR_BAMBU), 0);
    s_bambu_pause_icon = make_label(s_bambu_pause, LV_SYMBOL_PAUSE,
                                    &lv_font_montserrat_32, COLOR_BAMBU);
    lv_obj_center(s_bambu_pause_icon);
    lv_obj_add_event_cb(s_bambu_pause, bambu_pause_event, LV_EVENT_CLICKED, NULL);

    s_bambu_stop = lv_button_create(s_bambu_detail);
    lv_obj_set_size(s_bambu_stop, 70, 70);
    lv_obj_align(s_bambu_stop, LV_ALIGN_BOTTOM_RIGHT, -144, -30);
    lv_obj_set_style_radius(s_bambu_stop, 20, 0);
    lv_obj_set_style_bg_color(s_bambu_stop, color(0x311A19), 0);
    lv_obj_set_style_border_width(s_bambu_stop, 1, 0);
    lv_obj_set_style_border_color(s_bambu_stop, color(COLOR_DANGER), 0);
    s_bambu_stop_progress = lv_bar_create(s_bambu_stop);
    lv_obj_set_size(s_bambu_stop_progress, 66, 66);
    lv_obj_center(s_bambu_stop_progress);
    lv_obj_set_style_radius(s_bambu_stop_progress, 18, 0);
    lv_obj_set_style_bg_opa(s_bambu_stop_progress, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_bambu_stop_progress, color(COLOR_DANGER), LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa(s_bambu_stop_progress, LV_OPA_30, LV_PART_INDICATOR);
    lv_bar_set_value(s_bambu_stop_progress, 0, LV_ANIM_OFF);
    lv_obj_t *stop_icon = make_label(s_bambu_stop, LV_SYMBOL_STOP,
                                     &lv_font_montserrat_32, COLOR_DANGER);
    lv_obj_center(stop_icon);
    lv_obj_move_foreground(stop_icon);
    lv_obj_add_event_cb(s_bambu_stop, bambu_stop_event, LV_EVENT_ALL, NULL);
}

static void build_custom(void)
{
    s_custom = lv_obj_create(NULL);
    set_screen_background(s_custom);
    add_activity_event(s_custom);
    lv_obj_add_event_cb(s_custom, swipe_event, LV_EVENT_GESTURE, NULL);

    lv_obj_t *content = lv_obj_create(s_custom);
    lv_obj_remove_style_all(content);
    lv_obj_set_size(content, SCREEN_SIZE, SCREEN_SIZE);
    lv_obj_center(content);
    lv_obj_remove_flag(content, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(content, LV_OBJ_FLAG_CLICKABLE);

    s_custom_image = lv_image_create(content);
    lv_obj_center(s_custom_image);
    lv_obj_add_flag(s_custom_image, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(s_custom_image, LV_OBJ_FLAG_CLICKABLE);

    s_custom_ring = lv_arc_create(content);
    lv_obj_set_size(s_custom_ring, SCREEN_SIZE, SCREEN_SIZE);
    lv_obj_center(s_custom_ring);
    lv_arc_set_range(s_custom_ring, 0, 100);
    lv_arc_set_value(s_custom_ring, 100);
    lv_arc_set_bg_angles(s_custom_ring, 0, 360);
    lv_obj_set_style_arc_width(s_custom_ring, 26, LV_PART_MAIN);
    lv_obj_set_style_arc_color(s_custom_ring, color(0x2B2D28), LV_PART_MAIN);
    lv_obj_set_style_arc_width(s_custom_ring, 26, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(s_custom_ring, color(COLOR_WARNING), LV_PART_INDICATOR);
    lv_obj_remove_style(s_custom_ring, NULL, LV_PART_KNOB);
    lv_obj_remove_flag(s_custom_ring, LV_OBJ_FLAG_CLICKABLE);

    s_custom_title = make_label(content, "我的页面", &ui_font_detail_20, COLOR_MUTED);
    lv_label_set_long_mode(s_custom_title, LV_LABEL_LONG_DOT);
    lv_obj_set_width(s_custom_title, 280);
    lv_obj_set_style_text_align(s_custom_title, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(s_custom_title, LV_ALIGN_TOP_MID, 0, 54);

    s_custom_value = make_label(content, "你好，Dotii", &ui_font_detail_20, COLOR_WARNING);
    lv_label_set_long_mode(s_custom_value, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_custom_value, 304);
    lv_obj_set_style_text_align(s_custom_value, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(s_custom_value, LV_ALIGN_CENTER, 0, -52);

    s_custom_body = make_label(content, "在电脑端编辑内容，保存后会自动同步到设备。", &ui_font_detail_20, 0xC6D0CC);
    lv_label_set_long_mode(s_custom_body, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_custom_body, 300);
    lv_obj_set_style_text_align(s_custom_body, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(s_custom_body, LV_ALIGN_CENTER, 0, 32);

    s_custom_footer = make_label(content, "自定义内容", &ui_font_detail_20, COLOR_MUTED);
    lv_label_set_long_mode(s_custom_footer, LV_LABEL_LONG_DOT);
    lv_obj_set_width(s_custom_footer, 270);
    lv_obj_set_style_text_align(s_custom_footer, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(s_custom_footer, LV_ALIGN_BOTTOM_MID, 0, -54);

    /* The custom page is designed as a clean ambient canvas and intentionally
       does not show navigation dots. */
}

static lv_obj_t *make_dotii_part(lv_obj_t *parent, int width, int height,
                                 int x, int y, uint32_t part_color)
{
    lv_obj_t *part = lv_obj_create(parent);
    lv_obj_remove_style_all(part);
    lv_obj_set_size(part, width, height);
    lv_obj_align(part, LV_ALIGN_CENTER, x, y);
    lv_obj_set_style_radius(part, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(part, color(part_color), 0);
    lv_obj_set_style_bg_opa(part, LV_OPA_COVER, 0);
    lv_obj_remove_flag(part, LV_OBJ_FLAG_CLICKABLE);
    return part;
}

static uint32_t dotii_expression_duration(dotii_expression_t expression)
{
    if (expression < 0 || expression >= DOTII_EXPRESSION_COUNT) return 800;
    uint32_t duration = s_snapshot.dotii_expression_durations_ms[expression];
    return duration >= 800 && duration <= 10000 ? duration : 800;
}

static bool dotii_touch_active(void)
{
    return s_dotii_touch_started != 0 &&
           lv_tick_elaps(s_dotii_touch_started) <
               dotii_expression_duration(s_snapshot.dotii_touch_expression);
}

static uint32_t dotii_blink_cycle_position(void)
{
    const uint32_t duration = dotii_expression_duration(s_snapshot.dotii_blink_expression);
    return lv_tick_get() % (6000U + duration);
}

static bool dotii_business_animation_active(void)
{
    if (!s_snapshot.dotii_state_assigned || s_snapshot.dotii_base_idle) return false;
    if (s_snapshot.dotii_state_duration_ms == 0) return true;
    return s_dotii_state_seen &&
           lv_tick_elaps(s_dotii_state_started) < s_snapshot.dotii_state_duration_ms;
}

static bool dotii_idle_fallback_active(void)
{
    return s_snapshot.dotii_base_idle || !dotii_business_animation_active();
}

static dotii_expression_t active_dotii_expression(void)
{
    if (dotii_touch_active()) {
        return s_snapshot.dotii_touch_expression;
    }
    if (dotii_idle_fallback_active()) {
        if (lv_tick_elaps(s_last_activity) > 60000) return s_snapshot.dotii_long_idle_expression;
        if (dotii_blink_cycle_position() >= 6000U) return s_snapshot.dotii_blink_expression;
        return DOTII_EXPRESSION_IDLE_BREATH;
    }
    return s_snapshot.dotii_expression;
}

static void set_dotii_part(lv_obj_t *part, int width, int height, int x, int y,
                           uint32_t part_color, lv_opa_t opacity)
{
    /* Keep every expression in the same visual system while making the face
       occupy more of the 466 px round display. Scale dimensions and offsets
       together so animated accents retain their spacing and stay centered. */
    const int scale_num = 6;
    const int scale_den = 5;
    width = (width * scale_num + scale_den / 2) / scale_den;
    height = (height * scale_num + scale_den / 2) / scale_den;
    x = x >= 0 ? (x * scale_num + scale_den / 2) / scale_den :
                 -((-x * scale_num + scale_den / 2) / scale_den);
    y = y >= 0 ? (y * scale_num + scale_den / 2) / scale_den :
                 -((-y * scale_num + scale_den / 2) / scale_den);
    lv_obj_remove_flag(part, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_size(part, width, height);
    lv_obj_align(part, LV_ALIGN_CENTER, x, y);
    lv_obj_set_style_bg_color(part, color(part_color), 0);
    lv_obj_set_style_bg_opa(part, opacity, 0);
}

static void render_dotii_expression(void)
{
    if (s_dotii == NULL) return;
    const uint32_t warm = 0xF4EAD2;
    const uint32_t amber = 0xF2C66D;
    const uint32_t cyan = 0x55D8D0;
    const uint32_t coral = 0xFF827A;
    const dotii_expression_t expression = active_dotii_expression();
    uint32_t elapsed = lv_tick_get();
    if (dotii_touch_active() && expression == s_snapshot.dotii_touch_expression) {
        elapsed = lv_tick_elaps(s_dotii_touch_started);
    } else if (dotii_idle_fallback_active() &&
               lv_tick_elaps(s_last_activity) <= 60000 &&
               expression == s_snapshot.dotii_blink_expression &&
               dotii_blink_cycle_position() >= 6000U) {
        elapsed = dotii_blink_cycle_position() - 6000U;
    } else if (dotii_business_animation_active() && s_dotii_state_seen) {
        elapsed = lv_tick_elaps(s_dotii_state_started);
    }
    const uint32_t duration = dotii_expression_duration(expression);
    const uint32_t phase = ((elapsed % duration) * 8U) / duration;

    lv_obj_add_flag(s_dotii_accent_left, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s_dotii_accent_right, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s_dotii_accent_center, LV_OBJ_FLAG_HIDDEN);
    set_dotii_part(s_dotii_left_eye, 52, 72, -78, -30, warm, LV_OPA_COVER);
    set_dotii_part(s_dotii_right_eye, 52, 72, 78, -30, warm, LV_OPA_COVER);
    set_dotii_part(s_dotii_mouth, 56, 12, 0, 70, warm, LV_OPA_COVER);

    switch (expression) {
    case DOTII_EXPRESSION_BLINK: {
        const int eye_height = phase == 2 || phase == 3 ? 7 :
                               phase == 1 || phase == 4 ? 24 : 66;
        set_dotii_part(s_dotii_left_eye, 58, eye_height, -78, -26, warm, LV_OPA_COVER);
        set_dotii_part(s_dotii_right_eye, 58, eye_height, 78, -26, warm, LV_OPA_COVER);
        break;
    }
    case DOTII_EXPRESSION_CURIOUS: {
        const int curious = phase < 4 ? (int)phase : (int)(8 - phase);
        set_dotii_part(s_dotii_left_eye, 62, 82, -76, -34, warm, LV_OPA_COVER);
        set_dotii_part(s_dotii_right_eye, 46, 62, 82, -26, warm, LV_OPA_COVER);
        set_dotii_part(s_dotii_mouth, 20 + curious, 20 + curious, 8, 70,
                       amber, LV_OPA_COVER);
        break;
    }
    case DOTII_EXPRESSION_SLEEPY_YAWN: {
        const int yawn = phase < 4 ? (int)phase * 3 : (int)(8 - phase) * 3;
        set_dotii_part(s_dotii_left_eye, 68, 10, -76, -18, warm, LV_OPA_COVER);
        set_dotii_part(s_dotii_right_eye, 68, 10, 76, -18, warm, LV_OPA_COVER);
        set_dotii_part(s_dotii_mouth, 26 + yawn, 26 + yawn, 0, 72, amber, LV_OPA_COVER);
        break;
    }
    case DOTII_EXPRESSION_TOUCH_RESPONSE: {
        /* Keep the original tall-eye/cyan-dot character, but make the response
           visibly animate from the first short-click frame. */
        const int pulse = phase < 4 ? (int)phase * 4 : (int)(8 - phase) * 4;
        const int spread = phase < 4 ? (int)phase * 3 : (int)(8 - phase) * 3;
        set_dotii_part(s_dotii_left_eye, 62 + pulse / 2, 80 + pulse, -76, -34, warm, LV_OPA_COVER);
        set_dotii_part(s_dotii_right_eye, 62 + pulse / 2, 80 + pulse, 76, -34, warm, LV_OPA_COVER);
        set_dotii_part(s_dotii_mouth, 30 + pulse, 16 + pulse / 2, 0, 76, cyan, LV_OPA_COVER);
        set_dotii_part(s_dotii_accent_left, 13, 13, -120 - spread, 52, cyan, LV_OPA_COVER);
        set_dotii_part(s_dotii_accent_right, 13, 13, 120 + spread, 52, cyan, LV_OPA_COVER);
        break;
    }
    case DOTII_EXPRESSION_CONNECTING: {
        const lv_opa_t left_opacity = phase < 4 ? LV_OPA_COVER : (lv_opa_t)110;
        const lv_opa_t right_opacity = phase < 4 ? (lv_opa_t)110 : LV_OPA_COVER;
        set_dotii_part(s_dotii_left_eye, 48, 64, -76, -28, cyan, left_opacity);
        set_dotii_part(s_dotii_right_eye, 48, 64, 76, -28, cyan, right_opacity);
        set_dotii_part(s_dotii_accent_center, 10, 10, (int)phase * 22 - 77, 88, cyan, LV_OPA_COVER);
        break;
    }
    case DOTII_EXPRESSION_WORKING: {
        const int shift = phase < 4 ? (int)phase * 4 : (int)(8 - phase) * 4;
        set_dotii_part(s_dotii_left_eye, 48, 68, -86 + shift, -30, warm, LV_OPA_COVER);
        set_dotii_part(s_dotii_right_eye, 48, 68, 70 + shift, -30, warm, LV_OPA_COVER);
        set_dotii_part(s_dotii_mouth, 62, 10, 0, 72, cyan, LV_OPA_COVER);
        set_dotii_part(s_dotii_accent_right, 10, 10, 134, -82 + (int)phase * 7,
                       cyan, LV_OPA_COVER);
        break;
    }
    case DOTII_EXPRESSION_COMPLETE: {
        const int celebrate = phase < 4 ? (int)phase : (int)(8 - phase);
        set_dotii_part(s_dotii_left_eye, 68, 14, -76, -28, warm, LV_OPA_COVER);
        set_dotii_part(s_dotii_right_eye, 68, 14, 76, -28, warm, LV_OPA_COVER);
        set_dotii_part(s_dotii_mouth, 108 + celebrate, 20 + celebrate / 2,
                       0, 62, amber, LV_OPA_COVER);
        set_dotii_part(s_dotii_accent_left, 10 + celebrate, 10 + celebrate,
                       -132, -76, amber, LV_OPA_COVER);
        set_dotii_part(s_dotii_accent_right, 10 + celebrate, 10 + celebrate,
                       132, -76, cyan, LV_OPA_COVER);
        break;
    }
    case DOTII_EXPRESSION_FAILURE: {
        const int warning = phase < 4 ? (int)phase : (int)(8 - phase);
        set_dotii_part(s_dotii_left_eye, 58, 12, -76, -22, coral, LV_OPA_COVER);
        set_dotii_part(s_dotii_right_eye, 58, 12, 76, -22, coral, LV_OPA_COVER);
        set_dotii_part(s_dotii_mouth, 72, 12, 0, 80 + warning / 2,
                       coral, LV_OPA_COVER);
        set_dotii_part(s_dotii_accent_center, 10 + warning, 10 + warning,
                       0, 52, coral, LV_OPA_COVER);
        break;
    }
    case DOTII_EXPRESSION_IDLE_BREATH: {
        const int breath = phase < 4 ? (int)phase * 2 : (int)(8 - phase) * 2;
        set_dotii_part(s_dotii_left_eye, 52, 68 + breath, -78, -30, warm, LV_OPA_COVER);
        set_dotii_part(s_dotii_right_eye, 52, 68 + breath, 78, -30, warm, LV_OPA_COVER);
        break;
    }
    default:
        break;
    }
}

static void dotii_touch_event(lv_event_t *event)
{
    if (lv_event_get_code(event) != LV_EVENT_SHORT_CLICKED) return;
    if (s_ignore_next_dotii_click) {
        s_ignore_next_dotii_click = false;
        return;
    }
    if (s_wake_touch_in_progress) return;
    s_dotii_touch_started = lv_tick_get();
    if (s_dotii_touch_started == 0) s_dotii_touch_started = 1;
    render_dotii_expression();
}

static void dotii_timer(lv_timer_t *timer)
{
    (void)timer;
    if (s_current == s_dotii && s_screen_on) render_dotii_expression();
}

static void build_dotii(void)
{
    s_dotii = lv_obj_create(NULL);
    set_screen_background(s_dotii);
    add_activity_event(s_dotii);
    lv_obj_add_event_cb(s_dotii, swipe_event, LV_EVENT_GESTURE, NULL);
    lv_obj_add_event_cb(s_dotii, dotii_touch_event, LV_EVENT_SHORT_CLICKED, NULL);
    lv_obj_add_flag(s_dotii, LV_OBJ_FLAG_CLICKABLE);

    s_dotii_left_eye = make_dotii_part(s_dotii, 52, 72, -78, -30, 0xF4EAD2);
    s_dotii_right_eye = make_dotii_part(s_dotii, 52, 72, 78, -30, 0xF4EAD2);
    s_dotii_mouth = make_dotii_part(s_dotii, 56, 12, 0, 70, 0xF4EAD2);
    s_dotii_accent_left = make_dotii_part(s_dotii, 12, 12, -132, 52, COLOR_CYAN);
    s_dotii_accent_right = make_dotii_part(s_dotii, 12, 12, 132, 52, COLOR_CYAN);
    s_dotii_accent_center = make_dotii_part(s_dotii, 12, 12, 0, 88, COLOR_CYAN);
    /* Like the custom canvas, Dotii is an immersive ambient page and does not
       show the global navigation dots. */
    render_dotii_expression();
}

static lv_obj_t *make_quick_button(lv_obj_t *parent,
                                   const lv_image_dsc_t *icon_source, uint32_t accent)
{
    lv_obj_t *button = lv_button_create(parent);
    lv_obj_set_size(button, 78, 78);
    lv_obj_set_style_radius(button, 20, 0);
    lv_obj_set_style_bg_color(button, color(COLOR_SURFACE), 0);
    lv_obj_set_style_bg_opa(button, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(button, 1, 0);
    lv_obj_set_style_border_color(button, color(accent), 0);
    lv_obj_set_style_pad_all(button, 0, 0);
    if (icon_source != NULL) {
        lv_obj_t *icon = lv_image_create(button);
        lv_image_set_src(icon, icon_source);
        lv_obj_set_style_image_recolor(icon, color(accent), 0);
        lv_obj_set_style_image_recolor_opa(icon, LV_OPA_COVER, 0);
        lv_obj_center(icon);
    } else {
        lv_obj_t *icon = lv_obj_create(button);
        lv_obj_remove_style_all(icon);
        lv_obj_set_size(icon, 36, 36);
        lv_obj_center(icon);
        static const int16_t positions[4][2] = {{1, 1}, {20, 1}, {1, 20}, {20, 20}};
        for (size_t i = 0; i < 4; ++i) {
            lv_obj_t *tile = lv_obj_create(icon);
            lv_obj_remove_style_all(tile);
            lv_obj_set_size(tile, 15, 15);
            lv_obj_set_pos(tile, positions[i][0], positions[i][1]);
            lv_obj_set_style_border_width(tile, 3, 0);
            lv_obj_set_style_border_color(tile, color(accent), 0);
            lv_obj_set_style_border_opa(tile, LV_OPA_COVER, 0);
            lv_obj_set_style_radius(tile, 4, 0);
        }
    }
    return button;
}

static void build_control(void)
{
    s_control = lv_obj_create(NULL);
    set_screen_background(s_control);
    add_activity_event(s_control);
    lv_obj_add_event_cb(s_control, swipe_event, LV_EVENT_GESTURE, NULL);
    lv_obj_t *safe = make_safe(s_control);
    make_centered_title(safe, "控制中心", &s_ui_font, 200, 346, 2, 7);

    lv_obj_t *brightness = make_label(safe, "屏幕亮度", &s_ui_font, COLOR_MUTED);
    lv_obj_set_style_transform_scale(brightness, 282, 0);
    lv_obj_set_pos(brightness, 12, 59);
    s_brightness_slider = lv_slider_create(safe);
    lv_obj_set_size(s_brightness_slider, 330, 22);
    lv_obj_set_pos(s_brightness_slider, 14, 96);
    lv_slider_set_range(s_brightness_slider, 10, 100);
    lv_slider_set_value(s_brightness_slider, s_brightness, LV_ANIM_OFF);
    lv_obj_set_style_bg_color(s_brightness_slider, color(0x272D2C), LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_brightness_slider, color(COLOR_GREEN), LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(s_brightness_slider, color(0xE9FFF7), LV_PART_KNOB);
    lv_obj_add_event_cb(s_brightness_slider, brightness_changed, LV_EVENT_VALUE_CHANGED, NULL);

    lv_obj_t *row = lv_obj_create(safe);
    lv_obj_remove_style_all(row);
    lv_obj_set_size(row, 340, 82);
    lv_obj_set_pos(row, 9, 156);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(row, 9, 0);
    s_codex_quick = make_quick_button(row, &ui_icon_openai_36, COLOR_BLUE);
    s_bambu_quick = make_quick_button(row, &ui_icon_bambu_36, COLOR_BAMBU);
    s_custom_quick = make_quick_button(row, NULL, COLOR_WARNING);
    s_dotii_quick = make_quick_button(row, NULL, COLOR_CYAN);
    lv_obj_clean(s_dotii_quick);
    make_dotii_part(s_dotii_quick, 12, 20, -12, -2, COLOR_CYAN);
    make_dotii_part(s_dotii_quick, 12, 20, 12, -2, COLOR_CYAN);
    lv_obj_add_flag(s_custom_quick, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_event_cb(s_codex_quick, quick_clicked, LV_EVENT_CLICKED, s_main);
    lv_obj_add_event_cb(s_bambu_quick, quick_clicked, LV_EVENT_CLICKED, s_bambu_main);
    lv_obj_add_event_cb(s_custom_quick, quick_clicked, LV_EVENT_CLICKED, s_custom);
    lv_obj_add_event_cb(s_dotii_quick, quick_clicked, LV_EVENT_CLICKED, s_dotii);

    s_battery_label = make_label(safe, "电量 --", &s_ui_font, COLOR_MUTED);
    lv_obj_set_width(s_battery_label, 300);
    lv_label_set_long_mode(s_battery_label, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_align(s_battery_label, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(s_battery_label, LV_ALIGN_BOTTOM_MID, 0, -5);
}

static lv_obj_t *make_setting_card(lv_obj_t *parent, const char *title, const char *detail)
{
    lv_obj_t *card = lv_obj_create(parent);
    lv_obj_set_width(card, LV_PCT(100));
    lv_obj_set_height(card, 88);
    lv_obj_set_style_radius(card, 20, 0);
    lv_obj_set_style_bg_color(card, color(COLOR_SURFACE), 0);
    lv_obj_set_style_bg_opa(card, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(card, 0, 0);
    lv_obj_set_style_pad_all(card, 12, 0);
    lv_obj_set_flex_flow(card, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(card, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_START);
    lv_obj_set_style_pad_row(card, 4, 0);
    lv_obj_remove_flag(card, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t *title_label = make_label(card, title, &ui_font_detail_20, COLOR_TEXT);
    lv_obj_set_width(title_label, LV_PCT(100));
    lv_label_set_long_mode(title_label, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_align(title_label, LV_TEXT_ALIGN_LEFT, 0);
    lv_obj_t *detail_label = make_label(card, detail, &ui_font_detail_20, COLOR_MUTED);
    lv_label_set_long_mode(detail_label, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(detail_label, LV_PCT(100));
    lv_obj_set_style_text_align(detail_label, LV_TEXT_ALIGN_LEFT, 0);
    return detail_label;
}

static void build_settings(void)
{
    s_settings = lv_obj_create(NULL);
    set_screen_background(s_settings);
    add_activity_event(s_settings);
    lv_obj_add_event_cb(s_settings, swipe_event, LV_EVENT_GESTURE, NULL);
    lv_obj_t *safe = make_safe(s_settings);
    make_centered_title(safe, "设置", &ui_font_detail_20, 336, 256, 1, -2);

    s_settings_list = lv_obj_create(safe);
    lv_obj_remove_style_all(s_settings_list);
    lv_obj_set_size(s_settings_list, 334, 310);
    lv_obj_align(s_settings_list, LV_ALIGN_TOP_MID, 0, 30);
    lv_obj_set_flex_flow(s_settings_list, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(s_settings_list, 10, 0);
    lv_obj_set_scroll_dir(s_settings_list, LV_DIR_VER);

    lv_obj_t *refresh = lv_button_create(s_settings_list);
    lv_obj_set_width(refresh, LV_PCT(100));
    lv_obj_set_height(refresh, 54);
    lv_obj_set_style_radius(refresh, 18, 0);
    lv_obj_set_style_bg_color(refresh, color(COLOR_BLUE_DARK), 0);
    lv_obj_t *refresh_text = make_label(refresh, "立即刷新数据", &ui_font_detail_20, COLOR_TEXT);
    lv_obj_center(refresh_text);
    lv_obj_add_event_cb(refresh, refresh_clicked, LV_EVENT_CLICKED, NULL);

    s_settings_wifi = make_setting_card(s_settings_list, "网络", "未配置");
    s_settings_ip = make_setting_card(s_settings_list, "设备地址", "IP --");
    s_settings_bridge = make_setting_card(s_settings_list, "Codex 数据源", "未配置");
    char device_info[72];
    snprintf(device_info, sizeof(device_info), "Dotii %s · ESP-IDF %s",
             esp_app_get_description()->version, esp_get_idf_version());
    make_setting_card(s_settings_list, "设备", device_info);

    register_page_scroll_target(s_settings, s_settings_list);

}

static void build_power(void)
{
    s_power = lv_obj_create(NULL);
    set_screen_background(s_power);
    add_activity_event(s_power);
    lv_obj_add_event_cb(s_power, swipe_event, LV_EVENT_GESTURE, NULL);
    lv_obj_t *safe = make_safe(s_power);
    make_centered_title(safe, "电源", &s_ui_font, 200, 346, 2, 7);

    lv_obj_t *restart = lv_button_create(safe);
    lv_obj_set_size(restart, 156, 132);
    lv_obj_align(restart, LV_ALIGN_CENTER, -86, 0);
    lv_obj_set_style_radius(restart, 28, 0);
    lv_obj_set_style_bg_color(restart, color(0x173D2D), 0);
    lv_obj_set_style_bg_color(restart, color(0x214F3A), LV_STATE_PRESSED);
    lv_obj_t *restart_icon = make_label(restart, LV_SYMBOL_REFRESH, &lv_font_montserrat_32, COLOR_GREEN);
    lv_obj_align(restart_icon, LV_ALIGN_CENTER, 0, -24);
    lv_obj_t *restart_text = make_label(restart, "重新启动", &ui_font_detail_20, COLOR_GREEN);
    lv_obj_set_width(restart_text, 132);
    lv_obj_set_style_text_align(restart_text, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(restart_text, LV_ALIGN_CENTER, 0, 24);
    lv_obj_add_event_cb(restart, restart_event, LV_EVENT_ALL, NULL);

    lv_obj_t *shutdown = lv_button_create(safe);
    lv_obj_set_size(shutdown, 156, 132);
    lv_obj_align(shutdown, LV_ALIGN_CENTER, 86, 0);
    lv_obj_set_style_radius(shutdown, 28, 0);
    lv_obj_set_style_bg_color(shutdown, color(0x3A1D1D), 0);
    lv_obj_set_style_bg_color(shutdown, color(0x572725), LV_STATE_PRESSED);
    lv_obj_t *shutdown_icon = make_label(shutdown, LV_SYMBOL_POWER, &lv_font_montserrat_32, COLOR_DANGER);
    lv_obj_align(shutdown_icon, LV_ALIGN_CENTER, 0, -24);
    lv_obj_t *shutdown_text = make_label(shutdown, "关机", &ui_font_detail_20, COLOR_DANGER);
    lv_obj_set_width(shutdown_text, 132);
    lv_obj_set_style_text_align(shutdown_text, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(shutdown_text, LV_ALIGN_CENTER, 0, 24);
    lv_obj_add_event_cb(shutdown, shutdown_event, LV_EVENT_ALL, NULL);

    lv_obj_t *hint = make_label(safe, "按住 1.2 秒确认\n向右滑动取消", &ui_font_detail_20, COLOR_MUTED);
    lv_obj_set_width(hint, 330);
    lv_label_set_long_mode(hint, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_align(hint, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_line_space(hint, 6, 0);
    lv_obj_align(hint, LV_ALIGN_BOTTOM_MID, 0, -2);
}

static void update_snapshot(const codex_snapshot_t *snapshot)
{
    if (!s_dotii_state_seen || snapshot->dotii_state_token != s_dotii_state_token ||
        snapshot->dotii_state_assigned != s_dotii_state_assigned ||
        snapshot->dotii_state_duration_ms != s_dotii_state_duration_ms ||
        snapshot->dotii_expression != s_dotii_state_expression) {
        s_dotii_state_seen = true;
        s_dotii_state_token = snapshot->dotii_state_token;
        s_dotii_state_assigned = snapshot->dotii_state_assigned;
        s_dotii_state_duration_ms = snapshot->dotii_state_duration_ms;
        s_dotii_state_expression = snapshot->dotii_expression;
        s_dotii_state_started = lv_tick_get();
    }
    s_snapshot = *snapshot;
    if (snapshot->docked_rotation_tenths >= DISPLAY_ANGLE_MIN_TENTHS &&
        snapshot->docked_rotation_tenths <= DISPLAY_ANGLE_MAX_TENTHS &&
        (snapshot->docked_rotation_tenths != s_display_angle_tenths ||
         snapshot->screen_off_timeout_seconds != s_screen_off_timeout_seconds ||
         snapshot->sleep_timeout_seconds != s_sleep_timeout_seconds ||
         snapshot->charging_screen_off_timeout_seconds != s_charging_screen_off_timeout_seconds ||
         snapshot->charging_sleep_timeout_seconds != s_charging_sleep_timeout_seconds ||
         snapshot->screen_off_page != s_screen_off_page ||
         snapshot->display_revision != s_display_revision)) {
        const bool timeout_changed =
            snapshot->screen_off_timeout_seconds != s_screen_off_timeout_seconds ||
            snapshot->sleep_timeout_seconds != s_sleep_timeout_seconds ||
            snapshot->charging_screen_off_timeout_seconds != s_charging_screen_off_timeout_seconds ||
            snapshot->charging_sleep_timeout_seconds != s_charging_sleep_timeout_seconds;
        const bool screen_off_page_changed = snapshot->screen_off_page != s_screen_off_page;
        s_display_angle_tenths = snapshot->docked_rotation_tenths;
        s_screen_off_timeout_seconds = snapshot->screen_off_timeout_seconds;
        s_sleep_timeout_seconds = snapshot->sleep_timeout_seconds;
        s_charging_screen_off_timeout_seconds = snapshot->charging_screen_off_timeout_seconds;
        s_charging_sleep_timeout_seconds = snapshot->charging_sleep_timeout_seconds;
        s_screen_off_page = snapshot->screen_off_page <= DISPLAY_SCREEN_OFF_PAGE_DOTII
            ? snapshot->screen_off_page : DISPLAY_SCREEN_OFF_PAGE_NONE;
        s_display_revision = snapshot->display_revision;
        save_display_settings();
        bsp_display_transform_set_angle(s_display_angle_tenths);
        if (timeout_changed || screen_off_page_changed) {
            if (s_screen_saver_active) exit_screen_saver();
            s_last_activity = lv_tick_get();
        }
        if (s_current != NULL) lv_obj_invalidate(s_current);
    }
    if (!snapshot->valid || snapshot->stale || !snapshot->weekly_available) {
        lv_label_set_text(s_percent, "--");
        lv_arc_set_value(s_main_arc, 0);
        lv_obj_set_style_arc_color(s_main_arc, color(0x59605E), LV_PART_INDICATOR);
    } else {
        lv_label_set_text_fmt(s_percent, "%d%%", snapshot->weekly_remaining_percent);
        lv_arc_set_value(s_main_arc, snapshot->weekly_remaining_percent);
        lv_obj_set_style_arc_color(s_main_arc, color(COLOR_BLUE), LV_PART_INDICATOR);
    }

    uint32_t status_color = COLOR_MUTED;
    if (snapshot->status == CODEX_STATUS_WORKING || snapshot->status == CODEX_STATUS_COMPLETED) status_color = COLOR_GREEN;
    else if (snapshot->status == CODEX_STATUS_WAITING) status_color = COLOR_WARNING;
    else if (snapshot->status == CODEX_STATUS_FAILED) status_color = COLOR_DANGER;
    lv_label_set_text(s_status, app_state_status_text(snapshot->status));
    bool long_status = snapshot->status == CODEX_STATUS_OFFLINE;
    lv_obj_set_width(s_status_pill, long_status ? 172 : 138);
    lv_obj_set_width(s_status, long_status ? 116 : 82);
    lv_obj_set_style_text_color(s_status, color(status_color), 0);
    lv_obj_set_style_text_outline_stroke_color(s_status, color(status_color), 0);
    lv_obj_set_style_bg_color(s_status_dot, color(status_color), 0);
    lv_obj_set_style_bg_color(s_status_pill, color(status_color), 0);
    lv_obj_set_style_bg_opa(s_status_pill, LV_OPA_20, 0);

    if (!snapshot->weekly_tokens_available) lv_label_set_text(s_weekly_tokens, "--");
    else if (snapshot->weekly_tokens >= 1000) lv_label_set_text_fmt(s_weekly_tokens, "%.1fk", snapshot->weekly_tokens / 1000.0);
    else lv_label_set_text_fmt(s_weekly_tokens, "%lu", (unsigned long)snapshot->weekly_tokens);
    if (snapshot->weekly_available && snapshot->reset_date[0]) {
        const char *space = strchr(snapshot->reset_date, ' ');
        if (space != NULL) {
            lv_label_set_text_fmt(s_reset_day, "%.*s", (int)(space - snapshot->reset_date), snapshot->reset_date);
            lv_label_set_text(s_reset_time, space + 1);
        } else {
            lv_label_set_text(s_reset_day, snapshot->reset_date);
            lv_label_set_text(s_reset_time, "");
        }
    } else {
        lv_label_set_text(s_reset_day, "--");
        lv_label_set_text(s_reset_time, "");
    }

    if (!snapshot->valid || snapshot->stale || !snapshot->five_hour_available) {
        lv_label_set_text(s_plus_five_percent, "--");
        lv_arc_set_value(s_plus_five_arc, 0);
        lv_obj_set_style_arc_color(s_plus_five_arc, color(0x59605E), LV_PART_INDICATOR);
        set_usage_reset_text(s_plus_five_reset, "", true);
    } else {
        lv_label_set_text_fmt(s_plus_five_percent, "%d%%", snapshot->five_hour_remaining_percent);
        lv_arc_set_value(s_plus_five_arc, snapshot->five_hour_remaining_percent);
        lv_obj_set_style_arc_color(s_plus_five_arc, color(COLOR_ORANGE), LV_PART_INDICATOR);
        set_usage_reset_text(s_plus_five_reset, snapshot->five_hour_reset_date, true);
    }
    if (!snapshot->valid || snapshot->stale || !snapshot->weekly_available) {
        lv_label_set_text(s_plus_weekly_percent, "--");
        lv_arc_set_value(s_plus_weekly_arc, 0);
        lv_obj_set_style_arc_color(s_plus_weekly_arc, color(0x59605E), LV_PART_INDICATOR);
        set_weekly_reset_text(s_plus_weekly_reset_day, s_plus_weekly_reset, "");
    } else {
        lv_label_set_text_fmt(s_plus_weekly_percent, "%d%%", snapshot->weekly_remaining_percent);
        lv_arc_set_value(s_plus_weekly_arc, snapshot->weekly_remaining_percent);
        lv_obj_set_style_arc_color(s_plus_weekly_arc, color(COLOR_BLUE_USAGE), LV_PART_INDICATOR);
        set_weekly_reset_text(s_plus_weekly_reset_day, s_plus_weekly_reset,
                              snapshot->reset_date);
    }
    uint32_t plus_status_color = COLOR_MUTED;
    if (snapshot->status == CODEX_STATUS_WORKING || snapshot->status == CODEX_STATUS_COMPLETED) plus_status_color = COLOR_GREEN;
    else if (snapshot->status == CODEX_STATUS_WAITING) plus_status_color = COLOR_WARNING;
    else if (snapshot->status == CODEX_STATUS_FAILED) plus_status_color = COLOR_DANGER;
    lv_label_set_text(s_plus_status, app_state_status_text(snapshot->status));
    lv_obj_set_width(s_plus_status_pill, 224);
    lv_obj_set_style_text_color(s_plus_status, color(plus_status_color), 0);
    lv_obj_set_style_text_outline_stroke_color(s_plus_status, color(plus_status_color), 0);
    lv_obj_set_style_bg_color(s_plus_status_pill, color(plus_status_color), 0);
    lv_obj_set_style_bg_opa(s_plus_status_pill, LV_OPA_20, 0);
    render_codex_detail();

    bool bambu_online = snapshot->bambu_configured && snapshot->bambu_connected;
    uint32_t bambu_status_color = !bambu_online ? COLOR_MUTED :
        snapshot->bambu_status == BAMBU_STATUS_FAULT ? COLOR_DANGER :
        snapshot->bambu_status == BAMBU_STATUS_PAUSED ? COLOR_WARNING : COLOR_BAMBU;
    lv_arc_set_value(s_bambu_arc, bambu_online ? snapshot->bambu_progress : 0);
    lv_obj_set_style_arc_color(s_bambu_arc,
                               color(snapshot->bambu_status == BAMBU_STATUS_FAULT ? COLOR_DANGER :
                                     snapshot->bambu_status == BAMBU_STATUS_PAUSED ? COLOR_WARNING : COLOR_BAMBU),
                               LV_PART_INDICATOR);
    lv_label_set_text(s_bambu_status,
                      snapshot->bambu_status_text[0] ? snapshot->bambu_status_text :
                      app_state_bambu_status_text(snapshot->bambu_status));
    lv_obj_set_style_text_color(s_bambu_status, color(bambu_status_color), 0);
    lv_obj_set_style_text_outline_stroke_color(s_bambu_status, color(bambu_status_color), 0);
    lv_obj_set_style_bg_color(s_bambu_status_dot, color(bambu_status_color), 0);
    lv_obj_set_style_bg_color(s_bambu_status_pill, color(bambu_status_color), 0);
    lv_obj_set_style_bg_opa(s_bambu_status_pill, LV_OPA_20, 0);
    if (bambu_online) lv_label_set_text_fmt(s_bambu_percent, "%u%%", snapshot->bambu_progress);
    else lv_label_set_text(s_bambu_percent, "--");
    if (bambu_online && snapshot->bambu_remaining_minutes > 0) {
        if (snapshot->bambu_remaining_minutes >= 60) {
            lv_label_set_text_fmt(s_bambu_remaining, "%uh%02um",
                                  snapshot->bambu_remaining_minutes / 60,
                                  snapshot->bambu_remaining_minutes % 60);
        } else {
            lv_label_set_text_fmt(s_bambu_remaining, "%um", snapshot->bambu_remaining_minutes);
        }
    } else lv_label_set_text(s_bambu_remaining, "--");
    struct tm finish_local;
    if (snapshot->bambu_finish_at > 0 && localtime_r(&snapshot->bambu_finish_at, &finish_local) != NULL) {
        lv_label_set_text_fmt(s_bambu_finish, "%02d:%02d", finish_local.tm_hour, finish_local.tm_min);
    } else lv_label_set_text(s_bambu_finish, "--");
    lv_label_set_text(s_bambu_filename, snapshot->bambu_filename[0] ? snapshot->bambu_filename : "--");
    lv_label_set_text_fmt(s_bambu_filament, "耗材 %s",
                          snapshot->bambu_filament[0] ? snapshot->bambu_filament : "--");
    if (snapshot->bambu_layer_total > 0) {
        lv_label_set_text_fmt(s_bambu_layer, "层数 %u/%u",
                              snapshot->bambu_layer_current, snapshot->bambu_layer_total);
    } else lv_label_set_text(s_bambu_layer, "层数 --");
    lv_label_set_text(s_bambu_pause_icon,
                      snapshot->bambu_status == BAMBU_STATUS_PAUSED ? LV_SYMBOL_PLAY : LV_SYMBOL_PAUSE);
    bool can_pause = bambu_online && (snapshot->bambu_status == BAMBU_STATUS_PRINTING ||
                                      snapshot->bambu_status == BAMBU_STATUS_PAUSED);
    bool can_stop = bambu_online && snapshot->bambu_commandable &&
                    (snapshot->bambu_status == BAMBU_STATUS_PRINTING ||
                     snapshot->bambu_status == BAMBU_STATUS_PAUSED ||
                     snapshot->bambu_status == BAMBU_STATUS_PREPARING);
    if (can_pause) lv_obj_remove_state(s_bambu_pause, LV_STATE_DISABLED);
    else lv_obj_add_state(s_bambu_pause, LV_STATE_DISABLED);
    if (can_stop) lv_obj_remove_state(s_bambu_stop, LV_STATE_DISABLED);
    else lv_obj_add_state(s_bambu_stop, LV_STATE_DISABLED);

    const uint8_t *camera = snapshot->bambu_camera_available ?
        connectivity_bambu_camera_data(snapshot->bambu_camera_revision) : NULL;
    if (camera != NULL) {
        if (s_bambu_camera_revision != snapshot->bambu_camera_revision) {
            if (s_bambu_camera_revision != 0) lv_image_cache_drop(&s_bambu_camera_dsc);
            memset(&s_bambu_camera_dsc, 0, sizeof(s_bambu_camera_dsc));
            s_bambu_camera_dsc.header.magic = LV_IMAGE_HEADER_MAGIC;
            s_bambu_camera_dsc.header.cf = LV_COLOR_FORMAT_RGB565;
            s_bambu_camera_dsc.header.w = 320;
            s_bambu_camera_dsc.header.h = 180;
            s_bambu_camera_dsc.header.stride = 320 * 2;
            s_bambu_camera_dsc.data_size = 320 * 180 * 2;
            s_bambu_camera_dsc.data = camera;
            lv_image_set_src(s_bambu_camera, &s_bambu_camera_dsc);
            s_bambu_camera_revision = snapshot->bambu_camera_revision;
        }
        lv_obj_remove_flag(s_bambu_camera, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_bambu_camera_empty, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(s_bambu_camera, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(s_bambu_camera_empty, LV_OBJ_FLAG_HIDDEN);
    }

    uint32_t custom_accent = snapshot->custom_accent ? snapshot->custom_accent : COLOR_WARNING;
    lv_label_set_text(s_custom_title, snapshot->custom_title[0] ? snapshot->custom_title : "我的页面");
    lv_label_set_text(s_custom_value, snapshot->custom_value[0] ? snapshot->custom_value : "--");
    lv_label_set_text(s_custom_body, snapshot->custom_body[0] ? snapshot->custom_body : "暂无说明");
    lv_label_set_text(s_custom_footer, snapshot->custom_footer[0] ? snapshot->custom_footer : "Dotii 自定义");
    lv_obj_set_style_text_color(s_custom_value, color(custom_accent), 0);
    lv_obj_set_style_arc_color(s_custom_ring, color(snapshot->custom_ring_start), LV_PART_INDICATOR);
    lv_arc_set_value(s_custom_ring, 100);
    const uint8_t *custom_image = snapshot->custom_image_available ?
        connectivity_custom_image_data(snapshot->custom_image_revision) : NULL;
    if (custom_image != NULL) {
        if (s_custom_image_revision != snapshot->custom_image_revision) {
            if (s_custom_image_revision != 0) {
                lv_image_cache_drop(&s_custom_image_dsc);
            }
            memset(&s_custom_image_dsc, 0, sizeof(s_custom_image_dsc));
            s_custom_image_dsc.header.magic = LV_IMAGE_HEADER_MAGIC;
            s_custom_image_dsc.header.cf = LV_COLOR_FORMAT_RGB565;
            s_custom_image_dsc.header.w = SCREEN_SIZE;
            s_custom_image_dsc.header.h = SCREEN_SIZE;
            s_custom_image_dsc.header.stride = SCREEN_SIZE * 2;
            s_custom_image_dsc.data_size = SCREEN_SIZE * SCREEN_SIZE * 2;
            s_custom_image_dsc.data = custom_image;
            lv_image_set_src(s_custom_image, &s_custom_image_dsc);
            s_custom_image_revision = snapshot->custom_image_revision;
        }
        lv_obj_remove_flag(s_custom_image, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_custom_ring, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_custom_title, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_custom_value, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_custom_body, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_custom_footer, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(s_custom_image, LV_OBJ_FLAG_HIDDEN);
        if (snapshot->custom_ring_enabled) lv_obj_remove_flag(s_custom_ring, LV_OBJ_FLAG_HIDDEN);
        else lv_obj_add_flag(s_custom_ring, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(s_custom_title, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(s_custom_value, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(s_custom_body, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(s_custom_footer, LV_OBJ_FLAG_HIDDEN);
    }
    if (snapshot->codex_enabled) lv_obj_remove_flag(s_codex_quick, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(s_codex_quick, LV_OBJ_FLAG_HIDDEN);
    if (snapshot->bambu_enabled) lv_obj_remove_flag(s_bambu_quick, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(s_bambu_quick, LV_OBJ_FLAG_HIDDEN);
    if (snapshot->custom_enabled) lv_obj_remove_flag(s_custom_quick, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(s_custom_quick, LV_OBJ_FLAG_HIDDEN);
    if (snapshot->dotii_enabled) lv_obj_remove_flag(s_dotii_quick, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(s_dotii_quick, LV_OBJ_FLAG_HIDDEN);
    render_dotii_expression();
    update_page_dots();

    if ((s_current == s_main || s_current == s_plus_main) && s_current != page_screen(0)) {
        load_screen(page_screen(0), false);
    } else if (!s_screen_saver_active && !screen_enabled(s_current)) {
        load_screen(first_enabled_screen(), false);
    }
}

static void ui_timer(lv_timer_t *timer)
{
    (void)timer;
    if (xQueueReceive(s_snapshot_queue, &s_snapshot, 0) == pdTRUE) update_snapshot(&s_snapshot);

    time_t now = time(NULL);
    struct tm local;
    localtime_r(&now, &local);
    char clock[8];
    if (local.tm_year >= 120) snprintf(clock, sizeof(clock), "%02d:%02d", local.tm_hour, local.tm_min);
    else strlcpy(clock, "--:--", sizeof(clock));
    lv_label_set_text(s_time_main, clock);
    lv_label_set_text(s_plus_time, clock);
    lv_label_set_text(s_time_detail, clock);
    lv_label_set_text(s_time_bambu_main, clock);
    lv_label_set_text(s_time_bambu_detail, clock);

    char summary[80];
    connectivity_get_summary(summary, sizeof(summary));
    lv_label_set_text(s_settings_wifi, summary);
    connectivity_get_ip(summary, sizeof(summary));
    lv_label_set_text_fmt(s_settings_ip, "IP %s", summary);
    connectivity_get_bridge_summary(summary, sizeof(summary));
    lv_label_set_text(s_settings_bridge, summary);

    uint32_t inactive_ms = lv_tick_elaps(s_last_activity);
    if (s_ignore_next_dotii_click && inactive_ms > 1000) {
        s_ignore_next_dotii_click = false;
    }
    const uint32_t active_screen_off_timeout = s_external_power ?
        s_charging_screen_off_timeout_seconds : s_screen_off_timeout_seconds;
    const uint32_t active_sleep_timeout = s_external_power ?
        s_charging_sleep_timeout_seconds : s_sleep_timeout_seconds;
    if (s_screen_on && !s_screen_saver_active && active_screen_off_timeout > 0 &&
        inactive_ms > active_screen_off_timeout * 1000U) {
        if (!enter_screen_saver()) screen_off();
    }
    if (active_sleep_timeout > 0 && inactive_ms > active_sleep_timeout * 1000U) {
        s_last_activity = lv_tick_get();
        board_input_request_sleep();
    }
}

void state_ui_start(QueueHandle_t snapshot_queue)
{
    s_snapshot_queue = snapshot_queue;
    /* Conversation text and its render cache grow on demand in PSRAM so a
       complete Codex reply is not truncated to a fixed character budget. */
    s_detail_task = heap_caps_calloc(1, sizeof(*s_detail_task),
                                     MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    load_persisted_settings();
    ESP_ERROR_CHECK(bsp_display_transform_set_angle(s_display_angle_tenths));
    s_ui_font = ui_font_fixed_20;
    s_ui_font.fallback = &ui_font_detail_20;
    build_main();
    build_plus_main();
    build_detail();
    build_bambu();
    build_custom();
    build_dotii();
    build_settings();
    build_control();
    build_power();
    s_current = page_screen(0);
    s_last_activity = lv_tick_get();
    lv_obj_invalidate(s_current);
    /* Push one complete first frame while the caller still owns the BSP LVGL
       lock.  Subsequent timer updates can then use partial refresh safely. */
    lv_refr_now(NULL);
    /* One-second updates are sufficient for the clock and avoid redrawing the
       full AMOLED frame twice for the same displayed second. */
    lv_timer_create(ui_timer, 1000, NULL);
    /* The compact geometric character uses eight 100 ms phases. */
    lv_timer_create(dotii_timer, 100, NULL);
}

void state_ui_button_a_short(void)
{
    if (s_screen_saver_active) {
        exit_screen_saver();
        return;
    }
    if (!s_screen_on) {
        screen_wake();
        return;
    }
    if (s_current == s_power) return;
    if (s_current == s_control) {
        load_screen_with_transition(enabled_return_screen(s_before_control), TRANSITION_CONTROL_UP);
        return;
    }
    if (s_current == s_settings) {
        load_screen(first_enabled_screen(), false);
        return;
    }
    int current_page = (s_current == s_main || s_current == s_plus_main) ? 0 :
        s_current == s_bambu_main ? 1 : s_current == s_custom ? 2 :
        s_current == s_dotii ? 3 : -1;
    if (current_page < 0) {
        load_screen(first_enabled_screen(), true);
        return;
    }
    for (uint8_t offset = 1; offset <= PAGE_COUNT; ++offset) {
        uint8_t next = (current_page + offset) % PAGE_COUNT;
        if (page_enabled(next)) {
            load_screen(page_screen(next), true);
            return;
        }
    }
}

void state_ui_button_a_long(void)
{
    if (s_screen_saver_active) {
        exit_screen_saver();
        return;
    }
    if (!s_screen_on) {
        screen_wake();
        return;
    }
    if (s_current == s_power) return;
    if (s_current == s_settings) {
        load_screen(first_enabled_screen(), false);
    } else {
        load_screen(s_settings, true);
    }
}

void state_ui_button_b_short(void)
{
    if (s_screen_saver_active) {
        exit_screen_saver();
        return;
    }
    if (!s_screen_on) {
        screen_wake();
        return;
    }
    if (enter_screen_saver()) return;
    s_last_activity = lv_tick_get();
    screen_off();
}

void state_ui_button_b_long(void)
{
    if (s_screen_saver_active) {
        exit_screen_saver();
        return;
    }
    if (!s_screen_on) {
        screen_wake();
        return;
    }
    if (s_current == s_power) return;
    s_before_power = s_current;
    load_screen_with_transition(s_power, TRANSITION_DIRECT);
}

void state_ui_set_power(uint8_t percent, bool external_power, bool charging)
{
    if (s_external_power != external_power) {
        s_external_power = external_power;
        s_last_activity = lv_tick_get();
    }
    s_battery = percent;
    if (percent <= 100) {
        lv_label_set_text_fmt(s_battery_label,
                              charging ? "电量 %u%% · 充电中" :
                              external_power ? "电量 %u%% · 充电中" : "电量 %u%%",
                              percent);
    } else {
        lv_label_set_text(s_battery_label,
                          charging ? "电量 -- · 充电中" :
                          external_power ? "电量 -- · 充电中" : "电量 --");
    }
}
