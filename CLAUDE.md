# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Dotii 桌面交互屏：ESP32-S3 圆形 AMOLED 固件 + 电脑端"Dotii 管理中心"（Python 后台 + 本机管理网页）。设备通过两种链路获取状态快照：**Wi-Fi 局域网 HTTP 拉取**（默认，完整功能）或 **BLE 推送**（精简模式，免网络环境）；蓝牙同时承担配网/绑定。管理中心默认监听 `127.0.0.1:8787`。

面向用户的名称统一为"Dotii 管理中心"；`bridge`、`bridge_url`、`bridge_token` 只作为内部目录名和协议字段。

## 常用命令

电脑端（Python 3.11+，用 `.venv`）：

```bash
.venv/bin/python -m unittest discover -s bridge/tests -v            # 全部测试
.venv/bin/python -m unittest discover -s bridge/tests -k test_zai_client   # 单个测试文件
.venv/bin/python -m compileall -q bridge                            # Python 编译检查
node --check bridge/web/app.js                                      # 管理网页 JS 语法检查
nohup .venv/bin/python -B bridge/codex_bridge.py --app-server > /tmp/dotii-bridge.log 2>&1 & disown   # 常驻启动（日志 /tmp/dotii-bridge.log）
```

- 测试需要在项目根目录预先存在 `.codx/` 目录（`mkdir -p .codx`）
- 测试依赖 `bleak`、`esptool`、`Pillow`（`packaging/requirements-build.txt`）；Claude Code hooks 相关测试依赖系统 `codex` CLI（`/api/v1/admin/claudecode/event` 相关逻辑）
- 测试不得启动外部服务、连接真实设备或复用真实令牌/访问码；必须使用隔离运行目录

固件（ESP-IDF 6.0.2，目标固定 `esp32s3`，版本由根 `CMakeLists.txt` 的 `PROJECT_VER` 定义；本机安装于 `~/esp/esp-idf`，先激活环境）：

```bash
. ~/esp/esp-idf/export.sh
idf.py build
idf.py -p /dev/cu.usbmodem101 flash      # 串口 VID/PID 303A:1001（唯一 Dotii 设备）
```

- 设备深睡后 USB 挂起，软件复位烧录会失败（`No serial data received`）：按住 **BOOT** 点按 **RST** 进下载模式
- 固件新增 GATT 特征后 macOS 可能仍用旧服务缓存（报 `Characteristic not found`）：系统蓝牙忽略设备后重新配对

## 架构

**数据流**：各数据源模块（管理中心内）→ 快照（schema v1 JSON，同一份）→ 设备。两条链路传同一 JSON：

- Wi-Fi：设备每 5 秒 `GET /api/v1/snapshot`（`X-Bridge-Token` 头认证）
- BLE：管理中心常驻 `BleLinkService`（`bluetooth_bridge.py`）主动推送——`SNAPSHOT` 特征（UUID 尾 05）加密写分包，`SYNC`（尾 06）notify 重发请求；协议纯函数在 `bridge/ble_link.py`
- `link_mode`（wifi/ble）的事实源是**管理页配网的连接方式选择**（绑定载荷直接写入设备 NVS）；设备设置页的切换是无电脑时的备用入口，两者写同一字段，重置配网不清除
- 蓝牙模式下设备无 SNTP，用快照 `generated_at_epoch` 校准时钟；管理中心每次响应必须刷新该字段（同时是设备 stale 判定基准）

**bridge/（Python）**——四个数据源模块 + 平台层：

- `codex_bridge.py`：核心 Web/API 服务。`BridgeHandler` 定义所有路由（if/elif 链）；`BridgeServer.snapshot()` 组装快照并注入动态节点（bambu/zai/claudecode）；`dotii_state()` 推导表情（Codex 模块关闭时忽略 state.json 种子预览数据）
- `zai_client.py`：Z.ai 用量轮询。`CREDIT_LIMIT` 双窗口按重置时间排序（最早=5 小时窗）、毫秒时间戳、`percentage` 是已用需转剩余
- `claudecode_client.py`：官方 hooks 事件聚合（多会话 LRU）+ hooks 安装/清理（官方嵌套 matcher 结构、写前备份、裸 curl 命令跨平台、离线 `|| exit 0` 静默）
- `bluetooth_bridge.py`：配网（`_configuration_packets` 分包）+ `BleLinkService` 常驻推送（断线退避 2→60s，`resume` 立即打断退避；配网期间 `pause(seconds)` 让出连接；管理页暂停是 `pause(None)` 用户语义——不自动恢复）
- `ble_link.py`：BLE 分包协议纯函数（头 `[0x01][len u16][crc32][revision u32]`/块/尾、`SnapshotAssembler`、`PushThrottle`）
- `platforms/`：平台适配器（运行目录、启动项、串口、外部工具），平台差异不进共用代码
- `web/`：管理网页，源码与发布包共用一套

**main/（固件 C，ESP-IDF + LVGL + cJSON）**：

- `app_state.h`：单一扁平 `codex_snapshot_t`（所有模块字段平铺，`*_enabled`/数据字段命名如 `zai_*`、`claudecode_*`）
- `connectivity.c`：`parse_snapshot`（cJSON，全字段安全默认）+ `connectivity_ingest_snapshot`（BLE 入口，与 Wi-Fi 轮询共用解析，`s_parse_lock` 互斥）+ 时钟校准
- `ble_bridge.c`：GATT 服务（UUID 基 `7b4e...`，尾字节 01-06）+ 快照重组状态机（PSRAM 缓冲）+ `apply_configuration`（mode:ble 载荷免 Wi-Fi 凭证并写 link_mode）
- `state_ui.c`：LVGL 页面。`PAGE_COUNT=6`（codex→bambu→zai→claudecode→custom→dotii）；**主页面层左滑/右滑循环翻页**（`goto_page`，转场方向跟随滑动）、**点按进详情**（`detail_clicked`，350ms 手势窗口防滑动误触发点击）；控制中心（下滑）有亮度、快捷按钮（`layout_quick_buttons` 自适应：≤4 个单行 78px、更多则两行 3×N 70px）、链路模式图标行、电量；设置页有"连接方式"长按切换卡片
- `device_config.c`：NVS `dotii_cfg`（含 `link_mode`）

## 关键约束

- **BLE 协议**：revision 必须是单调递增序列号（管理中心时间戳起步）——内容 CRC 无单调性，作序号会被设备当旧包丢弃；CRC 只做完整性校验。推送节流 ≥2s（claudecode 事件高频）
- 模块默认关闭；启用前零外部请求。API key 只存本机运行目录、回读不回显、不进快照/日志
- 新管理接口：回环限制 + Content-Type/body 校验 + 稳定 JSON 错误 + 成败路径测试；设备接口验证令牌
- schema_version 保持 1，新快照节点全可选；旧管理中心 + 新固件时新页面隐藏（modules 缺省 false）
- 不手工编辑：`main/generated/`（字体图标生成结果——但可用脚本重新生成追加，如官方资源转 A8 位图）、`managed_components/`（Component Manager 按 `dependencies.lock` 恢复，勿改第三方绕过应用层问题）
- 固件中文字本前查字形覆盖（`ui_font_fixed_20` 与 `ui_font_detail_20` 的字符集不同）
- 临时文件统一放 `.codx/`，任务完成后清理
- 电脑串口/蓝牙互斥：设备同一时刻只接受一个中心连接

## 提交前检查

```bash
.venv/bin/python -m unittest discover -s bridge/tests -v
.venv/bin/python -m compileall -q bridge
node --check bridge/web/app.js
idf.py build
```

发布前另有平台验收清单（`开发指南.md` §14-15 与 `docs/development/*.md`）。

## 文档分工

用户说明只在根 `README.md`；通用开发规范在 `开发指南.md`（协议、模块流程 §8a/8b/10b 各数据源边界、圆屏交互 §12、BLE 通道）；平台细节在 `docs/development/windows.md` 与 `macos.md`。改对应领域前先读对应章节。
