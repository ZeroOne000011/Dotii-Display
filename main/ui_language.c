#include "ui_language.h"
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include "nvs.h"
#include "esp_heap_caps.h"

typedef struct { const char *zh; const char *en; } translation_t;
static const translation_t s_texts[] = {
#include "ui_translations.inc"
};
static volatile bool s_english;
static bool s_rendered_english;
typedef struct binding {
    lv_obj_t *label;
    const translation_t *text;
    const lv_font_t *font;
    struct binding *next;
} binding_t;
static binding_t *s_labels;

void ui_language_init(void)
{
    nvs_handle_t handle;
    uint8_t value = 0;
    if (nvs_open("display_ui", NVS_READONLY, &handle) == ESP_OK) {
        nvs_get_u8(handle, "english", &value);
        nvs_close(handle);
    }
    s_english = value == 1;
}

void ui_language_set(const char *language)
{
    if (!language || (strcmp(language, "en") && strcmp(language, "zh-CN"))) return;
    bool english = strcmp(language, "en") == 0;
    if (english == s_english) return;
    s_english = english;
    nvs_handle_t handle;
    if (nvs_open("display_ui", NVS_READWRITE, &handle) == ESP_OK) {
        nvs_set_u8(handle, "english", english);
        nvs_commit(handle);
        nvs_close(handle);
    }
}

bool ui_language_english(void) { return s_english; }

const char *ui_tr(const char *text)
{
    if (!text) return "";
    for (size_t i = 0; i < sizeof(s_texts) / sizeof(s_texts[0]); i++) {
        if (!strcmp(text, s_texts[i].zh)) return s_english ? s_texts[i].en : s_texts[i].zh;
    }
    return text;
}

static void label_deleted(lv_event_t *event)
{
    binding_t *item = lv_event_get_user_data(event);
    binding_t **link = &s_labels;
    while (*link && *link != item) link = &(*link)->next;
    if (*link) *link = item->next;
    free(item);
}

void ui_label_set_text(lv_obj_t *label, const char *text)
{
    const translation_t *translation = NULL;
    /* Pointer identity binds only literal system text, never user content. */
    for (size_t i = 0; i < sizeof(s_texts) / sizeof(s_texts[0]); i++) {
        if (text == s_texts[i].zh || text == s_texts[i].en) { translation = &s_texts[i]; break; }
    }
    binding_t *item = s_labels;
    while (item && item->label != label) item = item->next;
    if (!item && translation) {
        item = heap_caps_calloc(1, sizeof(*item), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (item) {
            item->label = label;
            item->next = s_labels;
            s_labels = item;
            lv_obj_add_event_cb(label, label_deleted, LV_EVENT_DELETE, item);
        }
    }
    if (item) {
        /* A placeholder may have used the English font. User text must return
           to its original CJK-capable font before the binding is removed. */
        if (!translation && item->font) lv_obj_set_style_text_font(label, item->font, 0);
        item->text = translation;
    }
    lv_label_set_text(label, text);
}

void ui_label_set_text_fmt(lv_obj_t *label, const char *format, ...)
{
    /* Formatted values are dynamic, never keep a literal placeholder binding. */
    ui_label_set_text(label, "");
    va_list args;
    va_start(args, format);
    lv_label_set_text_vfmt(label, format, args);
    va_end(args);
}

void ui_language_refresh(void)
{
    bool changed = s_rendered_english != s_english;
    for (binding_t *item = s_labels; item; item = item->next) {
        if (!item->text) continue;
        bool initial = !item->font;
        if (initial) item->font = lv_obj_get_style_text_font(item->label, LV_PART_MAIN);
        if (changed || initial) {
            lv_label_set_text(item->label, s_english ? item->text->en : item->text->zh);
            lv_obj_set_style_text_font(item->label, s_english ? &lv_font_montserrat_20 : item->font, 0);
        }
    }
    s_rendered_english = s_english;
}
