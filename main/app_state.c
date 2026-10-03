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

/* 快照里是否有真实数据：preview_data 只说明 Codex 段是种子，服务端
   实时注入的 zai/bambu/claudecode 模块不受影响——任一在线即有真数据。 */
bool app_state_has_real_data(const codex_snapshot_t *snapshot)
{
    if (snapshot == NULL || !snapshot->valid) return false;
    if (!snapshot->preview_data) return true;
    return snapshot->zai_connected || snapshot->bambu_connected ||
           snapshot->claudecode_connected;
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
