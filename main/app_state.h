#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <time.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

typedef enum {
    CODEX_STATUS_WORKING = 0,
    CODEX_STATUS_WAITING,
    CODEX_STATUS_COMPLETED,
    CODEX_STATUS_FAILED,
    CODEX_STATUS_IDLE,
    CODEX_STATUS_OFFLINE,
} codex_task_status_t;

typedef enum {
    CODEX_CONVERSATION_EMPTY = 0,
    CODEX_CONVERSATION_PROGRESS,
    CODEX_CONVERSATION_HISTORY,
    CODEX_CONVERSATION_FINAL,
} codex_conversation_mode_t;

typedef enum {
    BAMBU_STATUS_OFFLINE = 0,
    BAMBU_STATUS_IDLE,
    BAMBU_STATUS_PREPARING,
    BAMBU_STATUS_PRINTING,
    BAMBU_STATUS_PAUSED,
    BAMBU_STATUS_COMPLETED,
    BAMBU_STATUS_CANCELLING,
    BAMBU_STATUS_FAULT,
} bambu_status_t;

typedef enum {
    DOTII_EXPRESSION_IDLE_BREATH = 0,
    DOTII_EXPRESSION_BLINK,
    DOTII_EXPRESSION_CURIOUS,
    DOTII_EXPRESSION_SLEEPY_YAWN,
    DOTII_EXPRESSION_TOUCH_RESPONSE,
    DOTII_EXPRESSION_CONNECTING,
    DOTII_EXPRESSION_WORKING,
    DOTII_EXPRESSION_COMPLETE,
    DOTII_EXPRESSION_FAILURE,
    DOTII_EXPRESSION_COUNT,
} dotii_expression_t;

typedef enum {
    DISPLAY_SCREEN_OFF_PAGE_NONE = 0,
    DISPLAY_SCREEN_OFF_PAGE_CUSTOM,
    DISPLAY_SCREEN_OFF_PAGE_DOTII,
} display_screen_off_page_t;

#define CODEX_TASK_DETAIL_MAX 6

typedef struct {
    codex_task_status_t status;
    codex_conversation_mode_t conversation_mode;
    char thread_id[64];
    char title[96];
    char *last_user_message;
    char *conversation_text;
    uint32_t duration_seconds;
    uint32_t message_count;
    time_t started_at;
    time_t updated_at;
} codex_task_detail_t;

typedef struct {
    bool valid;
    bool preview_data;
    bool source_online;
    bool stale;
    bool five_hour_available;
    bool weekly_available;
    bool weekly_tokens_available;
    bool task_tokens_available;
    codex_conversation_mode_t conversation_mode;
    bool codex_enabled;
    bool bambu_enabled;
    bool custom_enabled;
    bool dotii_enabled;
    bool custom_image_available;
    bool custom_ring_enabled;
    bool codex_ui_dual_limit;
    bool bambu_configured;
    bool bambu_connected;
    bool bambu_commandable;
    bool bambu_camera_available;
    bool dotii_base_idle;
    bool dotii_state_assigned;
    uint32_t display_revision;
    int16_t docked_rotation_tenths;
    uint32_t screen_off_timeout_seconds;
    uint32_t sleep_timeout_seconds;
    uint32_t charging_screen_off_timeout_seconds;
    uint32_t charging_sleep_timeout_seconds;
    display_screen_off_page_t screen_off_page;
    int five_hour_remaining_percent;
    int weekly_remaining_percent;
    uint32_t weekly_tokens;
    char five_hour_reset_date[16];
    char reset_date[16];
    char plan_type[32];
    codex_task_status_t status;
    char title[96];
    char last_user_message[1536];
    char last_assistant_message[256];
    char conversation_text[3073];
    char current_action[128];
    uint32_t duration_seconds;
    uint32_t message_count;
    uint16_t conversation_message_count;
    uint32_t task_tokens;
    uint16_t plan_completed;
    uint16_t plan_total;
    uint32_t custom_accent;
    uint32_t custom_ring_start;
    uint32_t custom_ring_end;
    uint32_t custom_image_revision;
    uint32_t custom_image_size;
    bambu_status_t bambu_status;
    dotii_expression_t dotii_expression;
    uint32_t dotii_state_duration_ms;
    uint32_t dotii_state_token;
    dotii_expression_t dotii_touch_expression;
    dotii_expression_t dotii_blink_expression;
    dotii_expression_t dotii_long_idle_expression;
    uint16_t dotii_expression_durations_ms[DOTII_EXPRESSION_COUNT];
    uint8_t bambu_progress;
    uint16_t bambu_remaining_minutes;
    uint16_t bambu_layer_current;
    uint16_t bambu_layer_total;
    int16_t bambu_nozzle_temperature;
    int16_t bambu_bed_temperature;
    uint32_t bambu_camera_revision;
    uint32_t bambu_camera_size;
    time_t bambu_finish_at;
    time_t bambu_updated_at;
    char bambu_name[41];
    char bambu_status_text[17];
    char bambu_filename[96];
    char bambu_filament[32];
    char custom_title[49];
    char custom_value[97];
    char custom_body[257];
    char custom_footer[97];
    time_t task_updated_at;
    time_t generated_at;
} codex_snapshot_t;

QueueHandle_t app_state_queue_create(void);
void app_state_publish(const codex_snapshot_t *snapshot);
void app_state_tasks_publish(const codex_task_detail_t *tasks, size_t count);
size_t app_state_task_count(void);
bool app_state_task_copy(size_t index, codex_task_detail_t *task);
void app_state_make_preview(codex_snapshot_t *snapshot);
const char *app_state_status_text(codex_task_status_t status);
codex_task_status_t app_state_status_from_string(const char *status);
const char *app_state_bambu_status_text(bambu_status_t status);
bambu_status_t app_state_bambu_status_from_string(const char *status);
dotii_expression_t app_state_dotii_expression_from_string(const char *expression);
