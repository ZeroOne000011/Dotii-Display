#include "app_state.h"

#include <string.h>

#include "esp_heap_caps.h"
#include "freertos/semphr.h"

static QueueHandle_t s_snapshot_queue;
static codex_task_detail_t *s_tasks;
static size_t s_task_count;
static SemaphoreHandle_t s_tasks_lock;

static void assign_external_text(char **destination, const char *text)
{
    if (text == NULL || text[0] == '\0') {
        heap_caps_free(*destination);
        *destination = NULL;
        return;
    }
    size_t size = strlen(text) + 1;
    char *copy = heap_caps_realloc(*destination, size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (copy == NULL) {
        heap_caps_free(*destination);
        *destination = NULL;
        return;
    }
    *destination = copy;
    memcpy(*destination, text, size);
}

static void clear_task_text(codex_task_detail_t *task)
{
    if (task == NULL) return;
    heap_caps_free(task->last_user_message);
    heap_caps_free(task->conversation_text);
    task->last_user_message = NULL;
    task->conversation_text = NULL;
}

static void copy_task(codex_task_detail_t *destination, const codex_task_detail_t *source)
{
    char *last_user_message = destination->last_user_message;
    char *conversation_text = destination->conversation_text;
    *destination = *source;
    destination->last_user_message = last_user_message;
    destination->conversation_text = conversation_text;
    assign_external_text(&destination->last_user_message, source->last_user_message);
    assign_external_text(&destination->conversation_text, source->conversation_text);
}

QueueHandle_t app_state_queue_create(void)
{
    if (s_snapshot_queue == NULL) {
        s_snapshot_queue = xQueueCreate(1, sizeof(codex_snapshot_t));
        s_tasks = heap_caps_calloc(CODEX_TASK_DETAIL_MAX, sizeof(*s_tasks),
                                   MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        s_tasks_lock = xSemaphoreCreateMutex();
    }
    return s_snapshot_queue;
}

void app_state_tasks_publish(const codex_task_detail_t *tasks, size_t count)
{
    if (s_tasks == NULL || s_tasks_lock == NULL) return;
    if (tasks == NULL) count = 0;
    if (count > CODEX_TASK_DETAIL_MAX) count = CODEX_TASK_DETAIL_MAX;
    if (xSemaphoreTake(s_tasks_lock, pdMS_TO_TICKS(100)) != pdTRUE) return;
    if (tasks != NULL) {
        for (size_t index = 0; index < count; index++) copy_task(&s_tasks[index], &tasks[index]);
    }
    for (size_t index = count; index < CODEX_TASK_DETAIL_MAX; index++) {
        clear_task_text(&s_tasks[index]);
        memset(&s_tasks[index], 0, sizeof(s_tasks[index]));
    }
    s_task_count = count;
    xSemaphoreGive(s_tasks_lock);
}

size_t app_state_task_count(void)
{
    if (s_tasks_lock == NULL) return 0;
    if (xSemaphoreTake(s_tasks_lock, pdMS_TO_TICKS(100)) != pdTRUE) return 0;
    size_t count = s_task_count;
    xSemaphoreGive(s_tasks_lock);
    return count;
}

bool app_state_task_copy(size_t index, codex_task_detail_t *task)
{
    if (task == NULL || s_tasks == NULL || s_tasks_lock == NULL) return false;
    if (xSemaphoreTake(s_tasks_lock, pdMS_TO_TICKS(100)) != pdTRUE) return false;
    bool available = index < s_task_count;
    if (available) copy_task(task, &s_tasks[index]);
    xSemaphoreGive(s_tasks_lock);
    return available;
}

void app_state_publish(const codex_snapshot_t *snapshot)
{
    if (s_snapshot_queue != NULL && snapshot != NULL) {
        xQueueOverwrite(s_snapshot_queue, snapshot);
    }
}

void app_state_make_preview(codex_snapshot_t *snapshot)
{
    memset(snapshot, 0, sizeof(*snapshot));
    snapshot->valid = true;
    snapshot->preview_data = true;
    snapshot->source_online = false;
    snapshot->five_hour_available = true;
    snapshot->weekly_available = true;
    snapshot->weekly_tokens_available = true;
    snapshot->task_tokens_available = true;
    snapshot->conversation_mode = CODEX_CONVERSATION_PROGRESS;
    snapshot->codex_enabled = true;
    snapshot->bambu_enabled = true;
    snapshot->custom_enabled = true;
    snapshot->dotii_enabled = true;
    snapshot->custom_ring_enabled = true;
    snapshot->bambu_configured = true;
    snapshot->bambu_connected = true;
    snapshot->bambu_commandable = true;
    snapshot->docked_rotation_tenths = 840;
    snapshot->screen_off_timeout_seconds = 60;
    snapshot->sleep_timeout_seconds = 300;
    snapshot->charging_screen_off_timeout_seconds = 60;
    snapshot->charging_sleep_timeout_seconds = 300;
    snapshot->five_hour_remaining_percent = 68;
    snapshot->bambu_status = BAMBU_STATUS_PRINTING;
    snapshot->dotii_expression = DOTII_EXPRESSION_WORKING;
    snapshot->dotii_state_assigned = true;
    snapshot->dotii_state_duration_ms = 0;
    snapshot->dotii_state_token = 1;
    snapshot->dotii_touch_expression = DOTII_EXPRESSION_TOUCH_RESPONSE;
    snapshot->dotii_blink_expression = DOTII_EXPRESSION_BLINK;
    snapshot->dotii_long_idle_expression = DOTII_EXPRESSION_SLEEPY_YAWN;
    for (size_t index = 0; index < DOTII_EXPRESSION_COUNT; index++) {
        snapshot->dotii_expression_durations_ms[index] = 800;
    }
    snapshot->dotii_expression_durations_ms[DOTII_EXPRESSION_TOUCH_RESPONSE] = 1200;
    snapshot->bambu_progress = 68;
    snapshot->bambu_remaining_minutes = 42;
    snapshot->bambu_layer_current = 87;
    snapshot->bambu_layer_total = 142;
    snapshot->bambu_nozzle_temperature = 220;
    snapshot->bambu_bed_temperature = 55;
    snapshot->bambu_finish_at = time(NULL) + 42 * 60;
    snapshot->bambu_updated_at = time(NULL);
    snapshot->weekly_remaining_percent = 68;
    snapshot->weekly_tokens = 18420;
    snapshot->status = CODEX_STATUS_WORKING;
    snapshot->duration_seconds = 754;
    snapshot->message_count = 1;
    snapshot->task_tokens = 18420;
    snapshot->plan_completed = 2;
    snapshot->plan_total = 4;
    snapshot->custom_accent = 0xF2C66D;
    snapshot->custom_ring_start = 0xF2C66D;
    snapshot->custom_ring_end = 0x5DA9FF;
    snapshot->generated_at = time(NULL);
    strlcpy(snapshot->five_hour_reset_date, "08-18 14:40", sizeof(snapshot->five_hour_reset_date));
    strlcpy(snapshot->reset_date, "08-18 10:00", sizeof(snapshot->reset_date));
    strlcpy(snapshot->title, "等待 Codex 任务", sizeof(snapshot->title));
    strlcpy(snapshot->last_user_message, "请在电脑端启动 Dotii 管理中心。", sizeof(snapshot->last_user_message));
    strlcpy(snapshot->last_assistant_message, "Dotii 正在等待 Codex 任务数据。", sizeof(snapshot->last_assistant_message));
    snapshot->conversation_message_count = 2;
    strlcpy(snapshot->conversation_text,
            "正在连接 Dotii 管理中心。\n\n正在等待 Codex 数据更新。",
            sizeof(snapshot->conversation_text));
    strlcpy(snapshot->current_action, "等待数据更新", sizeof(snapshot->current_action));
    strlcpy(snapshot->plan_type, "preview", sizeof(snapshot->plan_type));
    strlcpy(snapshot->custom_title, "我的页面", sizeof(snapshot->custom_title));
    strlcpy(snapshot->custom_value, "你好，Dotii", sizeof(snapshot->custom_value));
    strlcpy(snapshot->custom_body, "这是从 Dotii 管理中心同步的自定义界面。", sizeof(snapshot->custom_body));
    strlcpy(snapshot->custom_footer, "自定义内容", sizeof(snapshot->custom_footer));
    strlcpy(snapshot->bambu_name, "Bambu Lab", sizeof(snapshot->bambu_name));
    strlcpy(snapshot->bambu_status_text, "打印中", sizeof(snapshot->bambu_status_text));
    strlcpy(snapshot->bambu_filename, "可爱机器人外壳.3mf", sizeof(snapshot->bambu_filename));
    strlcpy(snapshot->bambu_filament, "PLA Basic", sizeof(snapshot->bambu_filament));

    codex_task_detail_t task = {
        .status = snapshot->status,
        .conversation_mode = snapshot->conversation_mode,
        .duration_seconds = snapshot->duration_seconds,
        .message_count = snapshot->message_count,
        .started_at = time(NULL) - snapshot->duration_seconds,
        .updated_at = time(NULL),
    };
    strlcpy(task.thread_id, "preview-thread", sizeof(task.thread_id));
    strlcpy(task.title, snapshot->title, sizeof(task.title));
    task.last_user_message = snapshot->last_user_message;
    task.conversation_text = snapshot->conversation_text;
    app_state_tasks_publish(&task, 1);
}

const char *app_state_bambu_status_text(bambu_status_t status)
{
    switch (status) {
    case BAMBU_STATUS_IDLE: return "空闲";
    case BAMBU_STATUS_PREPARING: return "准备中";
    case BAMBU_STATUS_PRINTING: return "打印中";
    case BAMBU_STATUS_PAUSED: return "已暂停";
    case BAMBU_STATUS_COMPLETED: return "已完成";
    case BAMBU_STATUS_CANCELLING: return "取消中";
    case BAMBU_STATUS_FAULT: return "故障";
    default: return "离线";
    }
}

bambu_status_t app_state_bambu_status_from_string(const char *status)
{
    if (status == NULL) return BAMBU_STATUS_OFFLINE;
    if (strcmp(status, "idle") == 0) return BAMBU_STATUS_IDLE;
    if (strcmp(status, "preparing") == 0) return BAMBU_STATUS_PREPARING;
    if (strcmp(status, "printing") == 0) return BAMBU_STATUS_PRINTING;
    if (strcmp(status, "paused") == 0) return BAMBU_STATUS_PAUSED;
    if (strcmp(status, "completed") == 0) return BAMBU_STATUS_COMPLETED;
    if (strcmp(status, "cancelling") == 0) return BAMBU_STATUS_CANCELLING;
    if (strcmp(status, "fault") == 0) return BAMBU_STATUS_FAULT;
    return BAMBU_STATUS_OFFLINE;
}

dotii_expression_t app_state_dotii_expression_from_string(const char *expression)
{
    if (expression == NULL) return DOTII_EXPRESSION_IDLE_BREATH;
    if (strcmp(expression, "blink") == 0) return DOTII_EXPRESSION_BLINK;
    if (strcmp(expression, "curious") == 0) return DOTII_EXPRESSION_CURIOUS;
    if (strcmp(expression, "happy") == 0) return DOTII_EXPRESSION_COMPLETE;
    if (strcmp(expression, "sleepy_yawn") == 0) return DOTII_EXPRESSION_SLEEPY_YAWN;
    if (strcmp(expression, "touch_response") == 0) return DOTII_EXPRESSION_TOUCH_RESPONSE;
    if (strcmp(expression, "connecting") == 0) return DOTII_EXPRESSION_CONNECTING;
    if (strcmp(expression, "working") == 0) return DOTII_EXPRESSION_WORKING;
    if (strcmp(expression, "complete") == 0) return DOTII_EXPRESSION_COMPLETE;
    if (strcmp(expression, "failure") == 0) return DOTII_EXPRESSION_FAILURE;
    return DOTII_EXPRESSION_IDLE_BREATH;
}

const char *app_state_status_text(codex_task_status_t status)
{
    switch (status) {
    case CODEX_STATUS_WORKING: return "工作中";
    case CODEX_STATUS_WAITING: return "等待用户";
    case CODEX_STATUS_COMPLETED: return "已完成";
    case CODEX_STATUS_FAILED: return "失败";
    case CODEX_STATUS_IDLE: return "暂无任务";
    case CODEX_STATUS_OFFLINE: return "离线";
    default: return "未知";
    }
}

codex_task_status_t app_state_status_from_string(const char *status)
{
    if (status == NULL) return CODEX_STATUS_IDLE;
    if (strcmp(status, "working") == 0) return CODEX_STATUS_WORKING;
    if (strcmp(status, "waiting_user") == 0) return CODEX_STATUS_WAITING;
    if (strcmp(status, "completed") == 0) return CODEX_STATUS_COMPLETED;
    if (strcmp(status, "failed") == 0) return CODEX_STATUS_FAILED;
    if (strcmp(status, "offline") == 0) return CODEX_STATUS_OFFLINE;
    return CODEX_STATUS_IDLE;
}
