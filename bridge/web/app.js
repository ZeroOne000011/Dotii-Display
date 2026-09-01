const state = {
  overview: null,
  busy: false,
  toastTimer: null,
  customLoaded: false,
  customDirty: false,
  customSaving: false,
  customSourceImage: null,
  customSourceRevision: 0,
  customSourceUrl: null,
  crop: {
    image: null,
    file: null,
    objectUrl: null,
    zoom: 1,
    offsetX: 0,
    offsetY: 0,
    dragging: false,
    pointerId: null,
    lastX: 0,
    lastY: 0,
  },
  moduleSaving: {},
  bambuLoaded: false,
  displayDirty: false,
  displaySaving: false,
  codexUiSaving: false,
  codexChecking: false,
  codexCheck: null,
  firmwarePortsSignature: "",
  bluetoothDevicesSignature: "",
  selectedCodexTaskId: "",
  dotiiExpressions: [],
  dotiiFollowingLive: true,
  dotiiLiveExpression: "idle_breath",
  dotiiDisplayedExpression: "idle_breath",
  dotiiConfig: null,
  dotiiConfigDirty: false,
  dotiiConfigSaving: false,
};

const byId = (id) => document.getElementById(id);
const setText = (id, value) => {
  const target = byId(id);
  if (target) target.textContent = value;
};
const setMarkdown = (id, value) => {
  const target = byId(id);
  const renderer = window.StateDisplayMarkdown?.renderMarkdown;
  if (typeof renderer === "function") target.innerHTML = renderer(String(value));
  else target.textContent = String(value);
};
const setMarkdownMessages = (id, values) => {
  const target = byId(id);
  const renderer = window.StateDisplayMarkdown?.renderMarkdown;
  target.replaceChildren();
  target.setAttribute("role", "list");
  values.forEach((value) => {
    const item = document.createElement("div");
    item.className = "codex-message-item";
    item.setAttribute("role", "listitem");
    if (typeof renderer === "function") item.innerHTML = renderer(String(value));
    else item.textContent = String(value);
    target.append(item);
  });
};

const STATUS_TEXT = {
  working: "工作中",
  waiting_user: "等待用户",
  completed: "已完成",
  failed: "失败",
  idle: "暂无任务",
  offline: "暂时离线",
};

const DISPLAY_TIMEOUT_OPTIONS = [10, 30, 60, 180, 300, 600, 1800, 0];
const DISPLAY_TIMEOUT_PAIRS = [
  { screenOff: "screen-off-timeout", sleep: "sleep-timeout" },
  { screenOff: "charging-screen-off-timeout", sleep: "charging-sleep-timeout" },
];

const DOTII_EXPRESSION_FALLBACK = {
  id: "idle_breath",
  label: "待机呼吸",
  purpose: "默认空闲循环",
  src: "/assets/expressions/dotii-idle-breath.webp",
};
const DOTII_STATE_FALLBACK = [
  { id: "codex_waiting_user", label: "等待操作", group: "codex" },
  { id: "codex_working", label: "工作中", group: "codex" },
  { id: "codex_completed", label: "任务完成", group: "codex" },
  { id: "codex_failure", label: "失败或异常", group: "codex" },
  { id: "bambu_paused", label: "已暂停", group: "bambu" },
  { id: "bambu_printing", label: "打印中", group: "bambu" },
  { id: "bambu_completed", label: "打印完成", group: "bambu" },
  { id: "bambu_failure", label: "故障或异常", group: "bambu" },
];
const DOTII_STATE_GROUP_FALLBACK = [
  { id: "codex", label: "Codex 状态" },
  { id: "bambu", label: "Bambu 状态" },
];
const DOTII_FIXED_STATE_FALLBACK = [
  { id: "idle", label: "空闲", expression: "idle_breath", duration_ms: 800 },
  { id: "blink", label: "待机眨眼", expression: "blink", duration_ms: 800 },
  { id: "long_idle", label: "长时间无操作", expression: "sleepy_yawn", duration_ms: 800 },
  { id: "touch", label: "触摸屏幕", expression: "touch_response", duration_ms: 1200 },
  { id: "connecting", label: "连接中", expression: "connecting", duration_ms: 800 },
];
const DOTII_DURATION_OPTION_FALLBACK = [
  { value: 1000, label: "1 秒" },
  { value: 3000, label: "3 秒" },
  { value: 5000, label: "5 秒" },
  { value: 30000, label: "30 秒" },
  { value: 0, label: "保持" },
];

function cloneDotiiConfig(config) {
  return JSON.parse(JSON.stringify(config));
}

function dotiiExpression(expressionId) {
  return state.dotiiExpressions.find((item) => item.id === expressionId) || DOTII_EXPRESSION_FALLBACK;
}

function showDotiiExpression(expressionId, { live = false } = {}) {
  const expression = dotiiExpression(expressionId);
  state.dotiiDisplayedExpression = expression.id;
  const image = byId("dotii-current-expression");
  image.src = expression.src;
  image.alt = `Dotii ${expression.label}动画`;
  setText("dotii-stage-title", expression.label);
  document.querySelectorAll(".dotii-preview-button").forEach((button) => {
    button.classList.toggle("selected", button.dataset.expressionId === expression.id);
  });
  byId("dotii-follow-live").hidden = live;
}

async function loadDotiiExpressions() {
  try {
    const response = await fetch("/assets/expressions/animations.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const manifest = await response.json();
    if (!Array.isArray(manifest.animations) || !manifest.animations.length) throw new Error("动画清单为空");
    state.dotiiExpressions = manifest.animations;
    setText("dotii-library-state", `${manifest.animations.length} 个内置动画`);
    if (state.dotiiConfig) renderDotiiConfigEditor();
    showDotiiExpression(state.dotiiLiveExpression, { live: state.dotiiFollowingLive });
  } catch (error) {
    state.dotiiExpressions = [DOTII_EXPRESSION_FALLBACK];
    setText("dotii-library-state", "动画读取失败，请刷新页面");
    byId("dotii-config-list").replaceChildren();
  }
}

function dotiiAvailableStates() {
  const values = state.dotiiConfig?.available_states;
  if (!Array.isArray(values) || !values.length) return DOTII_STATE_FALLBACK;
  const fallback = Object.fromEntries(DOTII_STATE_FALLBACK.map((item) => [item.id, item]));
  return values.map((item) => ({ ...fallback[item.id], ...item }));
}

function dotiiStateGroups() {
  const values = state.dotiiConfig?.state_groups;
  return Array.isArray(values) && values.length ? values : DOTII_STATE_GROUP_FALLBACK;
}

function dotiiFixedStates() {
  const values = state.dotiiConfig?.fixed_states;
  return Array.isArray(values) && values.length ? values : DOTII_FIXED_STATE_FALLBACK;
}

function dotiiDurationOptions() {
  const values = state.dotiiConfig?.duration_options;
  return Array.isArray(values) && values.length ? values : DOTII_DURATION_OPTION_FALLBACK;
}

function markDotiiConfigDirty() {
  state.dotiiConfigDirty = true;
  setText("dotii-config-save-state", "有尚未保存的修改");
  byId("dotii-config-save").disabled = false;
}

function setDotiiStateAssignment(stateId, expressionId = null) {
  Object.values(state.dotiiConfig.animations).forEach((animation) => {
    animation.states = Array.isArray(animation.states)
      ? animation.states.filter((item) => item !== stateId)
      : [];
  });
  if (expressionId && state.dotiiConfig?.animations?.[expressionId]) {
    state.dotiiConfig.animations[expressionId].states.push(stateId);
  }
  markDotiiConfigDirty();
  renderDotiiConfigEditor();
}

function makeDotiiPreviewButton(expression, extraClass = "") {
  const preview = document.createElement("button");
  preview.type = "button";
  preview.className = `dotii-preview-button ${extraClass}`.trim();
  preview.classList.toggle("selected", state.dotiiDisplayedExpression === expression.id);
  preview.dataset.expressionId = expression.id;
  preview.setAttribute("aria-label", `预览${expression.label}`);
  const image = document.createElement("img");
  image.src = expression.src;
  image.alt = "";
  preview.append(image);
  preview.addEventListener("click", () => {
    state.dotiiFollowingLive = false;
    showDotiiExpression(expression.id);
    setText("dotii-stage-reason", `${expression.purpose}。此处按内置资源原速预览。`);
  });
  return preview;
}

function renderDotiiConfigEditor() {
  const list = byId("dotii-config-list");
  if (!state.dotiiConfig || !state.dotiiExpressions.length) return;
  const availableStates = dotiiAvailableStates();
  const stateGroups = dotiiStateGroups();
  const fixedStates = dotiiFixedStates();
  const fixedExpressions = new Set(fixedStates.map((item) => item.expression));
  const durationOptions = dotiiDurationOptions();
  const groupLabels = Object.fromEntries(stateGroups.map((item) => [item.id, item.label.replace(/\s*状态$/, "")]));
  const stateLabels = Object.fromEntries(availableStates.map((item) => [
    item.id,
    `${groupLabels[item.group] || "Dotii"} ${item.label}`,
  ]));
  list.replaceChildren();

  const fixedRow = document.createElement("article");
  fixedRow.className = "dotii-fixed-row";
  const fixedHeading = document.createElement("div");
  fixedHeading.className = "dotii-fixed-heading";
  const fixedTitle = document.createElement("strong");
  fixedTitle.textContent = "Dotii 默认状态";
  fixedHeading.append(fixedTitle);
  const fixedGrid = document.createElement("div");
  fixedGrid.className = "dotii-fixed-grid";
  fixedStates.forEach((fixedState) => {
    const expression = dotiiExpression(fixedState.expression);
    const item = document.createElement("div");
    item.className = "dotii-fixed-item";
    const preview = makeDotiiPreviewButton(expression, "dotii-fixed-preview");
    const copy = document.createElement("div");
    const label = document.createElement("strong");
    label.textContent = fixedState.label;
    copy.append(label);
    const locked = document.createElement("span");
    locked.textContent = "默认";
    item.append(preview, copy, locked);
    fixedGrid.append(item);
  });
  fixedRow.append(fixedHeading, fixedGrid);
  list.append(fixedRow);

  state.dotiiExpressions.forEach((expression) => {
    if (fixedExpressions.has(expression.id)) return;
    const settings = state.dotiiConfig.animations?.[expression.id];
    if (!settings) return;
    const row = document.createElement("article");
    row.className = "dotii-config-row";

    const preview = makeDotiiPreviewButton(expression);

    const main = document.createElement("div");
    main.className = "dotii-config-main";
    const heading = document.createElement("div");
    heading.className = "dotii-config-row-heading";
    const title = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = expression.label;
    title.append(name);

    const picker = document.createElement("details");
    picker.className = "dotii-state-picker";
    const summary = document.createElement("summary");
    summary.textContent = "分配状态";
    const options = document.createElement("div");
    options.className = "dotii-state-options";
    stateGroups.forEach((group) => {
      const groupedStates = availableStates.filter((item) => item.group === group.id);
      if (!groupedStates.length) return;
      const groupHeading = document.createElement("div");
      groupHeading.className = "dotii-state-group-heading";
      groupHeading.setAttribute("role", "presentation");
      const groupName = document.createElement("span");
      groupName.textContent = group.label;
      groupHeading.append(groupName);
      options.append(groupHeading);
      groupedStates.forEach((stateOption) => {
        const label = document.createElement("label");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = settings.states.includes(stateOption.id);
        checkbox.setAttribute("aria-label", `${group.label}：${stateOption.label}`);
        checkbox.addEventListener("change", () => {
          setDotiiStateAssignment(stateOption.id, checkbox.checked ? expression.id : null);
        });
        const labelText = document.createElement("span");
        labelText.textContent = stateOption.label;
        label.append(checkbox, labelText);
        options.append(label);
      });
    });
    picker.append(summary, options);
    heading.append(title, picker);

    const chips = document.createElement("div");
    chips.className = "dotii-state-chips";
    settings.states.forEach((stateId) => {
      const chip = document.createElement("span");
      chip.textContent = stateLabels[stateId] || stateId;
      chips.append(chip);
    });
    if (!settings.states.length) {
      const empty = document.createElement("span");
      empty.className = "empty";
      empty.textContent = "未分配状态";
      chips.append(empty);
    }
    main.append(heading, chips);

    const duration = document.createElement("label");
    duration.className = "dotii-duration-field";
    const durationLabel = document.createElement("span");
    const durationId = `dotii-duration-${expression.id}`;
    durationLabel.id = `${durationId}-label`;
    durationLabel.textContent = "动画持续时间";
    const control = document.createElement("div");
    control.className = "ui-select dotii-duration-select";
    control.dataset.uiSelect = durationId;
    const input = document.createElement("input");
    input.id = durationId;
    input.type = "hidden";
    input.value = String(Number(settings.state_duration_ms ?? 0));
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "ui-select-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-labelledby", `${durationLabel.id} ${durationId}-value`);
    const value = document.createElement("span");
    value.id = `${durationId}-value`;
    const chevron = document.createElement("span");
    chevron.className = "ui-select-chevron";
    chevron.setAttribute("aria-hidden", "true");
    trigger.append(value, chevron);
    const menu = document.createElement("div");
    menu.className = "ui-select-menu";
    menu.setAttribute("role", "listbox");
    menu.setAttribute("aria-labelledby", durationLabel.id);
    menu.hidden = true;
    durationOptions.forEach((durationOption) => {
      const option = document.createElement("button");
      option.type = "button";
      option.setAttribute("role", "option");
      option.dataset.value = String(durationOption.value);
      option.textContent = durationOption.label;
      menu.append(option);
    });
    control.append(input, trigger, menu);
    input.addEventListener("change", () => {
      settings.state_duration_ms = Number(input.value);
      markDotiiConfigDirty();
    });
    duration.append(durationLabel, control);
    row.append(preview, main, duration);
    list.append(row);
    setupUiSelect(control);
  });
}

async function saveDotiiConfig(event) {
  event.preventDefault();
  if (!state.dotiiConfig || state.dotiiConfigSaving || !state.dotiiConfigDirty) return;
  state.dotiiConfigSaving = true;
  byId("dotii-config-save").disabled = true;
  setText("dotii-config-save-state", "正在保存");
  try {
    const response = await fetch("/api/v1/admin/dotii", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ animations: state.dotiiConfig.animations }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "动画设置保存失败");
    state.dotiiConfig = cloneDotiiConfig(result.dotii_config);
    state.dotiiConfigDirty = false;
    setText("dotii-config-save-state", "配置已同步");
    showToast("动画设置已保存并同步");
    renderDotiiConfigEditor();
  } catch (error) {
    setText("dotii-config-save-state", "保存失败，请检查设置");
    byId("dotii-config-save").disabled = false;
    showToast(error.message || "动画设置保存失败");
  } finally {
    state.dotiiConfigSaving = false;
  }
}

function renderDotii(dotii, enabled) {
  const expressionId = typeof dotii.expression === "string" ? dotii.expression : "idle_breath";
  state.dotiiLiveExpression = expressionId;
  if (!state.moduleSaving.dotii) byId("dotii-enabled").checked = enabled;
  byId("dotii-content").hidden = !enabled;
  setText("dotii-nav-state", enabled ? dotiiExpression(expressionId).label : "已关闭");
  if (state.dotiiFollowingLive) {
    showDotiiExpression(expressionId, { live: true });
    setText("dotii-stage-reason", dotii.reason || "当前处于空闲状态");
  }
}

function closeUiSelect(control) {
  if (!control) return;
  control.classList.remove("open");
  control.querySelector(".ui-select-trigger")?.setAttribute("aria-expanded", "false");
  const menu = control.querySelector(".ui-select-menu");
  if (menu) menu.hidden = true;
}

function closeAllUiSelects(except = null) {
  document.querySelectorAll("[data-ui-select]").forEach((control) => {
    if (control !== except) closeUiSelect(control);
  });
}

function openUiSelect(control, focusSelected = false) {
  closeAllUiSelects(control);
  control.classList.add("open");
  control.querySelector(".ui-select-trigger")?.setAttribute("aria-expanded", "true");
  const menu = control.querySelector(".ui-select-menu");
  if (!menu) return;
  menu.hidden = false;
  if (focusSelected) {
    (menu.querySelector('[aria-selected="true"]:not(:disabled)') ||
      menu.querySelector("button:not(:disabled)"))?.focus();
  }
}

function setUiSelectValue(id, value, dispatch = false) {
  const input = byId(id);
  const control = input?.closest("[data-ui-select]");
  if (!input || !control) return;
  const normalized = String(value);
  const selected = [...control.querySelectorAll("[data-value]")]
    .find((option) => option.dataset.value === normalized);
  if (!selected) return;
  input.value = normalized;
  setText(`${id}-value`, selected.textContent.trim());
  control.querySelectorAll("[data-value]").forEach((option) => {
    option.setAttribute("aria-selected", String(option === selected));
  });
  if (dispatch) {
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }
}

function refreshTimeoutConstraints() {
  DISPLAY_TIMEOUT_PAIRS.forEach(({ screenOff: screenOffId, sleep: sleepId }) => {
    const screenOff = Number(byId(screenOffId).value);
    const sleep = Number(byId(sleepId).value);
    document.querySelectorAll(`[data-ui-select="${screenOffId}"] [data-value]`).forEach((option) => {
      const value = Number(option.dataset.value);
      option.disabled = sleep !== 0 && (value === 0 || value > sleep);
    });
    document.querySelectorAll(`[data-ui-select="${sleepId}"] [data-value]`).forEach((option) => {
      const value = Number(option.dataset.value);
      option.disabled = screenOff === 0 ? value !== 0 : value !== 0 && value < screenOff;
    });
  });
}

function normalizeTimeoutPair(changedId, screenOffId, sleepId) {
  const screenOff = Number(byId(screenOffId).value);
  const sleep = Number(byId(sleepId).value);
  if (screenOff === 0) {
    if (sleep !== 0) setUiSelectValue(sleepId, 0);
  } else if (sleep !== 0 && sleep < screenOff) {
    if (changedId === screenOffId) {
      const nextSleep = DISPLAY_TIMEOUT_OPTIONS.find((value) => value === 0 || value >= screenOff) ?? 0;
      setUiSelectValue(sleepId, nextSleep);
    } else {
      const earlier = DISPLAY_TIMEOUT_OPTIONS.filter((value) => value > 0 && value <= sleep);
      setUiSelectValue(screenOffId, earlier.at(-1) ?? 10);
    }
  }
  refreshTimeoutConstraints();
}

function setupUiSelect(control) {
    if (!control || control.dataset.uiSelectReady === "true") return;
    const id = control.dataset.uiSelect;
    const trigger = control.querySelector(".ui-select-trigger");
    const menu = control.querySelector(".ui-select-menu");
    if (!id || !trigger || !menu || !byId(id)) return;
    control.dataset.uiSelectReady = "true";
    trigger.addEventListener("click", () => {
      if (control.classList.contains("open")) closeUiSelect(control);
      else openUiSelect(control);
    });
    trigger.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        openUiSelect(control, true);
      } else if (event.key === "Escape") {
        closeUiSelect(control);
      }
    });
    menu.addEventListener("keydown", (event) => {
      const options = [...menu.querySelectorAll("button:not(:disabled)")];
      const index = options.indexOf(document.activeElement);
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 :
          event.key === "ArrowDown" ? (index + 1 + options.length) % options.length :
          (index - 1 + options.length) % options.length;
        options[next]?.focus();
      } else if (event.key === "Escape") {
        event.preventDefault();
        closeUiSelect(control);
        trigger.focus();
      }
    });
    menu.addEventListener("click", (event) => {
      const option = event.target.closest("[data-value]");
      if (!option || option.disabled) return;
      setUiSelectValue(id, option.dataset.value, true);
      closeUiSelect(control);
      trigger.focus();
    });
    setUiSelectValue(id, byId(id).value);
}

function setupUiSelects() {
  document.querySelectorAll("[data-ui-select]").forEach((control) => {
    setupUiSelect(control);
  });
  refreshTimeoutConstraints();
  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-ui-select]")) closeAllUiSelects();
  });
}

function showToast(message) {
  const toast = byId("toast");
  toast.textContent = message;
  toast.classList.add("visible");
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => toast.classList.remove("visible"), 2400);
}

function formatNumber(value, available = true) {
  if (!available || !Number.isFinite(Number(value))) return "--";
  return new Intl.NumberFormat("zh-CN").format(Number(value));
}

function formatDuration(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  if (value < 60) return `${Math.floor(value)} 秒`;
  if (value < 3600) return `${Math.floor(value / 60)} 分钟`;
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  return `${hours} 小时 ${minutes} 分钟`;
}

function formatTaskDuration(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const remainingSeconds = value % 60;
  if (hours > 0) return `耗时 ${hours} 小时 ${minutes} 分钟 ${remainingSeconds} 秒`;
  if (minutes > 0) return `耗时 ${minutes} 分钟 ${remainingSeconds} 秒`;
  return `耗时 ${remainingSeconds} 秒`;
}

function formatTime(epoch) {
  if (!epoch) return "尚未更新";
  const date = new Date(Number(epoch) * 1000);
  if (Number.isNaN(date.getTime())) return "尚未更新";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).format(date);
}

function formatClock(epoch) {
  if (!epoch) return "--";
  const date = new Date(Number(epoch) * 1000);
  if (Number.isNaN(date.getTime())) return "--";
  return new Intl.DateTimeFormat("zh-CN", {hour: "2-digit", minute: "2-digit"}).format(date);
}

function formatMinutes(value) {
  const minutes = Math.max(0, Number(value) || 0);
  if (!minutes) return "--";
  if (minutes < 60) return `${minutes} 分钟`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`;
}

function renderBambu(bambu = {}, config = {}, enabled = true) {
  if (!state.bambuLoaded) {
    byId("bambu-name").value = config.name || "Bambu Lab";
    byId("bambu-host").value = config.host || "";
    byId("bambu-serial").value = config.serial || "";
    byId("bambu-camera-enabled").checked = config.camera_enabled !== false;
    byId("bambu-access-code").placeholder = config.has_access_code ? "已保存，留空表示不修改" : "请输入局域网访问码";
    state.bambuLoaded = true;
  }
  const configured = Boolean(bambu.configured || config.configured);
  const connected = Boolean(bambu.connected);
  byId("bambu-empty").hidden = configured;
  byId("bambu-dashboard").hidden = !configured;
  const connection = byId("bambu-connection");
  connection.dataset.state = connected ? "online" : configured ? "waiting" : "offline";
  connection.textContent = connected ? "已连接" : configured ? "正在连接" : "尚未添加";
  const dashboardConnection = byId("bambu-dashboard-connection");
  dashboardConnection.dataset.state = connection.dataset.state;
  dashboardConnection.textContent = connection.textContent;
  setText("bambu-dashboard-name", config.name || bambu.name || "Bambu Lab");
  setText("bambu-status", bambu.status_text || "离线");
  setText("bambu-progress", connected ? `${Number(bambu.progress) || 0}%` : "--");
  byId("bambu-progress-fill").style.width = connected ? `${Math.max(0, Math.min(100, Number(bambu.progress) || 0))}%` : "0%";
  setText("bambu-remaining", formatMinutes(bambu.remaining_minutes));
  setText("bambu-finish", formatClock(bambu.finish_epoch));
  setText("bambu-filename", bambu.filename || "--");
  setText("bambu-layer", bambu.layer_total ? `${bambu.layer_current || 0} / ${bambu.layer_total}` : "--");
  setText("bambu-filament", bambu.filament || "--");
  setText("bambu-nozzle", connected ? `${Number(bambu.nozzle_temperature || 0).toFixed(0)} °C` : "--");
  setText("bambu-bed", connected ? `${Number(bambu.bed_temperature || 0).toFixed(0)} °C` : "--");
  setText("bambu-updated", bambu.updated_at_epoch ? `更新于 ${formatTime(bambu.updated_at_epoch)}` : "尚未收到数据");
  const camera = byId("bambu-camera");
  camera.hidden = !bambu.camera_available;
  byId("bambu-camera-empty").hidden = Boolean(bambu.camera_available);
  if (bambu.camera_available) camera.src = `/api/v1/admin/bambu/camera.jpg?v=${encodeURIComponent(bambu.camera_revision || Date.now())}`;
  const action = bambu.status === "paused" ? "resume" : "pause";
  byId("bambu-pause").dataset.action = action;
  byId("bambu-pause").textContent = action === "resume" ? "继续" : "暂停";
  byId("bambu-pause").disabled = !connected || !["printing", "paused"].includes(bambu.status);
  byId("bambu-stop").disabled = !connected || !["printing", "paused", "preparing"].includes(bambu.status);
  const error = !configured
    ? ""
    : !connected && bambu.last_error
      ? "暂时无法连接打印机，请确认打印机已开机并与电脑连接到同一网络。"
      : bambu.camera_error
        ? "相机画面暂时不可用，打印状态仍会正常更新。"
        : "";
  byId("bambu-error").hidden = !error;
  setText("bambu-error", error);
  setText("bambu-nav-state", !enabled ? "已关闭" : connected ? (bambu.status_text || "在线") : configured ? "连接中" : "添加打印机");
}

function setBridgeState(bridge) {
  const indicator = byId("bridge-state");
  const collectorState = bridge.collector_state || "starting";
  const bridgeOnline = bridge.online !== false;
  indicator.dataset.state = bridgeOnline ? "online" : "error";
  setText("bridge-state-label", bridgeOnline ? "管理中心在线" : "管理中心连接失败");
  setText("collector-detail", collectorState === "disabled" ? "Codex 模块已关闭" : collectorState === "online" ? "已连接" : collectorState === "error" ? "需要修复" : "正在连接");
  setText("bridge-uptime", formatDuration(Date.now() / 1000 - Number(bridge.started_at_epoch || 0)));
  byId("auto-start").checked = Boolean(bridge.auto_start);
  setText("device-url", bridge.device_url || "--");
  setText("bridge-token", bridge.token || "--");
  setText("local-url", bridge.local_url || "--");
}

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MiB`;
}

function renderFirmware(firmware = {}) {
  const packageInfo = firmware.package || {};
  const running = firmware.operation_state === "running";
  const success = firmware.operation_state === "success";
  const ready = Boolean(firmware.tool_ready && packageInfo.ready);
  const ports = Array.isArray(firmware.ports) ? firmware.ports : [];
  const dotiiPorts = ports.filter((port) => port.dotii);
  const badge = byId("firmware-badge");
  badge.dataset.state = running ? "running" : success ? "ready" : ready ? "ready" : "error";
  badge.textContent = running ? "烧录中" : success ? "已完成" : ready ? "固件就绪" : "需要处理";
  setText("firmware-version", packageInfo.version ? `v${packageInfo.version}` : "--");
  setText("firmware-size", packageInfo.app_size ? formatBytes(packageInfo.app_size) : "--");
  setText("firmware-hash", packageInfo.app_sha256 ? packageInfo.app_sha256.slice(0, 20) : "--");

  const signature = JSON.stringify(ports.map((port) => [port.port, port.name, port.dotii]));
  const input = byId("firmware-port");
  const control = input.closest("[data-ui-select]");
  const menu = control.querySelector(".ui-select-menu");
  if (signature !== state.firmwarePortsSignature) {
    state.firmwarePortsSignature = signature;
    const previous = input.value;
    menu.replaceChildren();
    ports.forEach((port) => {
      const option = document.createElement("button");
      option.type = "button";
      option.setAttribute("role", "option");
      option.dataset.value = port.port;
      option.disabled = !port.dotii;
      option.textContent = `${port.port} · ${port.dotii ? "Dotii" : port.name}`;
      menu.append(option);
    });
    const selected = dotiiPorts.some((port) => port.port === previous) ? previous : dotiiPorts[0]?.port || "";
    input.value = selected;
    if (selected) setUiSelectValue("firmware-port", selected);
    else setText("firmware-port-value", ports.length ? "未识别到 Dotii" : "未发现串口设备");
  }
  control.querySelector(".ui-select-trigger").disabled = running || !ports.length;
  const progress = Math.max(0, Math.min(100, Number(firmware.progress) || 0));
  byId("firmware-progress").firstElementChild.style.width = `${progress}%`;
  byId("firmware-progress").closest(".firmware-panel").dataset.running = String(running);
  setText("firmware-detail", firmware.operation_detail || (ready ? "请选择 Dotii" : packageInfo.error || "烧录组件不可用"));
  byId("firmware-refresh").disabled = running;
  byId("firmware-flash").disabled = running || !ready || !input.value || !dotiiPorts.some((port) => port.port === input.value);
  byId("firmware-flash").textContent = running ? `正在烧录 ${progress}%` : "烧录 Dotii";
}

function renderBluetooth(bluetooth = {}) {
  const ready = Boolean(bluetooth.dependency_ready);
  const running = bluetooth.operation_state === "running";
  const success = bluetooth.operation_state === "success";
  const devices = Array.isArray(bluetooth.devices) ? bluetooth.devices : [];
  const badge = byId("bluetooth-badge");
  badge.dataset.state = running ? "running" : ready ? "ready" : "error";
  badge.textContent = running ? "处理中" : ready ? (success ? "已连接" : "蓝牙就绪") : "需要处理";

  const input = byId("bluetooth-device");
  const control = input.closest("[data-ui-select]");
  const menu = control.querySelector(".ui-select-menu");
  const signature = JSON.stringify(devices.map((device) => [device.address, device.name, device.rssi]));
  if (signature !== state.bluetoothDevicesSignature) {
    state.bluetoothDevicesSignature = signature;
    const previous = input.value || bluetooth.last_address || "";
    menu.replaceChildren();
    devices.forEach((device) => {
      const option = document.createElement("button");
      option.type = "button";
      option.setAttribute("role", "option");
      option.dataset.value = device.address;
      option.textContent = `${device.name || "Dotii"} · ${device.rssi} dBm`;
      menu.append(option);
    });
    const selected = devices.some((device) => device.address === previous) ? previous : devices[0]?.address || "";
    input.value = selected;
    if (selected) setUiSelectValue("bluetooth-device", selected);
    else setText("bluetooth-device-value", devices.length ? "请选择 Dotii" : "未发现附近设备");
  }
  control.querySelector(".ui-select-trigger").disabled = running || !devices.length;
  const status = bluetooth.device_status || {};
  const managementStatus = typeof status.bridge === "string"
    ? status.bridge.replace("桥接", "管理中心")
    : "管理中心状态未知";
  const liveDetail = status.name
    ? `${status.name} · ${status.wifi ? "Wi-Fi 已连接" : "Wi-Fi 未连接"} · ${managementStatus}`
    : bluetooth.operation_detail;
  setText("bluetooth-detail", liveDetail || (ready ? "请扫描附近的 Dotii" : "需要安装 Windows 蓝牙连接组件"));
  byId("bluetooth-install").hidden = ready;
  byId("bluetooth-install").disabled = running;
  byId("bluetooth-scan").disabled = running || !ready;
  byId("bluetooth-configure").disabled = running || !ready || !input.value || !byId("bluetooth-ssid").value.trim();
  byId("bluetooth-configure").textContent = running ? "正在连接…" : "保存并连接";
}

function customFormValue() {
  return {
    enabled: byId("custom-enabled").checked,
    title: byId("custom-title").value.trim(),
    value: byId("custom-value").value.trim(),
    body: byId("custom-body").value.trim(),
    footer: byId("custom-footer").value.trim(),
    accent: byId("custom-value-color").value.toUpperCase(),
    title_visible: byId("custom-title-visible").checked,
    title_color: byId("custom-title-color").value.toUpperCase(),
    value_visible: byId("custom-value-visible").checked,
    value_color: byId("custom-value-color").value.toUpperCase(),
    body_visible: byId("custom-body-visible").checked,
    body_color: byId("custom-body-color").value.toUpperCase(),
    footer_visible: byId("custom-footer-visible").checked,
    footer_color: byId("custom-footer-color").value.toUpperCase(),
    background_image: byId("custom-background-image").checked,
    image_fit: byId("custom-image-fit").value,
    image_opacity: Number(byId("custom-image-opacity").value),
    ring_enabled: byId("custom-ring-enabled").checked,
    ring_start: byId("custom-ring-start").value.toUpperCase(),
    ring_end: byId("custom-ring-end").value.toUpperCase(),
  };
}

function drawImageFitted(context, image, fit) {
  const scale = fit === "contain"
    ? Math.min(466 / image.naturalWidth, 466 / image.naturalHeight)
    : Math.max(466 / image.naturalWidth, 466 / image.naturalHeight);
  const width = image.naturalWidth * scale;
  const height = image.naturalHeight * scale;
  context.drawImage(image, (466 - width) / 2, (466 - height) / 2, width, height);
}

function wrapCanvasText(context, text, maxWidth, maxLines) {
  const lines = [];
  let line = "";
  for (const character of String(text || "")) {
    if (character === "\n") {
      lines.push(line);
      line = "";
      if (lines.length >= maxLines) break;
      continue;
    }
    const candidate = line + character;
    if (line && context.measureText(candidate).width > maxWidth) {
      lines.push(line);
      line = character;
      if (lines.length >= maxLines) break;
    } else {
      line = candidate;
    }
  }
  if (lines.length < maxLines && line) lines.push(line);
  return lines;
}

function drawCenteredText(context, text, y, maxWidth, lineHeight, maxLines) {
  const lines = wrapCanvasText(context, text, maxWidth, maxLines);
  const start = y - ((lines.length - 1) * lineHeight) / 2;
  lines.forEach((line, index) => context.fillText(line, 233, start + index * lineHeight));
}

function renderCustomPreview(custom = customFormValue()) {
  const canvas = byId("custom-device-preview");
  const context = canvas.getContext("2d", { alpha: false });
  context.save();
  context.clearRect(0, 0, 466, 466);
  context.beginPath();
  context.arc(233, 233, 233, 0, Math.PI * 2);
  context.clip();
  context.fillStyle = "#000000";
  context.fillRect(0, 0, 466, 466);
  if (custom.background_image && state.customSourceImage) {
    context.globalAlpha = Math.max(0, Math.min(100, Number(custom.image_opacity))) / 100;
    drawImageFitted(context, state.customSourceImage, custom.image_fit);
    context.globalAlpha = 1;
  }
  context.textAlign = "center";
  context.textBaseline = "middle";
  if (custom.title_visible && custom.title) {
    context.fillStyle = custom.title_color || "#8E9B97";
    context.font = '20px "Microsoft YaHei", "Noto Sans SC", sans-serif';
    drawCenteredText(context, custom.title, 74, 300, 25, 1);
  }
  if (custom.value_visible && custom.value) {
    context.fillStyle = custom.value_color || custom.accent || "#F2C66D";
    context.font = '700 44px "Microsoft YaHei", "Noto Sans SC", sans-serif';
    drawCenteredText(context, custom.value, 195, 320, 52, 2);
  }
  if (custom.body_visible && custom.body) {
    context.fillStyle = custom.body_color || "#D6DFDC";
    context.font = '20px "Microsoft YaHei", "Noto Sans SC", sans-serif';
    drawCenteredText(context, custom.body, 285, 310, 29, 3);
  }
  if (custom.footer_visible && custom.footer) {
    context.fillStyle = custom.footer_color || "#8E9B97";
    context.font = '18px "Microsoft YaHei", "Noto Sans SC", sans-serif';
    drawCenteredText(context, custom.footer, 394, 280, 23, 1);
  }
  if (custom.ring_enabled) {
    const gradient = context.createLinearGradient(36, 36, 430, 430);
    gradient.addColorStop(0, custom.ring_start || "#F2C66D");
    gradient.addColorStop(1, custom.ring_end || custom.ring_start || "#F2C66D");
    context.strokeStyle = gradient;
    context.lineWidth = 26;
    context.beginPath();
    context.arc(233, 233, 220, 0, Math.PI * 2);
    context.stroke();
  }
  context.restore();
  byId("custom-ring-colors").hidden = !custom.ring_enabled;
  setText("custom-image-opacity-output", `${custom.image_opacity}%`);
  ["title", "value", "body", "footer"].forEach((field) => {
    const setting = document.querySelector(`[data-text-setting="${field}"]`);
    const collapsed = !custom[`${field}_visible`];
    setting?.classList.toggle("is-hidden", collapsed);
    const body = setting?.querySelector(".text-setting-body");
    if (body) body.hidden = collapsed;
  });
}

function loadCustomSource(url, revision) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => {
      if (state.customSourceUrl?.startsWith("blob:")) URL.revokeObjectURL(state.customSourceUrl);
      state.customSourceImage = image;
      state.customSourceRevision = revision;
      state.customSourceUrl = url;
      renderCustomPreview();
      resolve(image);
    };
    image.onerror = () => reject(new Error("背景图片读取失败"));
    image.src = url;
  });
}

function populateCustomForm(custom = {}) {
  byId("custom-enabled").checked = Boolean(custom.enabled);
  byId("custom-title").value = typeof custom.title === "string" ? custom.title : "我的页面";
  byId("custom-value").value = typeof custom.value === "string" ? custom.value : "你好，Dotii";
  byId("custom-body").value = typeof custom.body === "string" ? custom.body : "";
  byId("custom-footer").value = typeof custom.footer === "string" ? custom.footer : "";
  byId("custom-title-visible").checked = custom.title_visible !== false;
  byId("custom-value-visible").checked = custom.value_visible !== false;
  byId("custom-body-visible").checked = custom.body_visible !== false;
  byId("custom-footer-visible").checked = custom.footer_visible !== false;
  byId("custom-title-color").value = /^#[0-9A-F]{6}$/i.test(custom.title_color || "") ? custom.title_color : "#8E9B97";
  byId("custom-value-color").value = /^#[0-9A-F]{6}$/i.test(custom.value_color || "") ? custom.value_color : (/^#[0-9A-F]{6}$/i.test(custom.accent || "") ? custom.accent : "#F2C66D");
  byId("custom-body-color").value = /^#[0-9A-F]{6}$/i.test(custom.body_color || "") ? custom.body_color : "#D6DFDC";
  byId("custom-footer-color").value = /^#[0-9A-F]{6}$/i.test(custom.footer_color || "") ? custom.footer_color : "#8E9B97";
  byId("custom-background-image").checked = Boolean(custom.background_image);
  setUiSelectValue("custom-image-fit", custom.image_fit === "contain" ? "contain" : "cover");
  byId("custom-image-opacity").value = Number.isFinite(Number(custom.image_opacity)) ? custom.image_opacity : 70;
  byId("custom-ring-enabled").checked = custom.ring_enabled !== false;
  byId("custom-ring-start").value = /^#[0-9A-F]{6}$/i.test(custom.ring_start || "") ? custom.ring_start : "#F2C66D";
  byId("custom-ring-end").value = /^#[0-9A-F]{6}$/i.test(custom.ring_end || "") ? custom.ring_end : "#5DA9FF";
  setText("custom-image-state", custom.source_available ? "当前图片已保存，可以选择表情或重新上传。" : "选择图片后可调整位置和大小。");
  byId("custom-settings").hidden = !Boolean(custom.enabled);
  renderCustomPreview(customFormValue());
  if (custom.source_available && Number(custom.source_revision) !== state.customSourceRevision) {
    loadCustomSource(`/api/v1/admin/custom/source?v=${encodeURIComponent(custom.source_revision)}`, Number(custom.source_revision))
      .catch((error) => showToast(error.message));
  }
  state.customLoaded = true;
  state.customDirty = false;
  setText("custom-save-state", "已保存");
}

function renderCodexTask(task) {
  setText("task-status", STATUS_TEXT[task.status] || "未知状态");
  setText("message-count", formatNumber(task.user_message_count ?? task.message_count));
  setText("conversation-title", task.title || "暂无任务");
  setText("task-title", task.title || "暂无任务");
  setText("task-duration", formatTaskDuration(task.duration_seconds));
  setMarkdown("last-user-message", task.last_user_message || "暂无内容");
  const messages = Array.isArray(task.codex_messages) ? task.codex_messages.filter(Boolean) : [];
  const visibleMessages = task.conversation_mode === "progress" ? messages : messages.slice(-1);
  const conversationHeading = {
    progress: "Codex 中间消息",
    history: "Codex 最终回复",
    final: "Codex 最终回复",
  }[task.conversation_mode] || "Codex 消息";
  setText("codex-message-heading", conversationHeading);
  setMarkdownMessages(
    "codex-message-stream",
    visibleMessages.length ? visibleMessages : [
      task.conversation_mode === "progress" ? "暂无中间消息" : (task.last_assistant_message || "暂无最终回复"),
    ],
  );
}

function renderCodexTaskSwitcher(tasks) {
  const switcher = byId("codex-task-switcher");
  switcher.hidden = tasks.length <= 1;
  switcher.replaceChildren();
  if (tasks.length <= 1) return;
  tasks.forEach((task, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `任务${["一", "二", "三", "四", "五", "六"][index] || index + 1}`;
    button.title = task.title || button.textContent;
    button.classList.toggle("active", task.thread_id === state.selectedCodexTaskId);
    button.addEventListener("click", () => {
      state.selectedCodexTaskId = task.thread_id || `task-${index}`;
      renderCodexTask(task);
      renderCodexTaskSwitcher(tasks);
    });
    switcher.append(button);
  });
}

function renderOverview(overview) {
  state.overview = overview;
  const snapshot = overview.snapshot || {};
  const codex = snapshot.codex || {};
  const tasks = Array.isArray(codex.tasks) && codex.tasks.length ? codex.tasks : [codex.task || {}];
  let task = tasks.find((item) => item.thread_id && item.thread_id === state.selectedCodexTaskId);
  if (!task) {
    task = tasks[0] || {};
    state.selectedCodexTaskId = task.thread_id || "task-0";
  }
  const bridge = overview.bridge || {};
  const modules = Object.fromEntries((overview.modules || []).map((module) => [module.id, module]));
  const codexEnabled = modules.codex?.enabled !== false;
  const bambuEnabled = modules.bambu?.enabled !== false;
  const dotiiEnabled = modules.dotii?.enabled !== false;
  const weeklyAvailable = codex.weekly_available !== false;
  const weekly = weeklyAvailable ? Math.max(0, Math.min(100, Number(codex.weekly_remaining_percent) || 0)) : 0;
  const fiveHourAvailable = codex.five_hour_available === true;
  const fiveHour = fiveHourAvailable ? Math.max(0, Math.min(100, Number(codex.five_hour_remaining_percent) || 0)) : 0;

  setBridgeState(bridge);
  renderBluetooth(overview.bluetooth || {});
  renderFirmware(overview.firmware || {});
  byId("usage-ring").style.setProperty("--progress", `${weekly * 3.6}deg`);
  byId("five-hour-ring").style.setProperty("--progress", `${fiveHour * 3.6}deg`);
  setText("weekly-value", weeklyAvailable ? `${weekly}%` : "--");
  setText("five-hour-value", fiveHourAvailable ? `${fiveHour}%` : "--");
  setText("five-hour-plan", fiveHourAvailable ? "Plus 计划" : "仅 Plus");
  setText("five-hour-reset-date", fiveHourAvailable ? (codex.five_hour_reset_date || "--") : "--");
  setText("weekly-reset-inline-value", codex.reset_date || "--");
  setText("weekly-plan", codex.plan_type ? `${codex.plan_type} 计划` : "计划类型未知");
  setText("snapshot-source", snapshot.preview_data ? "示例内容" : bridge.collector_state === "online" ? "已连接" : "等待连接");
  setText("snapshot-time", formatTime(snapshot.generated_at_epoch));
  setText("weekly-tokens", formatNumber(codex.weekly_tokens, codex.weekly_tokens_available !== false));
  setText("weekly-token-period", codex.weekly_tokens_available !== false && codex.weekly_tokens_as_of
    ? `统计截至 ${codex.weekly_tokens_as_of}`
    : "等待用量统计");
  setText("reset-date", codex.reset_date || "--");
  renderCodexTask(task);
  renderCodexTaskSwitcher(tasks);
  if (!state.moduleSaving.codex) byId("codex-enabled").checked = codexEnabled;
  if (!state.moduleSaving.bambu) byId("bambu-enabled").checked = bambuEnabled;
  byId("codex-content").hidden = !codexEnabled;
  renderCodexCheck(state.codexCheck, codexEnabled, bridge);
  byId("bambu-content").hidden = !bambuEnabled;
  setText("codex-nav-state", codexEnabled ? (bridge.collector_state === "online" ? "在线" : bridge.collector_state === "error" ? "异常" : "连接中") : "已关闭");
  renderBambu(snapshot.bambu || {}, overview.bambu_config || {}, bambuEnabled);
  const incomingDotiiConfig = overview.dotii_config;
  if (!state.dotiiConfigDirty && !state.dotiiConfigSaving && incomingDotiiConfig
      && (!state.dotiiConfig || state.dotiiConfig.revision !== incomingDotiiConfig.revision)) {
    state.dotiiConfig = cloneDotiiConfig(incomingDotiiConfig);
    byId("dotii-config-save").disabled = true;
    setText("dotii-config-save-state", "配置已同步");
    renderDotiiConfigEditor();
  }
  renderDotii(snapshot.dotii || {}, dotiiEnabled);
  const displayConfig = overview.display_config || snapshot.display || {};
  const codexUi = displayConfig.codex_ui === "dual_limit" ? "dual_limit" : "classic";
  byId("codex-overview").classList.toggle("dual-limit", codexUi === "dual_limit");
  byId("usage-panel").classList.toggle("dual-limit", codexUi === "dual_limit");
  byId("five-hour-quota").hidden = codexUi !== "dual_limit";
  byId("weekly-reset-inline").hidden = codexUi !== "dual_limit";
  if (!state.codexUiSaving) {
    setUiSelectValue("codex-ui", codexUi);
    setText("codex-ui-state", codexUi === "dual_limit"
      ? "同时显示 5 小时和周剩余额度"
      : "仅显示周剩余额度");
  }
  if (!state.displayDirty && !state.displaySaving) {
    const angle = Math.max(80, Math.min(100, Number(displayConfig.docked_rotation_tenths || 840) / 10));
    const screenOffTimeout = DISPLAY_TIMEOUT_OPTIONS.includes(Number(displayConfig.screen_off_timeout_seconds))
      ? Number(displayConfig.screen_off_timeout_seconds) : 60;
    const sleepTimeout = DISPLAY_TIMEOUT_OPTIONS.includes(Number(displayConfig.sleep_timeout_seconds))
      ? Number(displayConfig.sleep_timeout_seconds) : 300;
    const chargingScreenOffTimeout = DISPLAY_TIMEOUT_OPTIONS.includes(Number(displayConfig.charging_screen_off_timeout_seconds))
      ? Number(displayConfig.charging_screen_off_timeout_seconds) : screenOffTimeout;
    const chargingSleepTimeout = DISPLAY_TIMEOUT_OPTIONS.includes(Number(displayConfig.charging_sleep_timeout_seconds))
      ? Number(displayConfig.charging_sleep_timeout_seconds) : sleepTimeout;
    byId("dock-angle").value = angle.toFixed(1);
    setUiSelectValue("screen-off-timeout", screenOffTimeout);
    setUiSelectValue("sleep-timeout", sleepTimeout);
    setUiSelectValue("charging-screen-off-timeout", chargingScreenOffTimeout);
    setUiSelectValue("charging-sleep-timeout", chargingSleepTimeout);
    setUiSelectValue("screen-off-page", ["none", "custom", "dotii"].includes(displayConfig.screen_off_page)
      ? displayConfig.screen_off_page : "none");
    refreshTimeoutConstraints();
    setText("dock-angle-output", `${angle.toFixed(1)}°`);
    setText("display-save-state", "已同步到 Dotii 管理中心");
  }
  const custom = snapshot.custom || {};
  setText("custom-nav-state", custom.enabled ? "已启用" : "未启用");
  if (!state.customLoaded || (!state.customDirty && !state.customSaving)) populateCustomForm(custom);
  byId("fatal-state").hidden = true;
}

function checkText(value) {
  return value ? "正常" : "异常";
}

function collectorText(collector = {}) {
  const labels = { online: "在线", starting: "连接中", error: "异常", disabled: "已关闭" };
  return labels[collector.collector_state] || "等待连接";
}

function renderCodexCheck(result, enabled = true, liveBridge = {}) {
  const button = byId("codex-check");
  button.disabled = !enabled || state.codexChecking;
  const badge = byId("codex-check-badge");
  if (state.codexChecking) {
    badge.dataset.state = "running";
    badge.textContent = "检测中";
    setText("codex-check-detail", "正在通过本机 App Server 读取 Codex 状态…");
    setText("codex-check-collector", collectorText(liveBridge));
    return;
  }
  if (!result) {
    badge.dataset.state = "idle";
    badge.textContent = "尚未检测";
    setText("codex-check-detail", enabled ? "点击“开始检测”验证当前 Codex 数据链路。" : "启用 Codex 后可运行只读检测。");
    setText("codex-check-collector", collectorText(liveBridge));
    return;
  }
  const cli = result.cli || {};
  const appServer = result.app_server || {};
  const account = result.account || {};
  const checks = result.checks || {};
  const collector = result.collector || liveBridge;
  badge.dataset.state = result.ok ? "ready" : "error";
  badge.textContent = result.ok ? "运行正常" : account.logged_in ? "部分异常" : "需要处理";
  setText("codex-check-cli", cli.available ? (cli.version && cli.version !== "--" ? `v${cli.version}` : "可用") : "不可用");
  setText("codex-check-source", cli.source || "--");
  setText("codex-check-server", checkText(appServer.available));
  setText("codex-check-collector", collectorText(collector));
  setText("codex-check-account", account.logged_in ? (account.email || "已登录") : "未登录");
  setText("codex-check-plan", account.plan_type ? account.plan_type.toUpperCase() : "--");
  setText("codex-check-usage", account.logged_in ? `额度${checkText(checks.rate_limits)} · 用量${checkText(checks.usage)}` : "--");
  setText("codex-check-threads", checks.threads ? `正常 · ${Number(result.thread_count) || 0} 个` : "异常");
  setText("codex-check-detail", result.detail || (result.ok ? "Codex 功能运行正常。" : "Codex 功能检测异常。"));
  setText("codex-check-time", result.checked_at_epoch ? `检测于 ${formatTime(result.checked_at_epoch)}` : "尚未检测");
}

async function checkCodex() {
  if (state.codexChecking) return;
  state.codexChecking = true;
  renderCodexCheck(state.codexCheck, true, state.overview?.bridge || {});
  try {
    const response = await fetch("/api/v1/admin/codex/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Codex 检测失败");
    state.codexCheck = result;
    showToast(result.ok ? "Codex 功能运行正常" : (result.detail || "Codex 检测发现异常"));
  } catch (error) {
    state.codexCheck = {
      ok: false,
      checked_at_epoch: Math.floor(Date.now() / 1000),
      detail: error.message || "Codex 检测失败",
      collector: state.overview?.bridge || {},
    };
    showToast(state.codexCheck.detail);
  } finally {
    state.codexChecking = false;
    renderCodexCheck(state.codexCheck, byId("codex-enabled").checked, state.overview?.bridge || {});
  }
}

async function saveBambu(event) {
  event.preventDefault();
  const payload = {
    name: byId("bambu-name").value,
    host: byId("bambu-host").value,
    serial: byId("bambu-serial").value,
    access_code: byId("bambu-access-code").value,
    camera_enabled: byId("bambu-camera-enabled").checked,
  };
  try {
    const response = await fetch("/api/v1/admin/bambu/config", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    byId("bambu-access-code").value = "";
    byId("bambu-access-code").placeholder = "已保存，留空表示不修改";
    showToast("Bambu 配置已保存，正在连接");
    await refreshOverview();
  } catch (error) { showToast(error.message); }
}

async function reconnectBambu() {
  try {
    const response = await fetch("/api/v1/admin/bambu/refresh", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
    if (!response.ok) throw new Error((await response.json()).error || `HTTP ${response.status}`);
    showToast("正在重新连接打印机");
  } catch (error) { showToast(error.message); }
}

async function commandBambu(action) {
  if (action === "stop" && !window.confirm("确定要停止当前打印吗？该操作无法撤销。")) return;
  try {
    const response = await fetch("/api/v1/admin/bambu/command", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({action})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    showToast(action === "stop" ? "停止指令已发送" : action === "resume" ? "继续指令已发送" : "暂停指令已发送");
  } catch (error) { showToast(error.message); }
}

async function refreshOverview({ announce = false } = {}) {
  if (state.busy) return;
  state.busy = true;
  byId("refresh-button").disabled = true;
  try {
    const response = await fetch("/api/v1/admin/overview", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderOverview(await response.json());
    if (announce) showToast("数据已刷新");
  } catch (error) {
    byId("fatal-state").hidden = false;
    const indicator = byId("bridge-state");
    indicator.dataset.state = "error";
    setText("bridge-state-label", "管理中心连接失败");
  } finally {
    state.busy = false;
    byId("refresh-button").disabled = false;
  }
}

async function updateAutoStart(enabled) {
  try {
    const response = await fetch("/api/v1/admin/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ auto_start: enabled }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "设置失败");
    byId("auto-start").checked = Boolean(result.auto_start);
    showToast(result.auto_start ? "已开启登录后自动启动" : "已关闭登录后自动启动");
  } catch (error) {
    byId("auto-start").checked = !enabled;
    showToast(error.message || "设置失败");
  }
}

async function saveDisplaySettings(event) {
  event.preventDefault();
  if (state.displaySaving) return;
  state.displaySaving = true;
  const angleTenths = Math.round(Number(byId("dock-angle").value) * 10);
  const screenOffTimeoutSeconds = Number(byId("screen-off-timeout").value);
  const sleepTimeoutSeconds = Number(byId("sleep-timeout").value);
  const chargingScreenOffTimeoutSeconds = Number(byId("charging-screen-off-timeout").value);
  const chargingSleepTimeoutSeconds = Number(byId("charging-sleep-timeout").value);
  const screenOffPage = byId("screen-off-page").value;
  try {
    const response = await fetch("/api/v1/admin/display", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        docked_rotation_tenths: angleTenths,
        screen_off_timeout_seconds: screenOffTimeoutSeconds,
        sleep_timeout_seconds: sleepTimeoutSeconds,
        charging_screen_off_timeout_seconds: chargingScreenOffTimeoutSeconds,
        charging_sleep_timeout_seconds: chargingSleepTimeoutSeconds,
        screen_off_page: screenOffPage,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "屏幕设置保存失败");
    state.displayDirty = false;
    setText("display-save-state", "已保存，Dotii 将自动同步");
    showToast("屏幕与睡眠设置已保存");
  } catch (error) {
    setText("display-save-state", "保存失败");
    showToast(error.message || "屏幕设置保存失败");
  } finally {
    state.displaySaving = false;
    await refreshOverview();
  }
}

async function updateModule(moduleId, enabled) {
  if (state.moduleSaving[moduleId]) return;
  state.moduleSaving[moduleId] = true;
  const checkbox = byId(`${moduleId}-enabled`);
  const content = byId(`${moduleId}-content`);
  content.hidden = !enabled;
  try {
    const response = await fetch("/api/v1/admin/modules", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: moduleId, enabled }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "设置失败");
    const moduleName = { codex: "Codex", bambu: "Bambu", dotii: "Dotii" }[moduleId] || moduleId;
    showToast(`${moduleName} 页面已${enabled ? "启用" : "关闭"}`);
  } catch (error) {
    checkbox.checked = !enabled;
    content.hidden = enabled;
    showToast(error.message || "设置失败");
  } finally {
    state.moduleSaving[moduleId] = false;
    await refreshOverview();
  }
}

async function saveCodexUi(codexUi) {
  if (state.codexUiSaving) return;
  state.codexUiSaving = true;
  const previous = state.overview?.display_config?.codex_ui === "dual_limit" ? "dual_limit" : "classic";
  setText("codex-ui-state", "正在保存");
  try {
    const response = await fetch("/api/v1/admin/display", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ codex_ui: codexUi }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Codex 页面样式保存失败");
    const saved = result.display_config?.codex_ui === "dual_limit" ? "dual_limit" : "classic";
    setUiSelectValue("codex-ui", saved);
    setText("codex-ui-state", saved === "dual_limit"
      ? "同时显示 5 小时和周剩余额度"
      : "仅显示周剩余额度");
    showToast(saved === "dual_limit" ? "已显示 5 小时和周剩余额度" : "已仅显示周剩余额度");
  } catch (error) {
    setUiSelectValue("codex-ui", previous);
    setText("codex-ui-state", "保存失败，已恢复原选择");
    showToast(error.message || "Codex 页面样式保存失败");
  } finally {
    state.codexUiSaving = false;
    await refreshOverview();
  }
}

async function refreshFirmware() {
  const button = byId("firmware-refresh");
  button.disabled = true;
  try {
    const response = await fetch("/api/v1/admin/firmware/refresh", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "扫描失败");
    showToast("已重新扫描 Dotii 和固件包");
  } catch (error) {
    showToast(error.message || "扫描失败");
  } finally {
    await refreshOverview();
  }
}

async function bluetoothAction(path, payload = {}) {
  const response = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}

async function installBluetooth() {
  try {
    await bluetoothAction("/api/v1/admin/bluetooth/install");
    showToast("正在准备 Windows 蓝牙连接组件");
    await refreshOverview();
  } catch (error) { showToast(error.message || "蓝牙组件安装失败"); }
}

async function scanBluetooth() {
  try {
    await bluetoothAction("/api/v1/admin/bluetooth/scan");
    showToast("正在扫描附近的 Dotii");
    await refreshOverview();
  } catch (error) { showToast(error.message || "蓝牙扫描失败"); }
}

async function configureBluetooth() {
  const address = byId("bluetooth-device").value;
  const ssid = byId("bluetooth-ssid").value.trim();
  const password = byId("bluetooth-password").value;
  if (!address || !ssid) return showToast("请选择 Dotii 并填写 Wi-Fi 名称");
  try {
    await bluetoothAction("/api/v1/admin/bluetooth/configure", { address, ssid, password });
    byId("bluetooth-password").value = "";
    showToast("正在通过蓝牙配置 Dotii");
    await refreshOverview();
  } catch (error) { showToast(error.message || "蓝牙配置失败"); }
}

async function flashFirmware() {
  const port = byId("firmware-port").value;
  if (!port) return;
  const version = state.overview?.firmware?.package?.version || "当前";
  if (!window.confirm(`将向 ${port} 写入 Dotii v${version}。烧录期间请勿拔线，是否继续？`)) return;
  const button = byId("firmware-flash");
  button.disabled = true;
  try {
    const response = await fetch("/api/v1/admin/firmware/flash", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ port, confirmation: "FLASH_DOTII" }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "无法开始烧录");
    showToast("已开始烧录 Dotii");
    await refreshOverview();
  } catch (error) {
    showToast(error.message || "烧录启动失败");
    await refreshOverview();
  }
}

function canvasToRgb565() {
  const canvas = byId("custom-device-preview");
  const pixels = canvas.getContext("2d", { alpha: false }).getImageData(0, 0, 466, 466).data;
  const output = new Uint8Array(466 * 466 * 2);
  for (let source = 0, target = 0; source < pixels.length; source += 4, target += 2) {
    const value = ((pixels[source] >> 3) << 11)
      | ((pixels[source + 1] >> 2) << 5)
      | (pixels[source + 2] >> 3);
    output[target] = value & 0xFF;
    output[target + 1] = value >> 8;
  }
  return output;
}

async function stageCustomRender(custom) {
  if (custom.background_image && !state.customSourceRevision) {
    throw new Error("请先上传背景图片，或关闭“显示背景图片”");
  }
  if (custom.background_image && !state.customSourceImage && state.customSourceRevision) {
    await loadCustomSource(
      `/api/v1/admin/custom/source?v=${encodeURIComponent(state.customSourceRevision)}`,
      state.customSourceRevision,
    );
  }
  if (document.fonts?.ready) await document.fonts.ready;
  renderCustomPreview(custom);
  const response = await fetch("/api/v1/admin/custom/render", {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: canvasToRgb565(),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "画面生成失败");
  return result.render_token;
}

function clampCropOffset() {
  const { image, zoom } = state.crop;
  if (!image) return;
  const baseScale = Math.max(720 / image.naturalWidth, 720 / image.naturalHeight);
  const width = image.naturalWidth * baseScale * zoom;
  const height = image.naturalHeight * baseScale * zoom;
  state.crop.offsetX = Math.max(-(width - 720) / 2, Math.min((width - 720) / 2, state.crop.offsetX));
  state.crop.offsetY = Math.max(-(height - 720) / 2, Math.min((height - 720) / 2, state.crop.offsetY));
}

function cropPlacement(size = 720) {
  const { image, zoom, offsetX, offsetY } = state.crop;
  const ratio = size / 720;
  const scale = Math.max(720 / image.naturalWidth, 720 / image.naturalHeight) * zoom * ratio;
  const width = image.naturalWidth * scale;
  const height = image.naturalHeight * scale;
  return {
    width,
    height,
    x: (size - width) / 2 + offsetX * ratio,
    y: (size - height) / 2 + offsetY * ratio,
  };
}

function drawCropPreview() {
  if (!state.crop.image) return;
  clampCropOffset();
  const canvas = byId("custom-crop-canvas");
  const context = canvas.getContext("2d", { alpha: false });
  const placement = cropPlacement();
  context.clearRect(0, 0, 720, 720);
  context.fillStyle = "#000000";
  context.fillRect(0, 0, 720, 720);
  context.save();
  context.beginPath();
  context.arc(360, 360, 356, 0, Math.PI * 2);
  context.clip();
  context.drawImage(state.crop.image, placement.x, placement.y, placement.width, placement.height);
  context.restore();
  context.strokeStyle = "rgba(241, 248, 245, .72)";
  context.lineWidth = 4;
  context.beginPath();
  context.arc(360, 360, 354, 0, Math.PI * 2);
  context.stroke();
  setText("custom-crop-zoom-output", `${Math.round(state.crop.zoom * 100)}%`);
}

function closeCropDialog(restoreStatus = true) {
  const dialog = byId("custom-crop-dialog");
  if (dialog.open) dialog.close();
  if (state.crop.objectUrl) URL.revokeObjectURL(state.crop.objectUrl);
  state.crop.image = null;
  state.crop.file = null;
  state.crop.objectUrl = null;
  state.crop.dragging = false;
  state.crop.pointerId = null;
  byId("custom-image-file").value = "";
  if (restoreStatus) {
    const sourceAvailable = Boolean(state.overview?.snapshot?.custom?.source_available);
    setText("custom-image-state", sourceAvailable
      ? "当前图片已保存，可以选择表情或重新上传。"
      : "选择图片后可调整位置和大小。");
  }
}

function openCropDialog(file) {
  if (!file) return;
  if (!["image/png", "image/jpeg"].includes(file.type)) {
    showToast("请选择 PNG 或 JPEG 图片");
    byId("custom-image-file").value = "";
    return;
  }
  const objectUrl = URL.createObjectURL(file);
  const image = new Image();
  setText("custom-image-state", "正在读取图片");
  image.onload = () => {
    state.crop.image = image;
    state.crop.file = file;
    state.crop.objectUrl = objectUrl;
    state.crop.zoom = 1;
    state.crop.offsetX = 0;
    state.crop.offsetY = 0;
    byId("custom-crop-zoom").value = "100";
    byId("custom-crop-dialog").showModal();
    drawCropPreview();
    setText("custom-image-state", "图片已读取，请完成裁切。");
  };
  image.onerror = () => {
    URL.revokeObjectURL(objectUrl);
    byId("custom-image-file").value = "";
    setText("custom-image-state", "图片读取失败，请重新选择。");
    showToast("图片读取失败，请重新选择");
  };
  image.src = objectUrl;
}

async function uploadCustomSource(file, successMessage = "图片已裁切并上传") {
  setText("custom-image-state", "正在保存图片");
  try {
    const response = await fetch("/api/v1/admin/custom/source", {
      method: "POST",
      headers: { "Content-Type": file.type },
      body: file,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "图片上传失败");
    await loadCustomSource(
      `/api/v1/admin/custom/source?v=${encodeURIComponent(result.source_revision)}`,
      Number(result.source_revision),
    );
    byId("custom-background-image").checked = true;
    state.customDirty = true;
    renderCustomPreview();
    setText("custom-image-state", `${successMessage}。点击保存即可显示在 Dotii 上。`);
    setText("custom-save-state", "图片已更新，等待保存并同步");
    showToast(successMessage);
    return true;
  } catch (error) {
    setText("custom-image-state", "图片处理失败，请重新选择");
    showToast(error.message || "图片上传失败");
    return false;
  }
}

async function applyCrop() {
  if (!state.crop.image) return;
  const button = byId("apply-crop");
  button.disabled = true;
  button.textContent = "正在应用…";
  try {
    const output = document.createElement("canvas");
    output.width = 932;
    output.height = 932;
    const context = output.getContext("2d", { alpha: false });
    const placement = cropPlacement(932);
    context.fillStyle = "#000000";
    context.fillRect(0, 0, 932, 932);
    context.drawImage(state.crop.image, placement.x, placement.y, placement.width, placement.height);
    const blob = await new Promise((resolve) => output.toBlob(resolve, "image/jpeg", 0.88));
    if (!blob) throw new Error("无法生成裁切图片");
    const uploaded = await uploadCustomSource(blob, "图片已准备好");
    if (uploaded) closeCropDialog(false);
  } catch (error) {
    showToast(error.message || "裁切失败");
  } finally {
    button.disabled = false;
    button.textContent = "应用裁切";
  }
}

async function selectBuiltInExpression(button) {
  document.querySelectorAll(".expression-option").forEach((item) => { item.disabled = true; });
  try {
    const response = await fetch(button.dataset.expression, { cache: "force-cache" });
    if (!response.ok) throw new Error("内置表情读取失败");
    const blob = await response.blob();
    const uploaded = await uploadCustomSource(blob, `已选择“${button.dataset.expressionName}”`);
    if (!uploaded) return;
    setUiSelectValue("custom-image-fit", "cover");
    byId("custom-image-opacity").value = "100";
    byId("custom-ring-enabled").checked = false;
    ["title", "value", "body", "footer"].forEach((field) => { byId(`custom-${field}-visible`).checked = false; });
    state.customDirty = true;
    renderCustomPreview();
    document.querySelectorAll(".expression-option").forEach((item) => item.classList.toggle("selected", item === button));
  } catch (error) {
    showToast(error.message || "内置表情读取失败");
  } finally {
    document.querySelectorAll(".expression-option").forEach((item) => { item.disabled = false; });
  }
}

async function persistCustom({ announce = true, previousEnabled = null } = {}) {
  if (state.customSaving) return;
  const custom = customFormValue();
  state.customSaving = true;
  const button = byId("save-custom");
  button.disabled = true;
  button.textContent = "正在保存…";
    setText("custom-save-state", "正在保存");
  try {
    const renderToken = await stageCustomRender(custom);
    const response = await fetch("/api/v1/admin/custom", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...custom, render_token: renderToken }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "保存失败");
    state.customDirty = false;
    populateCustomForm(result.custom || custom);
    setText("custom-nav-state", custom.enabled ? "已启用" : "未启用");
    if (announce) showToast(custom.enabled ? "自定义页面已启用并同步" : "自定义页面已关闭，设备入口已隐藏");
  } catch (error) {
    if (previousEnabled !== null) {
      byId("custom-enabled").checked = previousEnabled;
      byId("custom-settings").hidden = !previousEnabled;
      renderCustomPreview();
    }
    setText("custom-save-state", "保存失败，请检查内容后重试");
    showToast(error.message || "保存失败");
  } finally {
    state.customSaving = false;
    button.disabled = false;
    button.textContent = "保存并同步";
  }
}

function saveCustom(event) {
  event.preventDefault();
  persistCustom();
}

function toggleCustom(event) {
  if (state.customSaving) {
    event.target.checked = !event.target.checked;
    return;
  }
  const enabled = event.target.checked;
  byId("custom-settings").hidden = !enabled;
  renderCustomPreview();
  persistCustom({ previousEnabled: !enabled });
}

document.querySelectorAll(".module-tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".module-tab").forEach((tab) => {
      const active = tab === button;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll(".panel").forEach((panel) => {
      const active = panel.id === `panel-${button.dataset.panel}`;
      panel.hidden = !active;
      panel.classList.toggle("active", active);
    });
  });
});

document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    const value = byId(button.dataset.copy).textContent;
    try {
      await navigator.clipboard.writeText(value);
      showToast("已复制");
    } catch (error) {
      showToast("复制失败，请手动选择文本");
    }
  });
});

byId("refresh-button").addEventListener("click", () => refreshOverview({ announce: true }));
byId("auto-start").addEventListener("change", (event) => updateAutoStart(event.target.checked));
byId("display-form").addEventListener("submit", saveDisplaySettings);
byId("dock-angle").addEventListener("input", (event) => {
  const angle = Number(event.target.value);
  state.displayDirty = true;
  setText("dock-angle-output", `${angle.toFixed(1)}°`);
  setText("display-save-state", "有尚未保存的修改");
});
DISPLAY_TIMEOUT_PAIRS.forEach(({ screenOff, sleep }) => {
  [screenOff, sleep].forEach((id) => byId(id).addEventListener("change", () => {
    normalizeTimeoutPair(id, screenOff, sleep);
    state.displayDirty = true;
    setText("display-save-state", "有尚未保存的修改");
  }));
});
byId("screen-off-page").addEventListener("change", () => {
  state.displayDirty = true;
  setText("display-save-state", "有尚未保存的修改");
});
byId("codex-enabled").addEventListener("change", (event) => updateModule("codex", event.target.checked));
byId("codex-ui").addEventListener("change", (event) => saveCodexUi(event.target.value));
byId("codex-check").addEventListener("click", checkCodex);
byId("bambu-enabled").addEventListener("change", (event) => updateModule("bambu", event.target.checked));
byId("dotii-enabled").addEventListener("change", (event) => updateModule("dotii", event.target.checked));
byId("dotii-config-form").addEventListener("submit", saveDotiiConfig);
byId("dotii-follow-live").addEventListener("click", () => {
  state.dotiiFollowingLive = true;
  showDotiiExpression(state.dotiiLiveExpression, { live: true });
  setText("dotii-stage-reason", state.overview?.snapshot?.dotii?.reason || "当前处于空闲状态");
});
byId("bambu-form").addEventListener("submit", saveBambu);
byId("bambu-reconnect").addEventListener("click", reconnectBambu);
byId("bambu-pause").addEventListener("click", (event) => commandBambu(event.currentTarget.dataset.action || "pause"));
byId("bambu-stop").addEventListener("click", () => commandBambu("stop"));
function openBambuSettings() {
  byId("bambu-form").scrollIntoView({ block: "start" });
  byId("bambu-host").focus({ preventScroll: true });
}
byId("bambu-empty-settings").addEventListener("click", openBambuSettings);
byId("bluetooth-install").addEventListener("click", installBluetooth);
byId("bluetooth-scan").addEventListener("click", scanBluetooth);
byId("bluetooth-configure").addEventListener("click", configureBluetooth);
byId("bluetooth-ssid").addEventListener("input", () => renderBluetooth(state.overview?.bluetooth || {}));
byId("firmware-refresh").addEventListener("click", refreshFirmware);
byId("firmware-flash").addEventListener("click", flashFirmware);
byId("custom-form").addEventListener("submit", saveCustom);
byId("custom-enabled").addEventListener("change", toggleCustom);
byId("custom-image-file").addEventListener("change", (event) => openCropDialog(event.target.files?.[0]));
byId("custom-crop-zoom").addEventListener("input", (event) => {
  state.crop.zoom = Number(event.target.value) / 100;
  drawCropPreview();
});
byId("cancel-crop").addEventListener("click", closeCropDialog);
byId("cancel-crop-top").addEventListener("click", closeCropDialog);
byId("apply-crop").addEventListener("click", applyCrop);
byId("custom-crop-dialog").addEventListener("cancel", (event) => {
  event.preventDefault();
  closeCropDialog();
});
const cropCanvas = byId("custom-crop-canvas");
cropCanvas.addEventListener("pointerdown", (event) => {
  state.crop.dragging = true;
  state.crop.pointerId = event.pointerId;
  state.crop.lastX = event.clientX;
  state.crop.lastY = event.clientY;
  cropCanvas.setPointerCapture(event.pointerId);
});
cropCanvas.addEventListener("pointermove", (event) => {
  if (!state.crop.dragging || state.crop.pointerId !== event.pointerId) return;
  const scale = 720 / cropCanvas.getBoundingClientRect().width;
  state.crop.offsetX += (event.clientX - state.crop.lastX) * scale;
  state.crop.offsetY += (event.clientY - state.crop.lastY) * scale;
  state.crop.lastX = event.clientX;
  state.crop.lastY = event.clientY;
  drawCropPreview();
});
function stopCropDrag(event) {
  if (state.crop.pointerId !== event.pointerId) return;
  state.crop.dragging = false;
  state.crop.pointerId = null;
}
cropCanvas.addEventListener("pointerup", stopCropDrag);
cropCanvas.addEventListener("pointercancel", stopCropDrag);
document.querySelectorAll(".expression-option").forEach((button) => {
  button.addEventListener("click", () => selectBuiltInExpression(button));
});
byId("custom-form").addEventListener("input", () => {
  state.customDirty = true;
  renderCustomPreview();
  setText("custom-save-state", "有尚未保存的修改");
});

setupUiSelects();
loadDotiiExpressions();
refreshOverview();
window.setInterval(refreshOverview, 3000);
