#pragma once
#include <stdbool.h>
#include "lvgl.h"
void ui_language_init(void);
void ui_language_set(const char *language);
bool ui_language_english(void);
const char *ui_tr(const char *text);
void ui_language_refresh(void);
void ui_label_set_text(lv_obj_t *label, const char *text);
void ui_label_set_text_fmt(lv_obj_t *label, const char *format, ...) LV_FORMAT_ATTRIBUTE(2, 3);
