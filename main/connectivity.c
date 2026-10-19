#include "connectivity.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/time.h>
#include "esp_mac.h"
#include "esp_app_desc.h"
#include "esp_timer.h"
#include "esp_rom_crc.h"
#include "esp_system.h"
#include "mbedtls/base64.h"
#include "nvs.h"
#include "usb_transport.h"
#include "ui_language.h"

#include "app_state.h"
#include "cJSON.h"
#include "device_config.h"
#include "esp_crt_bundle.h"
#include "esp_attr.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_netif_sntp.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "sdkconfig.h"

#define WIFI_CONNECTED_BIT BIT0
#define REFRESH_REQUESTED_BIT BIT1
#define HTTP_BODY_INITIAL_CAPACITY 49152
#define CUSTOM_IMAGE_WIDTH 466
#define CUSTOM_IMAGE_HEIGHT 466
#define CUSTOM_IMAGE_BYTES (CUSTOM_IMAGE_WIDTH * CUSTOM_IMAGE_HEIGHT * 2)
#define BAMBU_CAMERA_WIDTH 320
#define BAMBU_CAMERA_HEIGHT 180
#define BAMBU_CAMERA_BYTES (BAMBU_CAMERA_WIDTH * BAMBU_CAMERA_HEIGHT * 2)

static const char *TAG = "connectivity";
static EventGroupHandle_t s_events;
static bool s_wifi_connected;
static bool s_bridge_online;
static uint8_t s_bridge_failures;
static char s_ip[24] = "--";
static char s_bridge_note[48] = "正在启动";
/* Snapshot payloads are large and do not participate in DMA. Keep them in
   PSRAM so the Wi-Fi driver retains enough internal RAM for its RX buffers. */
EXT_RAM_BSS_ATTR static codex_snapshot_t s_last_snapshot;
EXT_RAM_BSS_ATTR static codex_snapshot_t s_work_snapshot;
static bool s_have_snapshot;
static int64_t s_usb_last_sync;
static char s_connection_mode[8] = "auto";
static char s_device_id[16];
static bool s_wifi_bridge_online;
static int64_t s_last_wifi_poll;
static char s_usb_token[65];
static uint32_t s_image_offset[2];
static uint32_t s_image_revision[2];
static uint32_t s_image_crc[2];
static int s_image_slot[2];
EXT_RAM_BSS_ATTR static uint8_t s_usb_decoded[4096];

bool connectivity_usb_active(void)
{
    return strcmp(s_connection_mode, "wifi") != 0 && s_usb_last_sync > 0 &&
           esp_timer_get_time() - s_usb_last_sync < 12000000;
}

static uint8_t *s_custom_image_buffers[2];
static uint32_t s_custom_image_revisions[2];
static int s_custom_image_active = -1;
static uint8_t *s_bambu_camera_buffers[2];
static uint32_t s_bambu_camera_revisions[2];
static int s_bambu_camera_active = -1;
static QueueHandle_t s_bambu_command_queue;
static codex_task_detail_t *s_work_tasks;
static size_t s_work_task_count;

typedef enum {
    BAMBU_COMMAND_PAUSE = 1,
    BAMBU_COMMAND_RESUME,
    BAMBU_COMMAND_STOP,
} bambu_command_t;

typedef struct {
    char *body;
    size_t capacity;
    size_t length;
    bool overflow;
} http_body_t;

static void blacken_custom_frame_near_black_edge(uint8_t *data)
{
    const int edge = 12;
    for (int y = 0; y < CUSTOM_IMAGE_HEIGHT; ++y) {
        for (int x = 0; x < CUSTOM_IMAGE_WIDTH; ++x) {
            if (x >= edge && x < CUSTOM_IMAGE_WIDTH - edge &&
                y >= edge && y < CUSTOM_IMAGE_HEIGHT - edge) {
                continue;
            }
            size_t offset = ((size_t)y * CUSTOM_IMAGE_WIDTH + x) * 2;
            uint16_t pixel = (uint16_t)data[offset] | ((uint16_t)data[offset + 1] << 8);
            uint16_t red = (pixel >> 11) & 0x1F;
            uint16_t green = (pixel >> 5) & 0x3F;
            uint16_t blue = pixel & 0x1F;
            if (red <= 1 && green <= 2 && blue <= 1) {
                data[offset] = 0;
                data[offset + 1] = 0;
            }
        }
    }
}

typedef struct {
    uint8_t *data;
    size_t capacity;
    size_t length;
    bool overflow;
} image_body_t;

static void copy_json_string(cJSON *parent, const char *name, char *destination, size_t destination_size)
{
    cJSON *item = cJSON_GetObjectItemCaseSensitive(parent, name);
    if (cJSON_IsString(item) && item->valuestring != NULL) {
        strlcpy(destination, item->valuestring, destination_size);
    }
}

static size_t utf8_char_length(const char *text, size_t available)
{
    unsigned char lead = (unsigned char)text[0];
    size_t length = lead < 0x80 ? 1 :
                    (lead & 0xE0) == 0xC0 ? 2 :
                    (lead & 0xF0) == 0xE0 ? 3 :
                    (lead & 0xF8) == 0xF0 ? 4 : 1;
    if (length > available) return 0;
    for (size_t index = 1; index < length; ++index) {
        if (((unsigned char)text[index] & 0xC0) != 0x80) return 1;
    }
    return length;
}

static bool plain_append(char *destination, size_t destination_size, size_t *out,
                         const char *text, size_t length)
{
    for (size_t index = 0; index < length;) {
        size_t character_length = utf8_char_length(text + index, length - index);
        if (character_length == 0 || *out + character_length >= destination_size) return false;
        memcpy(destination + *out, text + index, character_length);
        *out += character_length;
        index += character_length;
    }
    return true;
}

static const char *find_bounded(const char *text, size_t length, char needle)
{
    return memchr(text, needle, length);
}

static bool is_table_rule(const char *line, size_t length)
{
    size_t dashes = 0;
    for (size_t index = 0; index < length; ++index) {
        char ch = line[index];
        if (ch == '-') dashes++;
        else if (ch != '|' && ch != ':' && ch != ' ' && ch != '\t') return false;
    }
    return dashes >= 3;
}

/* LVGL labels intentionally stay plain-text.  Convert common Markdown
   structures into a compact reading view and never split a UTF-8 character
   when the small round-screen buffers fill. */
static void markdown_to_plain(const char *source, char *destination, size_t destination_size)
{
    if (destination_size == 0) return;
    destination[0] = '\0';
    if (source == NULL) return;

    size_t out = 0;
    bool in_code_fence = false;
    const char *line = source;
    while (*line != '\0' && out + 1 < destination_size) {
        const char *newline = strchr(line, '\n');
        size_t length = newline != NULL ? (size_t)(newline - line) : strlen(line);
        if (length > 0 && line[length - 1] == '\r') length--;

        size_t start = 0;
        while (start < length && (line[start] == ' ' || line[start] == '\t')) start++;
        size_t end = length;
        while (end > start && (line[end - 1] == ' ' || line[end - 1] == '\t')) end--;

        bool fence = end - start >= 3 &&
                     ((line[start] == '`' && line[start + 1] == '`' && line[start + 2] == '`') ||
                      (line[start] == '~' && line[start + 1] == '~' && line[start + 2] == '~'));
        if (fence) {
            if (!in_code_fence) {
                if (out > 0 && destination[out - 1] != '\n') plain_append(destination, destination_size, &out, "\n", 1);
                plain_append(destination, destination_size, &out, "代码片段", strlen("代码片段"));
            }
            in_code_fence = !in_code_fence;
            goto next_line;
        }
        if (in_code_fence || start == end || is_table_rule(line + start, end - start)) goto next_line;

        if (out > 0 && destination[out - 1] != '\n' &&
            !plain_append(destination, destination_size, &out, "\n", 1)) break;

        while (start < end && line[start] == '#') start++;
        if (start < end && line[start] == ' ') start++;
        if (start < end && line[start] == '|') start++;
        if (end > start && line[end - 1] == '|') end--;

        if (start + 1 < end && (line[start] == '-' || line[start] == '*' || line[start] == '+') &&
            line[start + 1] == ' ') {
            start += 2;
            plain_append(destination, destination_size, &out, "• ", strlen("• "));
        } else if (start + 1 < end && line[start] == '>' && line[start + 1] == ' ') {
            start += 2;
            plain_append(destination, destination_size, &out, "引用：", strlen("引用："));
        }
        if (start + 2 < end && line[start] == '[' && line[start + 2] == ']' &&
            (line[start + 1] == ' ' || line[start + 1] == 'x' || line[start + 1] == 'X')) {
            plain_append(destination, destination_size, &out,
                         line[start + 1] == ' ' ? "待办 " : "完成 ",
                         strlen(line[start + 1] == ' ' ? "待办 " : "完成 "));
            start += 3;
            if (start < end && line[start] == ' ') start++;
        }

        for (size_t index = start; index < end && out + 1 < destination_size;) {
            char ch = line[index];
            if (ch == '\\' && index + 1 < end) {
                index++;
                size_t character_length = utf8_char_length(line + index, end - index);
                if (character_length == 0 || !plain_append(destination, destination_size, &out,
                                                            line + index, character_length)) break;
                index += character_length;
                continue;
            }
            if (ch == '!' && index + 1 < end && line[index + 1] == '[') {
                index++;
                plain_append(destination, destination_size, &out, "图片：", strlen("图片："));
                ch = '[';
            }
            if (ch == '[') {
                const char *close = find_bounded(line + index + 1, end - index - 1, ']');
                if (close != NULL && close + 1 < line + end && close[1] == '(') {
                    const char *url_end = find_bounded(close + 2, (size_t)(line + end - close - 2), ')');
                    if (url_end != NULL) {
                        size_t label_length = (size_t)(close - line - index - 1);
                        if (!plain_append(destination, destination_size, &out, line + index + 1, label_length)) break;
                        index = (size_t)(url_end - line) + 1;
                        continue;
                    }
                }
            }
            if (ch == '<') {
                const char *close = find_bounded(line + index + 1, end - index - 1, '>');
                if (close != NULL) {
                    if (strncmp(line + index + 1, "http://", 7) == 0 ||
                        strncmp(line + index + 1, "https://", 8) == 0) {
                        plain_append(destination, destination_size, &out, "链接", strlen("链接"));
                    }
                    index = (size_t)(close - line) + 1;
                    continue;
                }
            }
            if (ch == '`' || ch == '*' || ch == '~' ||
                (ch == '_' && index + 1 < end && line[index + 1] == '_')) {
                index += ch == '_' ? 2 : 1;
                continue;
            }
            if (ch == '|') {
                if (!plain_append(destination, destination_size, &out, " · ", strlen(" · "))) break;
                index++;
                continue;
            }
            if (ch == '\t') ch = ' ';
            if (ch == ' ' && out > 0 && destination[out - 1] == ' ') {
                index++;
                continue;
            }
            size_t character_length = utf8_char_length(line + index, end - index);
            if (character_length == 0 || !plain_append(destination, destination_size, &out,
                                                        line + index, character_length)) break;
            index += character_length;
        }

next_line:
        if (newline == NULL) break;
        line = newline + 1;
    }
    while (out > 0 && (destination[out - 1] == ' ' || destination[out - 1] == '\n')) out--;
    destination[out] = '\0';
}

static void copy_json_plain(cJSON *parent, const char *name, char *destination, size_t destination_size)
{
    cJSON *item = cJSON_GetObjectItemCaseSensitive(parent, name);
    if (cJSON_IsString(item) && item->valuestring != NULL) {
        markdown_to_plain(item->valuestring, destination, destination_size);
    }
}

static char *plain_text_external(const char *source)
{
    if (source == NULL || source[0] == '\0') return NULL;
    size_t capacity = strlen(source) + 1;
    char *plain = heap_caps_malloc(capacity, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (plain != NULL) markdown_to_plain(source, plain, capacity);
    return plain;
}

static char *copy_json_plain_external(cJSON *parent, const char *name)
{
    cJSON *item = cJSON_GetObjectItemCaseSensitive(parent, name);
    return cJSON_IsString(item) ? plain_text_external(item->valuestring) : NULL;
}

static bool append_plain_external(char **destination, const char *source, bool separator)
{
    if (source == NULL || source[0] == '\0') return true;
    size_t previous = *destination != NULL ? strlen(*destination) : 0;
    size_t extra = strlen(source) + (separator && previous > 0 ? 1 : 0);
    char *expanded = heap_caps_realloc(*destination, previous + extra + 1,
                                       MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (expanded == NULL) return false;
    *destination = expanded;
    char *write_at = expanded + previous;
    if (separator && previous > 0) *write_at++ = '\x1E';
    markdown_to_plain(source, write_at, strlen(source) + 1);
    return true;
}

static void clear_codex_task_detail(codex_task_detail_t *detail)
{
    if (detail == NULL) return;
    heap_caps_free(detail->last_user_message);
    heap_caps_free(detail->conversation_text);
    memset(detail, 0, sizeof(*detail));
}

static codex_conversation_mode_t conversation_mode_from_json(cJSON *task,
                                                              codex_task_status_t status)
{
    cJSON *mode = cJSON_GetObjectItemCaseSensitive(task, "conversation_mode");
    if (cJSON_IsString(mode) && mode->valuestring != NULL) {
        if (strcmp(mode->valuestring, "progress") == 0) return CODEX_CONVERSATION_PROGRESS;
        if (strcmp(mode->valuestring, "history") == 0) return CODEX_CONVERSATION_HISTORY;
        if (strcmp(mode->valuestring, "final") == 0) return CODEX_CONVERSATION_FINAL;
        return CODEX_CONVERSATION_EMPTY;
    }
    return status == CODEX_STATUS_WORKING || status == CODEX_STATUS_WAITING ?
        CODEX_CONVERSATION_PROGRESS : CODEX_CONVERSATION_FINAL;
}

static void copy_codex_messages(cJSON *task, codex_snapshot_t *snapshot)
{
    snapshot->conversation_mode = conversation_mode_from_json(task, snapshot->status);
    cJSON *messages = cJSON_GetObjectItemCaseSensitive(task, "codex_messages");
    if (cJSON_IsArray(messages)) {
        cJSON *message = NULL;
        cJSON_ArrayForEach(message, messages) {
            if (!cJSON_IsString(message) || message->valuestring == NULL || message->valuestring[0] == '\0') continue;
            if (snapshot->conversation_mode != CODEX_CONVERSATION_PROGRESS) {
                snapshot->conversation_text[0] = '\0';
                snapshot->conversation_message_count = 0;
            }
            if (snapshot->conversation_text[0] != '\0') {
                strlcat(snapshot->conversation_text, "\x1E", sizeof(snapshot->conversation_text));
            }
            char plain[1024];
            markdown_to_plain(message->valuestring, plain, sizeof(plain));
            strlcat(snapshot->conversation_text, plain, sizeof(snapshot->conversation_text));
            snapshot->conversation_message_count++;
            if (strlen(snapshot->conversation_text) >= sizeof(snapshot->conversation_text) - 1) break;
        }
    }
    if (snapshot->conversation_text[0] == '\0' &&
        snapshot->conversation_mode != CODEX_CONVERSATION_PROGRESS) {
        strlcpy(snapshot->conversation_text, snapshot->last_assistant_message,
                sizeof(snapshot->conversation_text));
        snapshot->conversation_message_count = snapshot->conversation_text[0] ? 1 : 0;
    }
}

static void copy_codex_task_detail(cJSON *task, codex_task_detail_t *detail)
{
    clear_codex_task_detail(detail);
    cJSON *status = cJSON_GetObjectItemCaseSensitive(task, "status");
    detail->status = app_state_status_from_string(cJSON_IsString(status) ? status->valuestring : "idle");
    detail->conversation_mode = conversation_mode_from_json(task, detail->status);
    copy_json_string(task, "thread_id", detail->thread_id, sizeof(detail->thread_id));
    copy_json_string(task, "title", detail->title, sizeof(detail->title));
    detail->last_user_message = copy_json_plain_external(task, "last_user_message");

    cJSON *messages = cJSON_GetObjectItemCaseSensitive(task, "codex_messages");
    if (cJSON_IsArray(messages)) {
        cJSON *message = NULL;
        cJSON_ArrayForEach(message, messages) {
            if (!cJSON_IsString(message) || message->valuestring == NULL || message->valuestring[0] == '\0') continue;
            if (detail->conversation_mode != CODEX_CONVERSATION_PROGRESS) {
                heap_caps_free(detail->conversation_text);
                detail->conversation_text = NULL;
            }
            if (!append_plain_external(&detail->conversation_text, message->valuestring, true)) break;
        }
    }
    if ((detail->conversation_text == NULL || detail->conversation_text[0] == '\0') &&
        detail->conversation_mode != CODEX_CONVERSATION_PROGRESS) {
        detail->conversation_text = copy_json_plain_external(task, "last_assistant_message");
    }
    cJSON *duration = cJSON_GetObjectItemCaseSensitive(task, "duration_seconds");
    cJSON *message_count = cJSON_GetObjectItemCaseSensitive(task, "user_message_count");
    if (!cJSON_IsNumber(message_count)) {
        message_count = cJSON_GetObjectItemCaseSensitive(task, "message_count");
    }
    cJSON *started = cJSON_GetObjectItemCaseSensitive(task, "started_at_epoch");
    cJSON *updated = cJSON_GetObjectItemCaseSensitive(task, "last_updated_epoch");
    detail->duration_seconds = cJSON_IsNumber(duration) ? (uint32_t)duration->valuedouble : 0;
    detail->message_count = cJSON_IsNumber(message_count) ? (uint32_t)message_count->valuedouble : 0;
    detail->started_at = cJSON_IsNumber(started) ? (time_t)started->valuedouble : 0;
    detail->updated_at = cJSON_IsNumber(updated) ? (time_t)updated->valuedouble : 0;
}

static uint32_t parse_custom_color(cJSON *custom, const char *name, uint32_t fallback)
{
    cJSON *accent = cJSON_GetObjectItemCaseSensitive(custom, name);
    if (!cJSON_IsString(accent) || accent->valuestring == NULL || strlen(accent->valuestring) != 7 ||
        accent->valuestring[0] != '#') {
        return fallback;
    }
    for (size_t index = 1; index < 7; index++) {
        if (!isxdigit((unsigned char)accent->valuestring[index])) return fallback;
    }
    return (uint32_t)strtoul(accent->valuestring + 1, NULL, 16);
}

static void copy_custom_config(cJSON *root, codex_snapshot_t *snapshot)
{
    cJSON *custom = cJSON_GetObjectItemCaseSensitive(root, "custom");
    snapshot->custom_accent = 0xF2C66D;
    snapshot->custom_ring_start = 0xF2C66D;
    snapshot->custom_ring_end = 0x5DA9FF;
    if (!cJSON_IsObject(custom)) return;

    snapshot->custom_enabled = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(custom, "enabled"));
    snapshot->custom_accent = parse_custom_color(custom, "accent", 0xF2C66D);
    snapshot->custom_ring_enabled = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(custom, "ring_enabled"));
    snapshot->custom_ring_start = parse_custom_color(custom, "ring_start", snapshot->custom_accent);
    snapshot->custom_ring_end = parse_custom_color(custom, "ring_end", snapshot->custom_ring_start);
    copy_json_string(custom, "title", snapshot->custom_title, sizeof(snapshot->custom_title));
    copy_json_string(custom, "value", snapshot->custom_value, sizeof(snapshot->custom_value));
    copy_json_string(custom, "body", snapshot->custom_body, sizeof(snapshot->custom_body));
    copy_json_string(custom, "footer", snapshot->custom_footer, sizeof(snapshot->custom_footer));
    cJSON *image_available = cJSON_GetObjectItemCaseSensitive(custom, "image_available");
    cJSON *image_revision = cJSON_GetObjectItemCaseSensitive(custom, "image_revision");
    cJSON *image_size = cJSON_GetObjectItemCaseSensitive(custom, "image_size");
    if (cJSON_IsTrue(image_available) && cJSON_IsNumber(image_revision) &&
        cJSON_IsNumber(image_size) && image_size->valuedouble == CUSTOM_IMAGE_BYTES) {
        snapshot->custom_image_available = true;
        snapshot->custom_image_revision = (uint32_t)image_revision->valuedouble;
        snapshot->custom_image_size = (uint32_t)image_size->valuedouble;
    }
}

static void copy_module_config(cJSON *root, codex_snapshot_t *snapshot)
{
    snapshot->codex_enabled = true;
    snapshot->bambu_enabled = true;
    snapshot->dotii_enabled = true;
    cJSON *modules = cJSON_GetObjectItemCaseSensitive(root, "modules");
    if (!cJSON_IsObject(modules)) return;

    cJSON *codex = cJSON_GetObjectItemCaseSensitive(modules, "codex");
    cJSON *bambu = cJSON_GetObjectItemCaseSensitive(modules, "bambu");
    cJSON *dotii = cJSON_GetObjectItemCaseSensitive(modules, "dotii");
    if (cJSON_IsBool(codex)) snapshot->codex_enabled = cJSON_IsTrue(codex);
    if (cJSON_IsBool(bambu)) snapshot->bambu_enabled = cJSON_IsTrue(bambu);
    if (cJSON_IsBool(dotii)) snapshot->dotii_enabled = cJSON_IsTrue(dotii);
}

static uint32_t dotii_state_token_from_string(const char *value)
{
    uint32_t hash = 2166136261U;
    if (value == NULL) return hash;
    while (*value != '\0') {
        hash ^= (uint8_t)*value++;
        hash *= 16777619U;
    }
    return hash;
}

static void copy_dotii_state(cJSON *root, codex_snapshot_t *snapshot)
{
    snapshot->dotii_expression = DOTII_EXPRESSION_IDLE_BREATH;
    snapshot->dotii_base_idle = true;
    snapshot->dotii_state_assigned = false;
    snapshot->dotii_state_duration_ms = 0;
    snapshot->dotii_state_token = dotii_state_token_from_string("idle");
    snapshot->dotii_touch_expression = DOTII_EXPRESSION_TOUCH_RESPONSE;
    snapshot->dotii_blink_expression = DOTII_EXPRESSION_BLINK;
    snapshot->dotii_long_idle_expression = DOTII_EXPRESSION_SLEEPY_YAWN;
    for (size_t index = 0; index < DOTII_EXPRESSION_COUNT; index++) {
        snapshot->dotii_expression_durations_ms[index] = 800;
    }
    snapshot->dotii_expression_durations_ms[DOTII_EXPRESSION_TOUCH_RESPONSE] = 1200;
    cJSON *dotii = cJSON_GetObjectItemCaseSensitive(root, "dotii");
    if (!cJSON_IsObject(dotii)) return;

    cJSON *expression = cJSON_GetObjectItemCaseSensitive(dotii, "expression");
    if (cJSON_IsString(expression) && expression->valuestring != NULL) {
        snapshot->dotii_expression = app_state_dotii_expression_from_string(expression->valuestring);
        snapshot->dotii_base_idle = snapshot->dotii_expression == DOTII_EXPRESSION_IDLE_BREATH;
        /* Older management centers did not publish state_assigned, so preserve
           their continuous-expression behavior unless the new field says otherwise. */
        snapshot->dotii_state_assigned = !snapshot->dotii_base_idle;
    }
    cJSON *state = cJSON_GetObjectItemCaseSensitive(dotii, "state");
    if (cJSON_IsString(state) && state->valuestring != NULL) {
        snapshot->dotii_base_idle = strcmp(state->valuestring, "idle") == 0;
        snapshot->dotii_state_token = dotii_state_token_from_string(state->valuestring);
    }
    cJSON *assigned = cJSON_GetObjectItemCaseSensitive(dotii, "state_assigned");
    if (cJSON_IsBool(assigned)) snapshot->dotii_state_assigned = cJSON_IsTrue(assigned);
    if (!snapshot->dotii_state_assigned) {
        snapshot->dotii_expression = DOTII_EXPRESSION_IDLE_BREATH;
        snapshot->dotii_base_idle = true;
    }
    cJSON *state_duration = cJSON_GetObjectItemCaseSensitive(dotii, "state_duration_ms");
    if (cJSON_IsNumber(state_duration)) {
        const int duration = state_duration->valueint;
        if (duration == 0 || duration == 1000 || duration == 3000 || duration == 5000 ||
            duration == 30000) {
            snapshot->dotii_state_duration_ms = (uint32_t)duration;
        }
    }
    cJSON *state_token = cJSON_GetObjectItemCaseSensitive(dotii, "state_token");
    if (cJSON_IsNumber(state_token) && state_token->valuedouble >= 0 &&
        state_token->valuedouble <= UINT32_MAX) {
        snapshot->dotii_state_token = (uint32_t)state_token->valuedouble;
    }
}

static void copy_display_config(cJSON *root, codex_snapshot_t *snapshot)
{
    snapshot->codex_ui_dual_limit = false;
    snapshot->docked_rotation_tenths = 840;
    snapshot->screen_off_timeout_seconds = 60;
    snapshot->sleep_timeout_seconds = 300;
    snapshot->charging_screen_off_timeout_seconds = 60;
    snapshot->charging_sleep_timeout_seconds = 300;
    snapshot->screen_off_page = DISPLAY_SCREEN_OFF_PAGE_NONE;
    cJSON *display = cJSON_GetObjectItemCaseSensitive(root, "display");
    if (!cJSON_IsObject(display)) return;

    cJSON *revision = cJSON_GetObjectItemCaseSensitive(display, "revision");
    cJSON *codex_ui = cJSON_GetObjectItemCaseSensitive(display, "codex_ui");
    cJSON *angle = cJSON_GetObjectItemCaseSensitive(display, "docked_rotation_tenths");
    cJSON *screen_off_timeout = cJSON_GetObjectItemCaseSensitive(display, "screen_off_timeout_seconds");
    cJSON *sleep_timeout = cJSON_GetObjectItemCaseSensitive(display, "sleep_timeout_seconds");
    cJSON *charging_screen_off_timeout = cJSON_GetObjectItemCaseSensitive(display, "charging_screen_off_timeout_seconds");
    cJSON *charging_sleep_timeout = cJSON_GetObjectItemCaseSensitive(display, "charging_sleep_timeout_seconds");
    cJSON *screen_off_page = cJSON_GetObjectItemCaseSensitive(display, "screen_off_page");
    if (cJSON_IsNumber(revision) && revision->valuedouble >= 0) {
        snapshot->display_revision = (uint32_t)revision->valuedouble;
    }
    if (cJSON_IsString(codex_ui) && codex_ui->valuestring != NULL) {
        snapshot->codex_ui_dual_limit = strcmp(codex_ui->valuestring, "dual_limit") == 0;
    }
    if (cJSON_IsNumber(angle) && angle->valueint >= 800 && angle->valueint <= 1000) {
        snapshot->docked_rotation_tenths = (int16_t)angle->valueint;
    }
    if (cJSON_IsNumber(screen_off_timeout) && screen_off_timeout->valuedouble >= 0 &&
        screen_off_timeout->valuedouble <= 86400) {
        snapshot->screen_off_timeout_seconds = (uint32_t)screen_off_timeout->valuedouble;
    }
    if (cJSON_IsNumber(sleep_timeout) && sleep_timeout->valuedouble >= 0 &&
        sleep_timeout->valuedouble <= 86400) {
        snapshot->sleep_timeout_seconds = (uint32_t)sleep_timeout->valuedouble;
    }
    snapshot->charging_screen_off_timeout_seconds = snapshot->screen_off_timeout_seconds;
    snapshot->charging_sleep_timeout_seconds = snapshot->sleep_timeout_seconds;
    if (cJSON_IsNumber(charging_screen_off_timeout) && charging_screen_off_timeout->valuedouble >= 0 &&
        charging_screen_off_timeout->valuedouble <= 86400) {
        snapshot->charging_screen_off_timeout_seconds = (uint32_t)charging_screen_off_timeout->valuedouble;
    }
    if (cJSON_IsNumber(charging_sleep_timeout) && charging_sleep_timeout->valuedouble >= 0 &&
        charging_sleep_timeout->valuedouble <= 86400) {
        snapshot->charging_sleep_timeout_seconds = (uint32_t)charging_sleep_timeout->valuedouble;
    }
    if (cJSON_IsString(screen_off_page) && screen_off_page->valuestring != NULL) {
        if (strcmp(screen_off_page->valuestring, "custom") == 0) {
            snapshot->screen_off_page = DISPLAY_SCREEN_OFF_PAGE_CUSTOM;
        } else if (strcmp(screen_off_page->valuestring, "dotii") == 0) {
            snapshot->screen_off_page = DISPLAY_SCREEN_OFF_PAGE_DOTII;
        }
    }
    if ((snapshot->screen_off_timeout_seconds == 0 && snapshot->sleep_timeout_seconds != 0) ||
        (snapshot->screen_off_timeout_seconds != 0 && snapshot->sleep_timeout_seconds != 0 &&
         snapshot->sleep_timeout_seconds < snapshot->screen_off_timeout_seconds)) {
        ESP_LOGW(TAG, "Ignoring invalid display timeout order: off=%lu sleep=%lu",
                 (unsigned long)snapshot->screen_off_timeout_seconds,
                 (unsigned long)snapshot->sleep_timeout_seconds);
        snapshot->screen_off_timeout_seconds = 60;
        snapshot->sleep_timeout_seconds = 300;
    }
    if ((snapshot->charging_screen_off_timeout_seconds == 0 && snapshot->charging_sleep_timeout_seconds != 0) ||
        (snapshot->charging_screen_off_timeout_seconds != 0 && snapshot->charging_sleep_timeout_seconds != 0 &&
         snapshot->charging_sleep_timeout_seconds < snapshot->charging_screen_off_timeout_seconds)) {
        ESP_LOGW(TAG, "Ignoring invalid charging timeout order: off=%lu sleep=%lu",
                 (unsigned long)snapshot->charging_screen_off_timeout_seconds,
                 (unsigned long)snapshot->charging_sleep_timeout_seconds);
        snapshot->charging_screen_off_timeout_seconds = snapshot->screen_off_timeout_seconds;
        snapshot->charging_sleep_timeout_seconds = snapshot->sleep_timeout_seconds;
    }
}

static void copy_bambu_status(cJSON *root, codex_snapshot_t *snapshot)
{
    cJSON *bambu = cJSON_GetObjectItemCaseSensitive(root, "bambu");
    snapshot->bambu_status = BAMBU_STATUS_OFFLINE;
    strlcpy(snapshot->bambu_status_text, "离线", sizeof(snapshot->bambu_status_text));
    if (!cJSON_IsObject(bambu)) return;

    snapshot->bambu_configured = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(bambu, "configured"));
    snapshot->bambu_connected = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(bambu, "connected"));
    snapshot->bambu_commandable = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(bambu, "commandable"));
    copy_json_string(bambu, "name", snapshot->bambu_name, sizeof(snapshot->bambu_name));
    copy_json_string(bambu, "status_text", snapshot->bambu_status_text, sizeof(snapshot->bambu_status_text));
    copy_json_string(bambu, "filename", snapshot->bambu_filename, sizeof(snapshot->bambu_filename));
    copy_json_string(bambu, "filament", snapshot->bambu_filament, sizeof(snapshot->bambu_filament));
    cJSON *status = cJSON_GetObjectItemCaseSensitive(bambu, "status");
    snapshot->bambu_status = app_state_bambu_status_from_string(cJSON_IsString(status) ? status->valuestring : "offline");
    cJSON *progress = cJSON_GetObjectItemCaseSensitive(bambu, "progress");
    cJSON *remaining = cJSON_GetObjectItemCaseSensitive(bambu, "remaining_minutes");
    cJSON *finish = cJSON_GetObjectItemCaseSensitive(bambu, "finish_epoch");
    cJSON *updated = cJSON_GetObjectItemCaseSensitive(bambu, "updated_at_epoch");
    cJSON *layer_current = cJSON_GetObjectItemCaseSensitive(bambu, "layer_current");
    cJSON *layer_total = cJSON_GetObjectItemCaseSensitive(bambu, "layer_total");
    cJSON *nozzle = cJSON_GetObjectItemCaseSensitive(bambu, "nozzle_temperature");
    cJSON *bed = cJSON_GetObjectItemCaseSensitive(bambu, "bed_temperature");
    snapshot->bambu_progress = cJSON_IsNumber(progress) ? (uint8_t)progress->valueint : 0;
    snapshot->bambu_remaining_minutes = cJSON_IsNumber(remaining) ? (uint16_t)remaining->valueint : 0;
    snapshot->bambu_finish_at = cJSON_IsNumber(finish) ? (time_t)finish->valuedouble : 0;
    snapshot->bambu_updated_at = cJSON_IsNumber(updated) ? (time_t)updated->valuedouble : 0;
    snapshot->bambu_layer_current = cJSON_IsNumber(layer_current) ? (uint16_t)layer_current->valueint : 0;
    snapshot->bambu_layer_total = cJSON_IsNumber(layer_total) ? (uint16_t)layer_total->valueint : 0;
    snapshot->bambu_nozzle_temperature = cJSON_IsNumber(nozzle) ? (int16_t)nozzle->valuedouble : 0;
    snapshot->bambu_bed_temperature = cJSON_IsNumber(bed) ? (int16_t)bed->valuedouble : 0;
    cJSON *camera_available = cJSON_GetObjectItemCaseSensitive(bambu, "camera_available");
    cJSON *camera_revision = cJSON_GetObjectItemCaseSensitive(bambu, "camera_revision");
    cJSON *camera_size = cJSON_GetObjectItemCaseSensitive(bambu, "camera_size");
    if (cJSON_IsTrue(camera_available) && cJSON_IsNumber(camera_revision) &&
        cJSON_IsNumber(camera_size) && camera_size->valuedouble == BAMBU_CAMERA_BYTES) {
        snapshot->bambu_camera_available = true;
        snapshot->bambu_camera_revision = (uint32_t)camera_revision->valuedouble;
        snapshot->bambu_camera_size = (uint32_t)camera_size->valuedouble;
    }
}

static bool parse_snapshot_object(cJSON *root, codex_snapshot_t *snapshot)
{
    bool ok = false;
    if (root == NULL) return false;

    cJSON *version = cJSON_GetObjectItemCaseSensitive(root, "schema_version");
    cJSON *codex = cJSON_GetObjectItemCaseSensitive(root, "codex");
    cJSON *task = cJSON_IsObject(codex) ? cJSON_GetObjectItemCaseSensitive(codex, "task") : NULL;
    if (!cJSON_IsNumber(version) || version->valueint != 1 || !cJSON_IsObject(codex) || !cJSON_IsObject(task)) {
        goto done;
    }

    memset(snapshot, 0, sizeof(*snapshot));
    snapshot->valid = true;
    snapshot->source_online = true;
    copy_module_config(root, snapshot);
    copy_display_config(root, snapshot);

    cJSON *preview = cJSON_GetObjectItemCaseSensitive(root, "preview_data");
    snapshot->preview_data = cJSON_IsTrue(preview);

    cJSON *generated = cJSON_GetObjectItemCaseSensitive(root, "generated_at_epoch");
    snapshot->generated_at = cJSON_IsNumber(generated) ? (time_t)generated->valuedouble : time(NULL);

    cJSON *weekly = cJSON_GetObjectItemCaseSensitive(codex, "weekly_remaining_percent");
    cJSON *weekly_tokens = cJSON_GetObjectItemCaseSensitive(codex, "weekly_tokens");
    if (!cJSON_IsNumber(weekly_tokens)) {
        weekly_tokens = cJSON_GetObjectItemCaseSensitive(codex, "today_tokens");
    }
    if (!cJSON_IsNumber(weekly) || weekly->valueint < 0 || weekly->valueint > 100 ||
        !cJSON_IsNumber(weekly_tokens)) {
        goto done;
    }
    snapshot->weekly_remaining_percent = weekly->valueint;
    snapshot->weekly_tokens = (uint32_t)weekly_tokens->valuedouble;
    cJSON *five_hour = cJSON_GetObjectItemCaseSensitive(codex, "five_hour_remaining_percent");
    cJSON *five_hour_available = cJSON_GetObjectItemCaseSensitive(codex, "five_hour_available");
    if (cJSON_IsNumber(five_hour) && five_hour->valueint >= 0 && five_hour->valueint <= 100) {
        snapshot->five_hour_remaining_percent = five_hour->valueint;
        snapshot->five_hour_available = five_hour_available == NULL ? true : cJSON_IsTrue(five_hour_available);
    }
    cJSON *weekly_available = cJSON_GetObjectItemCaseSensitive(codex, "weekly_available");
    cJSON *weekly_tokens_available = cJSON_GetObjectItemCaseSensitive(codex, "weekly_tokens_available");
    if (weekly_tokens_available == NULL) {
        weekly_tokens_available = cJSON_GetObjectItemCaseSensitive(codex, "today_tokens_available");
    }
    snapshot->weekly_available = weekly_available == NULL ? true : cJSON_IsTrue(weekly_available);
    snapshot->weekly_tokens_available = weekly_tokens_available == NULL ? false : cJSON_IsTrue(weekly_tokens_available);
    copy_json_string(codex, "five_hour_reset_date", snapshot->five_hour_reset_date,
                     sizeof(snapshot->five_hour_reset_date));
    copy_json_string(codex, "reset_date", snapshot->reset_date, sizeof(snapshot->reset_date));
    copy_json_string(codex, "plan_type", snapshot->plan_type, sizeof(snapshot->plan_type));

    cJSON *status = cJSON_GetObjectItemCaseSensitive(task, "status");
    snapshot->status = app_state_status_from_string(cJSON_IsString(status) ? status->valuestring : "idle");
    copy_json_string(task, "title", snapshot->title, sizeof(snapshot->title));
    copy_json_plain(task, "last_user_message", snapshot->last_user_message, sizeof(snapshot->last_user_message));
    copy_json_plain(task, "last_assistant_message", snapshot->last_assistant_message, sizeof(snapshot->last_assistant_message));
    copy_json_string(task, "current_action", snapshot->current_action, sizeof(snapshot->current_action));
    copy_codex_messages(task, snapshot);

    cJSON *duration = cJSON_GetObjectItemCaseSensitive(task, "duration_seconds");
    cJSON *messages = cJSON_GetObjectItemCaseSensitive(task, "user_message_count");
    if (!cJSON_IsNumber(messages)) {
        messages = cJSON_GetObjectItemCaseSensitive(task, "message_count");
    }
    cJSON *tokens = cJSON_GetObjectItemCaseSensitive(task, "token_count");
    cJSON *tokens_available = cJSON_GetObjectItemCaseSensitive(task, "token_count_available");
    cJSON *plan_completed = cJSON_GetObjectItemCaseSensitive(task, "plan_completed");
    cJSON *plan_total = cJSON_GetObjectItemCaseSensitive(task, "plan_total");
    cJSON *task_updated = cJSON_GetObjectItemCaseSensitive(task, "last_updated_epoch");
    snapshot->duration_seconds = cJSON_IsNumber(duration) ? (uint32_t)duration->valuedouble : 0;
    snapshot->message_count = cJSON_IsNumber(messages) ? (uint32_t)messages->valuedouble : 0;
    snapshot->task_tokens = cJSON_IsNumber(tokens) ? (uint32_t)tokens->valuedouble : 0;
    snapshot->task_tokens_available = tokens_available == NULL ? true : cJSON_IsTrue(tokens_available);
    snapshot->plan_completed = cJSON_IsNumber(plan_completed) ? (uint16_t)plan_completed->valueint : 0;
    snapshot->plan_total = cJSON_IsNumber(plan_total) ? (uint16_t)plan_total->valueint : 0;
    snapshot->task_updated_at = cJSON_IsNumber(task_updated) ? (time_t)task_updated->valuedouble : 0;

    s_work_task_count = 0;
    cJSON *tasks = cJSON_GetObjectItemCaseSensitive(codex, "tasks");
    if (cJSON_IsArray(tasks) && s_work_tasks != NULL) {
        cJSON *task_item = NULL;
        cJSON_ArrayForEach(task_item, tasks) {
            if (!cJSON_IsObject(task_item) || s_work_task_count >= CODEX_TASK_DETAIL_MAX) break;
            copy_codex_task_detail(task_item, &s_work_tasks[s_work_task_count++]);
        }
    }
    if (s_work_task_count == 0 && s_work_tasks != NULL) {
        copy_codex_task_detail(task, &s_work_tasks[0]);
        s_work_task_count = 1;
    }
    if (s_work_tasks != NULL) {
        for (size_t index = s_work_task_count; index < CODEX_TASK_DETAIL_MAX; index++) {
            clear_codex_task_detail(&s_work_tasks[index]);
        }
    }
    copy_custom_config(root, snapshot);
    copy_bambu_status(root, snapshot);
    copy_dotii_state(root, snapshot);
    ok = true;

done:
    return ok;
}

static bool parse_snapshot(const char *json, codex_snapshot_t *snapshot)
{
    cJSON *root = cJSON_Parse(json);
    bool parsed = parse_snapshot_object(root, snapshot);
    cJSON_Delete(root);
    return parsed;
}

static esp_err_t http_event_handler(esp_http_client_event_t *event)
{
    http_body_t *response = (http_body_t *)event->user_data;
    if (event->event_id == HTTP_EVENT_ON_DATA && response != NULL &&
        response->body != NULL && event->data_len > 0) {
        size_t required = response->length + (size_t)event->data_len + 1;
        if (required > response->capacity) {
            size_t capacity = response->capacity;
            while (capacity < required && capacity <= SIZE_MAX / 2) capacity *= 2;
            if (capacity < required) capacity = required;
            char *expanded = heap_caps_realloc(response->body, capacity,
                                               MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
            if (expanded == NULL) {
                response->overflow = true;
                return ESP_OK;
            }
            response->body = expanded;
            response->capacity = capacity;
        }
        memcpy(response->body + response->length, event->data, (size_t)event->data_len);
        response->length += (size_t)event->data_len;
        response->body[response->length] = '\0';
    }
    return ESP_OK;
}

static esp_err_t image_http_event_handler(esp_http_client_event_t *event)
{
    image_body_t *response = (image_body_t *)event->user_data;
    if (event->event_id == HTTP_EVENT_ON_DATA && response != NULL && event->data_len > 0) {
        size_t available = response->capacity - response->length;
        size_t count = (size_t)event->data_len;
        if (count > available) {
            count = available;
            response->overflow = true;
        }
        if (count > 0) {
            memcpy(response->data + response->length, event->data, count);
            response->length += count;
        }
    }
    return ESP_OK;
}

static bool fetch_custom_image(uint32_t revision)
{
    const device_config_values_t *device_config = device_config_get();
    for (size_t index = 0; index < 2; index++) {
        if (s_custom_image_buffers[index] != NULL && s_custom_image_revisions[index] == revision) {
            s_custom_image_active = (int)index;
            return true;
        }
    }
    char url[256];
    strlcpy(url, device_config->bridge_url, sizeof(url));
    char *last_slash = strrchr(url, '/');
    if (last_slash == NULL) return false;
    strlcpy(last_slash + 1, "custom-screen.rgb565", sizeof(url) - (size_t)(last_slash + 1 - url));

    int slot = s_custom_image_active == 0 ? 1 : 0;
    if (s_custom_image_buffers[slot] == NULL) {
        s_custom_image_buffers[slot] = heap_caps_malloc(CUSTOM_IMAGE_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (s_custom_image_buffers[slot] == NULL) {
            ESP_LOGE(TAG, "Unable to allocate custom image buffer");
            return false;
        }
    }
    image_body_t response = {
        .data = s_custom_image_buffers[slot],
        .capacity = CUSTOM_IMAGE_BYTES,
    };
    esp_http_client_config_t config = {
        .url = url,
        .event_handler = image_http_event_handler,
        .user_data = &response,
        .timeout_ms = 15000,
        .buffer_size = 4096,
        .crt_bundle_attach = esp_crt_bundle_attach,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == NULL) return false;
    if (strlen(device_config->bridge_token) > 0) {
        esp_http_client_set_header(client, "X-Bridge-Token", device_config->bridge_token);
    }
    esp_err_t error = esp_http_client_perform(client);
    int status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    if (error != ESP_OK || status != 200 || response.overflow || response.length != CUSTOM_IMAGE_BYTES) {
        ESP_LOGW(TAG, "Custom image request failed: %s, HTTP %d, bytes %u",
                 esp_err_to_name(error), status, (unsigned)response.length);
        return false;
    }
    blacken_custom_frame_near_black_edge(s_custom_image_buffers[slot]);
    s_custom_image_revisions[slot] = revision;
    s_custom_image_active = slot;
    ESP_LOGI(TAG, "Custom image received: revision %lu", (unsigned long)revision);
    return true;
}

static bool fetch_bambu_camera(uint32_t revision)
{
    const device_config_values_t *device_config = device_config_get();
    for (size_t index = 0; index < 2; index++) {
        if (s_bambu_camera_buffers[index] != NULL && s_bambu_camera_revisions[index] == revision) {
            s_bambu_camera_active = (int)index;
            return true;
        }
    }
    char url[256];
    strlcpy(url, device_config->bridge_url, sizeof(url));
    char *last_slash = strrchr(url, '/');
    if (last_slash == NULL) return false;
    strlcpy(last_slash + 1, "bambu-camera.rgb565", sizeof(url) - (size_t)(last_slash + 1 - url));
    int slot = s_bambu_camera_active == 0 ? 1 : 0;
    if (s_bambu_camera_buffers[slot] == NULL) {
        s_bambu_camera_buffers[slot] = heap_caps_malloc(BAMBU_CAMERA_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (s_bambu_camera_buffers[slot] == NULL) return false;
    }
    image_body_t response = {.data = s_bambu_camera_buffers[slot], .capacity = BAMBU_CAMERA_BYTES};
    esp_http_client_config_t config = {
        .url = url, .event_handler = image_http_event_handler, .user_data = &response,
        .timeout_ms = 12000, .buffer_size = 4096, .crt_bundle_attach = esp_crt_bundle_attach,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == NULL) return false;
    if (strlen(device_config->bridge_token) > 0) {
        esp_http_client_set_header(client, "X-Bridge-Token", device_config->bridge_token);
    }
    esp_err_t error = esp_http_client_perform(client);
    int status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    if (error != ESP_OK || status != 200 || response.overflow || response.length != BAMBU_CAMERA_BYTES) {
        ESP_LOGW(TAG, "Bambu camera request failed: %s, HTTP %d, bytes %u",
                 esp_err_to_name(error), status, (unsigned)response.length);
        return false;
    }
    s_bambu_camera_revisions[slot] = revision;
    s_bambu_camera_active = slot;
    return true;
}

static void apply_connection_options(cJSON *root);

static bool fetch_snapshot(codex_snapshot_t *snapshot)
{
    const device_config_values_t *device_config = device_config_get();
    if (strlen(device_config->bridge_url) == 0) return false;

    char *body = heap_caps_calloc(1, HTTP_BODY_INITIAL_CAPACITY,
                                  MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (body == NULL) return false;
    http_body_t response = {.body = body, .capacity = HTTP_BODY_INITIAL_CAPACITY};
    esp_http_client_config_t config = {
        .url = device_config->bridge_url,
        .event_handler = http_event_handler,
        .user_data = &response,
        .timeout_ms = 10000,
        .buffer_size = 1024,
        .crt_bundle_attach = esp_crt_bundle_attach,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == NULL) {
        heap_caps_free(response.body);
        return false;
    }

    esp_http_client_set_header(client, "Accept", "application/json");
    esp_http_client_set_header(client, "X-Dotii-Device", s_device_id);
    if (strlen(device_config->bridge_token) > 0) {
        esp_http_client_set_header(client, "X-Bridge-Token", device_config->bridge_token);
    }
    esp_err_t error = esp_http_client_perform(client);
    int status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    if (error != ESP_OK || status != 200) {
        ESP_LOGW(TAG, "Bridge request failed: %s, HTTP %d", esp_err_to_name(error), status);
        heap_caps_free(response.body);
        return false;
    }
    if (response.overflow) {
        ESP_LOGW(TAG, "Bridge snapshot could not fit in available PSRAM");
        heap_caps_free(response.body);
        return false;
    }
    bool parsed = parse_snapshot(response.body, snapshot);
    if (parsed) {
        cJSON *root = cJSON_Parse(response.body);
        if (root) { apply_connection_options(cJSON_GetObjectItemCaseSensitive(root, "display")); cJSON_Delete(root); }
    }
    heap_caps_free(response.body);
    if (parsed) app_state_tasks_publish(s_work_tasks, s_work_task_count);
    if (parsed && snapshot->custom_image_available &&
        !fetch_custom_image(snapshot->custom_image_revision)) {
        snapshot->custom_image_available = false;
    }
    if (parsed && snapshot->bambu_camera_available &&
        !fetch_bambu_camera(snapshot->bambu_camera_revision)) {
        snapshot->bambu_camera_available = false;
    }
    return parsed;
}

const uint8_t *connectivity_custom_image_data(uint32_t revision)
{
    for (size_t index = 0; index < 2; index++) {
        if (s_custom_image_buffers[index] != NULL && s_custom_image_revisions[index] == revision) {
            return s_custom_image_buffers[index];
        }
    }
    return NULL;
}

const uint8_t *connectivity_bambu_camera_data(uint32_t revision)
{
    for (size_t index = 0; index < 2; index++) {
        if (s_bambu_camera_buffers[index] != NULL && s_bambu_camera_revisions[index] == revision) {
            return s_bambu_camera_buffers[index];
        }
    }
    return NULL;
}

static bool send_bambu_command(bambu_command_t command)
{
    const device_config_values_t *device_config = device_config_get();
    const char *action = command == BAMBU_COMMAND_PAUSE ? "pause" :
                         command == BAMBU_COMMAND_RESUME ? "resume" : "stop";
    if (connectivity_usb_active()) {
        cJSON *message = cJSON_CreateObject();
        cJSON_AddStringToObject(message, "event", "bambu_command");
        cJSON_AddStringToObject(message, "action", action);
        usb_transport_send(message);
        cJSON_Delete(message);
        return true;
    }
    char url[256];
    strlcpy(url, device_config->bridge_url, sizeof(url));
    char *last_slash = strrchr(url, '/');
    if (last_slash == NULL) return false;
    strlcpy(last_slash + 1, "bambu/command", sizeof(url) - (size_t)(last_slash + 1 - url));
    char payload[40];
    snprintf(payload, sizeof(payload), "{\"action\":\"%s\"}", action);
    char response_body[256] = {0};
    http_body_t response = {.body = response_body, .capacity = sizeof(response_body)};
    esp_http_client_config_t config = {
        .url = url, .event_handler = http_event_handler, .user_data = &response,
        .timeout_ms = 8000, .buffer_size = 512, .crt_bundle_attach = esp_crt_bundle_attach,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == NULL) return false;
    esp_http_client_set_method(client, HTTP_METHOD_POST);
    esp_http_client_set_header(client, "Content-Type", "application/json");
    if (strlen(device_config->bridge_token) > 0) {
        esp_http_client_set_header(client, "X-Bridge-Token", device_config->bridge_token);
    }
    esp_http_client_set_post_field(client, payload, strlen(payload));
    esp_err_t error = esp_http_client_perform(client);
    int status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    ESP_LOGI(TAG, "Bambu %s command: %s, HTTP %d", action, esp_err_to_name(error), status);
    return error == ESP_OK && status == 202;
}

static void check_wifi_link(void)
{
    const device_config_values_t *device_config = device_config_get();
    if (!s_wifi_connected || !device_config->bridge_url[0]) { s_wifi_bridge_online = false; return; }
    char url[256];
    strlcpy(url, device_config->bridge_url, sizeof(url));
    char *last = strrchr(url, '/');
    if (!last) return;
    strlcpy(last + 1, "device/ping", sizeof(url) - (size_t)(last + 1 - url));
    esp_http_client_config_t config = {.url = url, .timeout_ms = 1500, .crt_bundle_attach = esp_crt_bundle_attach};
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) return;
    esp_http_client_set_header(client, "X-Bridge-Token", device_config->bridge_token);
    esp_http_client_set_header(client, "X-Dotii-Device", s_device_id);
    esp_err_t error = esp_http_client_perform(client);
    s_wifi_bridge_online = error == ESP_OK && esp_http_client_get_status_code(client) == 200;
    esp_http_client_cleanup(client);
}

static void publish_offline_state(void)
{
    if (s_have_snapshot) {
        s_work_snapshot = s_last_snapshot;
        s_work_snapshot.source_online = false;
        time_t now = time(NULL);
        s_work_snapshot.stale = s_work_snapshot.generated_at > 0 && now > s_work_snapshot.generated_at &&
                                (now - s_work_snapshot.generated_at) > CONFIG_STATE_DISPLAY_STALE_SECONDS;
        if (s_work_snapshot.stale) s_work_snapshot.status = CODEX_STATUS_OFFLINE;
    } else if (CONFIG_STATE_DISPLAY_DEMO_MODE) {
        app_state_make_preview(&s_work_snapshot);
    } else {
        memset(&s_work_snapshot, 0, sizeof(s_work_snapshot));
        s_work_snapshot.status = CODEX_STATUS_OFFLINE;
    }
    app_state_publish(&s_work_snapshot);
}


static const char *usb_string(cJSON *root, const char *key)
{
    cJSON *value = cJSON_GetObjectItemCaseSensitive(root, key);
    return cJSON_IsString(value) ? value->valuestring : "";
}

static bool token_equal(const char *a, const char *b)
{
    size_t la = strlen(a), lb = strlen(b);
    unsigned difference = (unsigned)(la ^ lb);
    for (size_t i = 0; i < la || i < lb; i++) difference |= (i < la ? a[i] : 0) ^ (i < lb ? b[i] : 0);
    return difference == 0;
}

static void apply_connection_options(cJSON *root)
{
    const char *language = usb_string(root, "language");
    ui_language_set(language);
    const char *mode = usb_string(root, "connection_mode");
    if ((strcmp(mode, "auto") == 0 || strcmp(mode, "usb") == 0 || strcmp(mode, "wifi") == 0) &&
        strcmp(mode, s_connection_mode) != 0) {
        strlcpy(s_connection_mode, mode, sizeof(s_connection_mode));
        nvs_handle_t handle;
        if (nvs_open("display_ui", NVS_READWRITE, &handle) == ESP_OK) {
            nvs_set_str(handle, "connection", mode);
            nvs_commit(handle);
            nvs_close(handle);
        }
    }
}

static bool receive_usb_image(cJSON *root)
{
    const bool custom = strcmp(usb_string(root, "kind"), "custom") == 0;
    if (!custom && strcmp(usb_string(root, "kind"), "camera") != 0) return false;
    int kind = custom ? 0 : 1;
    const uint32_t total = custom ? CUSTOM_IMAGE_BYTES : BAMBU_CAMERA_BYTES;
    cJSON *offset_value = cJSON_GetObjectItemCaseSensitive(root, "offset");
    cJSON *revision_value = cJSON_GetObjectItemCaseSensitive(root, "revision");
    cJSON *total_value = cJSON_GetObjectItemCaseSensitive(root, "total");
    cJSON *crc_value = cJSON_GetObjectItemCaseSensitive(root, "crc");
    if (!cJSON_IsNumber(offset_value) || !cJSON_IsNumber(revision_value) || !cJSON_IsNumber(crc_value) ||
        !cJSON_IsNumber(total_value) || total_value->valuedouble != total ||
        offset_value->valuedouble < 0 || offset_value->valuedouble >= total) return false;
    uint32_t offset = (uint32_t)offset_value->valuedouble;
    uint32_t revision = (uint32_t)revision_value->valuedouble;
    uint8_t **buffers = custom ? s_custom_image_buffers : s_bambu_camera_buffers;
    uint32_t *revisions = custom ? s_custom_image_revisions : s_bambu_camera_revisions;
    if (offset == 0) {
        s_image_slot[kind] = (custom ? s_custom_image_active : s_bambu_camera_active) == 0 ? 1 : 0;
        int slot = s_image_slot[kind];
        if (!buffers[slot]) buffers[slot] = heap_caps_malloc(total, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!buffers[slot]) return false;
        revisions[slot] = 0;
        s_image_offset[kind] = 0;
        s_image_revision[kind] = revision;
        s_image_crc[kind] = (uint32_t)crc_value->valuedouble;
    }
    if (s_image_offset[kind] != offset || s_image_revision[kind] != revision ||
        s_image_crc[kind] != (uint32_t)crc_value->valuedouble) return false;
    const char *encoded = usb_string(root, "data");
    if (strlen(encoded) > 5500) return false;
    size_t length = 0;
    if (mbedtls_base64_decode(s_usb_decoded, sizeof(s_usb_decoded), &length, (const unsigned char *)encoded, strlen(encoded)) != 0 ||
        !length || offset + length > total) return false;
    int slot = s_image_slot[kind];
    memcpy(buffers[slot] + offset, s_usb_decoded, length);
    s_image_offset[kind] += length;
    if (s_image_offset[kind] == total) {
        if (esp_rom_crc32_le(0, buffers[slot], total) != s_image_crc[kind]) return false;
        if (custom) blacken_custom_frame_near_black_edge(buffers[slot]);
        revisions[slot] = revision;
        if (custom) s_custom_image_active = slot;
        else s_bambu_camera_active = slot;
    }
    return true;
}

static void process_usb(cJSON *request)
{
    cJSON *response = cJSON_CreateObject();
    cJSON *id = cJSON_GetObjectItemCaseSensitive(request, "id");
    cJSON_AddNumberToObject(response, "id", cJSON_IsNumber(id) ? id->valuedouble : 0);
    const char *op = usb_string(request, "op");
    const char *token = usb_string(request, "token");
    const device_config_values_t *config = device_config_get();
    const char *bound = config->provisioned ? config->bridge_token : s_usb_token;
    const char *error = NULL;
    bool restart = false;
    if (strlen(token) < 16 || strlen(token) > 64 || (*bound && !token_equal(token, bound))) {
        error = "binding_mismatch";
    } else if (strcmp(op, "hello") == 0) {
        cJSON_AddStringToObject(response, "product", "Dotii");
        cJSON_AddNumberToObject(response, "protocol", 1);
        cJSON_AddStringToObject(response, "device_id", s_device_id);
        cJSON_AddStringToObject(response, "firmware", esp_app_get_description()->version);
    } else if (strcmp(op, "snapshot") == 0) {
        cJSON *payload = cJSON_GetObjectItemCaseSensitive(request, "snapshot");
        bool parsed = cJSON_IsObject(payload) && parse_snapshot_object(payload, &s_work_snapshot);
        if (!parsed) error = "invalid_snapshot";
        else {
            apply_connection_options(request);
            cJSON *epoch = cJSON_GetObjectItemCaseSensitive(request, "epoch");
            if (cJSON_IsNumber(epoch) && epoch->valuedouble > 1700000000) {
                struct timeval now = {.tv_sec = (time_t)epoch->valuedouble};
                settimeofday(&now, NULL);
            }
            if (!*bound) {
                nvs_handle_t handle;
                if (nvs_open("dotii_cfg", NVS_READWRITE, &handle) == ESP_OK) {
                    if (nvs_set_str(handle, "usb_token", token) == ESP_OK && nvs_commit(handle) == ESP_OK)
                        strlcpy(s_usb_token, token, sizeof(s_usb_token));
                    nvs_close(handle);
                }
            }
            if (strcmp(s_connection_mode, "wifi") != 0) {
                s_usb_last_sync = esp_timer_get_time();
                s_bridge_online = true;
                s_bridge_failures = 0;
                if (!connectivity_custom_image_data(s_work_snapshot.custom_image_revision)) s_work_snapshot.custom_image_available = false;
                if (!connectivity_bambu_camera_data(s_work_snapshot.bambu_camera_revision)) s_work_snapshot.bambu_camera_available = false;
                app_state_tasks_publish(s_work_tasks, s_work_task_count);
                s_last_snapshot = s_work_snapshot;
                s_have_snapshot = true;
                app_state_publish(&s_work_snapshot);
            }
        }
    } else if (strcmp(op, "image") == 0) {
        if (!receive_usb_image(request)) error = "invalid_image_chunk";
    } else if (strcmp(op, "provision") == 0) {
        device_config_values_t next = *config;
        const char *ssid = usb_string(request, "ssid"), *password = usb_string(request, "password");
        const char *url = usb_string(request, "bridge_url");
        if (!*ssid || strlen(ssid) > 32 || strlen(password) > 64 || strlen(url) >= sizeof(next.bridge_url) || strncmp(url, "http://", 7)) {
            error = "invalid_config";
        } else {
            strlcpy(next.wifi_ssid, ssid, sizeof(next.wifi_ssid));
            const bool preserve = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(request, "preserve_password"));
            if (preserve && strcmp(ssid, config->wifi_ssid) != 0) error = "saved_network_mismatch";
            if (!preserve) strlcpy(next.wifi_password, password, sizeof(next.wifi_password));
            strlcpy(next.bridge_url, url, sizeof(next.bridge_url));
            strlcpy(next.bridge_token, token, sizeof(next.bridge_token));
            if (!error) {
                if (device_config_save(&next) != ESP_OK) error = "storage_failed";
                else restart = true;
            }
        }
    } else error = "unknown_operation";
    cJSON_AddBoolToObject(response, "ok", error == NULL);
    if (error) cJSON_AddStringToObject(response, "error", error);
    cJSON_AddBoolToObject(response, "wifi", s_wifi_connected);
    cJSON_AddBoolToObject(response, "wifi_bridge", s_wifi_bridge_online);
    cJSON_AddStringToObject(response, "ssid", config->wifi_ssid);
    cJSON_AddStringToObject(response, "language", ui_language_english() ? "en" : "zh-CN");
    cJSON_AddStringToObject(response, "mode", s_connection_mode);
    cJSON_AddNumberToObject(response, "uptime", esp_timer_get_time() / 1000000);
    cJSON_AddNumberToObject(response, "reset_reason", esp_reset_reason());
    cJSON_AddNumberToObject(response, "free_internal", heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    cJSON_AddNumberToObject(response, "custom_revision", s_custom_image_active >= 0 ? s_custom_image_revisions[s_custom_image_active] : 0);
    cJSON_AddNumberToObject(response, "camera_revision", s_bambu_camera_active >= 0 ? s_bambu_camera_revisions[s_bambu_camera_active] : 0);
    usb_transport_send(response);
    cJSON_Delete(response);
    if (restart) { vTaskDelay(pdMS_TO_TICKS(300)); esp_restart(); }
}

static void bridge_task(void *argument)
{
    (void)argument;
    publish_offline_state();
    while (true) {
        EventBits_t bits = xEventGroupWaitBits(
            s_events,
            WIFI_CONNECTED_BIT | REFRESH_REQUESTED_BIT,
            pdTRUE,
            pdFALSE,
            pdMS_TO_TICKS(20));

        cJSON *message;
        while ((message = usb_transport_receive()) != NULL) {
            process_usb(message);
            cJSON_Delete(message);
        }
        bambu_command_t usb_command;
        if (connectivity_usb_active()) {
            while (xQueueReceive(s_bambu_command_queue, &usb_command, 0) == pdTRUE) send_bambu_command(usb_command);
            if (esp_timer_get_time() - s_last_wifi_poll > 10000000 && strcmp(s_connection_mode, "usb") != 0) {
                s_last_wifi_poll = esp_timer_get_time();
                check_wifi_link();
            }
            continue;
        }
        if (strcmp(s_connection_mode, "usb") == 0) {
            if (s_bridge_online) { s_bridge_online = false; publish_offline_state(); }
            continue;
        }
        if (!(bits & REFRESH_REQUESTED_BIT) && esp_timer_get_time() - s_last_wifi_poll < CONFIG_STATE_DISPLAY_POLL_SECONDS * 1000000LL) continue;
        s_last_wifi_poll = esp_timer_get_time();
        if (s_wifi_connected) {
            bambu_command_t command;
            while (s_bambu_command_queue != NULL && xQueueReceive(s_bambu_command_queue, &command, 0) == pdTRUE) {
                send_bambu_command(command);
            }
            if (fetch_snapshot(&s_work_snapshot)) {
                if (!s_bridge_online) ESP_LOGI(TAG, "Bridge snapshot received");
                s_last_snapshot = s_work_snapshot;
                s_have_snapshot = true;
                s_bridge_online = true;
                s_wifi_bridge_online = true;
                s_bridge_failures = 0;
                strlcpy(s_bridge_note, s_work_snapshot.preview_data ? "管理中心在线 · 预览数据" : "管理中心在线", sizeof(s_bridge_note));
                app_state_publish(&s_work_snapshot);
            } else {
                s_wifi_bridge_online = false;
                if (s_bridge_failures < UINT8_MAX) s_bridge_failures++;
                if (s_have_snapshot && s_bridge_failures < 12) {
                    strlcpy(s_bridge_note, "正在重连 · 保留上次数据", sizeof(s_bridge_note));
                    app_state_publish(&s_last_snapshot);
                } else {
                    s_bridge_online = false;
                    strlcpy(s_bridge_note, "管理中心不可达", sizeof(s_bridge_note));
                    publish_offline_state();
                }
            }
        } else {
            s_bridge_online = false;
            s_wifi_bridge_online = false;
            publish_offline_state();
        }
    }
}

static void wifi_event_handler(void *arg, esp_event_base_t event_base, int32_t event_id, void *event_data)
{
    (void)arg;
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        s_wifi_connected = false;
        strlcpy(s_ip, "--", sizeof(s_ip));
        esp_wifi_connect();
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        snprintf(s_ip, sizeof(s_ip), IPSTR, IP2STR(&event->ip_info.ip));
        s_wifi_connected = true;
        xEventGroupSetBits(s_events, WIFI_CONNECTED_BIT | REFRESH_REQUESTED_BIT);
    }
}

static void wifi_start(void)
{
    const device_config_values_t *device_config = device_config_get();
    if (strlen(device_config->wifi_ssid) == 0) {
        strlcpy(s_bridge_note, "Wi-Fi 未配置", sizeof(s_bridge_note));
        return;
    }

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t config = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&config));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL));

    wifi_config_t station = {0};
    strlcpy((char *)station.sta.ssid, device_config->wifi_ssid, sizeof(station.sta.ssid));
    strlcpy((char *)station.sta.password, device_config->wifi_password, sizeof(station.sta.password));
    station.sta.threshold.authmode = strlen(device_config->wifi_password) ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
    station.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &station));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    esp_sntp_config_t sntp = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
    esp_netif_sntp_init(&sntp);
}

void connectivity_start(void)
{
    const device_config_values_t *device_config = device_config_get();
    ESP_LOGI(TAG, "Preparing bridge data channel");
    uint8_t mac[6];
    ESP_ERROR_CHECK(esp_read_mac(mac, ESP_MAC_WIFI_STA));
    snprintf(s_device_id, sizeof(s_device_id), "%02X%02X%02X%02X%02X%02X", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    nvs_handle_t preferences;
    if (nvs_open("display_ui", NVS_READONLY, &preferences) == ESP_OK) {
        size_t size = sizeof(s_connection_mode);
        nvs_get_str(preferences, "connection", s_connection_mode, &size);
        nvs_close(preferences);
    }
    if (nvs_open("dotii_cfg", NVS_READONLY, &preferences) == ESP_OK) {
        size_t size = sizeof(s_usb_token);
        nvs_get_str(preferences, "usb_token", s_usb_token, &size);
        nvs_close(preferences);
    }
    s_events = xEventGroupCreate();
    s_bambu_command_queue = xQueueCreate(4, sizeof(bambu_command_t));
    s_work_tasks = heap_caps_calloc(CODEX_TASK_DETAIL_MAX, sizeof(*s_work_tasks),
                                    MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    ESP_ERROR_CHECK((s_events == NULL || s_bambu_command_queue == NULL || s_work_tasks == NULL) ?
                    ESP_ERR_NO_MEM : ESP_OK);
    ESP_LOGI(TAG, "Bridge data buffers ready");
    setenv("TZ", CONFIG_STATE_DISPLAY_TIMEZONE, 1);
    tzset();
    strlcpy(s_bridge_note,
            strlen(device_config->bridge_url) == 0 ? "未配置" : "等待管理中心",
            sizeof(s_bridge_note));

    /* Reserve the polling task before the Wi-Fi driver fragments internal RAM. */
    BaseType_t task_created = xTaskCreate(bridge_task, "bridge", 6144, NULL, 5, NULL);
    if (task_created != pdPASS) {
        strlcpy(s_bridge_note, "管理中心任务启动失败", sizeof(s_bridge_note));
        ESP_LOGE(TAG, "Unable to create bridge task");
    }
    ESP_LOGI(TAG, "Starting Wi-Fi station");
    wifi_start();
    ESP_LOGI(TAG, "Connectivity services started");
}

esp_err_t connectivity_start_usb(void)
{
    /* Reserve radio and display DMA allocations before USB ring buffers. */
    return usb_transport_start();
}

bool connectivity_bambu_command(const char *action)
{
    if (s_bambu_command_queue == NULL || action == NULL) return false;
    bambu_command_t command;
    if (strcmp(action, "pause") == 0) command = BAMBU_COMMAND_PAUSE;
    else if (strcmp(action, "resume") == 0) command = BAMBU_COMMAND_RESUME;
    else if (strcmp(action, "stop") == 0) command = BAMBU_COMMAND_STOP;
    else return false;
    if (xQueueSend(s_bambu_command_queue, &command, 0) != pdTRUE) return false;
    xEventGroupSetBits(s_events, REFRESH_REQUESTED_BIT);
    return true;
}

void connectivity_request_refresh(void)
{
    if (s_events != NULL) xEventGroupSetBits(s_events, REFRESH_REQUESTED_BIT);
}

bool connectivity_is_wifi_connected(void)
{
    return s_wifi_connected;
}

void connectivity_get_summary(char *buffer, size_t buffer_size)
{
    const device_config_values_t *device_config = device_config_get();
    if (strlen(device_config->wifi_ssid) == 0) {
        snprintf(buffer, buffer_size, "%s", ui_tr("未配置"));
    } else {
        snprintf(buffer, buffer_size, "%s · %s", device_config->wifi_ssid,
                 ui_tr(s_wifi_connected ? "已连接" : "连接中"));
    }
}

void connectivity_get_ip(char *buffer, size_t buffer_size)
{
    strlcpy(buffer, s_ip, buffer_size);
}

void connectivity_get_bridge_summary(char *buffer, size_t buffer_size)
{
    if (connectivity_usb_active()) snprintf(buffer, buffer_size, "USB · %s", ui_tr("已连接"));
    else if (!strcmp(s_connection_mode, "usb")) snprintf(buffer, buffer_size, "%s", ui_tr("等待 USB"));
    else strlcpy(buffer, ui_tr(s_bridge_note), buffer_size);
}
