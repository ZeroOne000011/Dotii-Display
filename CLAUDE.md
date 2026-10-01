# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Dotii 桌面交互屏：ESP32-S3 圆形 AMOLED 固件 + 电脑端"Dotii 管理中心"（Python 后台 + 本机管理网页）。两部分通过带设备令牌的局域网 HTTP 协议（`schema_version = 1`）通信；蓝牙仅用于发现、配网与恢复。管理中心默认监听 `127.0.0.1:8787`。

面向用户的名称统一为"Dotii 管理中心"；`bridge`、`bridge_url`、`bridge_token` 只作为内部目录名和协议字段。

## 常用命令

电脑端（Python 3.11+，macOS/Linux 用 `/`、Windows 用 `\`）：

```bash
python -m unittest discover -s bridge/tests -v          # 全部测试
python -m unittest discover -s bridge/tests -k test_bambu_client   # 单个测试文件
python -m compileall -q bridge                          # Python 编译检查
node --check bridge/web/app.js                          # 管理网页 JS 语法检查
python bridge/bridge_app.py --open-dashboard            # 从源码启动管理中心
```

- 测试需要在项目根目录预先存在 `.codx/` 目录（测试的临时目录）：`mkdir -p .codx`
- 测试依赖 `bleak`、`esptool`、`Pillow`（版本见 `packaging/requirements-build.txt`）；本机 Python 缺依赖时蓝牙相关测试会失败，属环境问题而非代码问题
- 测试不得启动外部服务、连接真实设备或复用真实令牌/访问码；必须使用隔离运行目录

固件（ESP-IDF 6.0.2，目标固定 `esp32s3`，版本由根 `CMakeLists.txt` 的 `PROJECT_VER` 定义；本机安装于 `~/esp/esp-idf`，先激活环境）：

```bash
. ~/esp/esp-idf/export.sh
idf.py build
```

发布构建：

```bash
# Windows 便携包（PyInstaller + Inno Setup）
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\build_windows.ps1 -Clean
# macOS DMG（所有下载与中间产物进入 .codx/）
packaging/macos/prepare_python.sh
packaging/macos/prepare_tools.sh
packaging/macos/build_macos.sh --tools-dir .codx/macos-tools --dmg
```

## 架构

三个可交付部分共用一套业务逻辑与网页，平台差异只保留在平台适配器、宿主与打包目录：

- **固件 `main/`**（ESP-IDF C）：`main.c` 入口与页面注册/主循环；`app_state.*` 解析 schema v1 快照（缺失/越界字段必须有安全默认值）；`state_ui.*` 圆屏页面、局部刷新与交互；`connectivity.*` Wi-Fi 与 HTTP 快照/自定义帧同步；`device_config.*` NVS 配置与重置配网（只清 `dotii_cfg` 命名空间）；`ble_bridge.*` GATT 配网协议；`board_input.*` 触摸与按键。
- **管理中心 `bridge/`**（Python）：`codex_bridge.py` 是核心 Web/API 服务（`BridgeHandler` 定义所有路由，扩展接口前先查它）；`codex_app_server.py` Codex App Server 采集；`bambu_client.py` Bambu MQTT/TLS 与相机；`bluetooth_bridge.py` 跨平台 BLE 配网；`firmware_flasher.py` 受限烧录（只认 USB VID/PID `303A:1001`）；`runtime_paths.py` 统一资源定位（源码与 PyInstaller 双环境）。
- **平台层**：`bridge/platforms/`（`base.py` 接口 + `windows.py`/`macos.py` 适配器，运行目录、启动项、串口、外部工具解析都走这里）；`bridge/bridge_app.py` Windows 托盘宿主；`macos/DotiiManagementCenter/` AppKit 菜单栏宿主（Xcode 工程）。
- **`bridge/web/`**：本机管理网页，源码入口与发布包共用同一套，不为开发版/发布版维护两套逻辑。
- **`firmware/`**：共用最小烧录包（`flasher_args.json` + 镜像），构建后由打包脚本从 `build/` 原子更新。
- **`components/waveshare__esp32_s3_touch_amoled_1_75/`**：本地 BSP，含对上游的定制修正（CO5300 方向、原生 gap、软件合成旋转、RGB565 脏矩形偶数对齐）；更新上游 BSP 时必须逐项重新核对。

## 关键约束

- **平台边界**：Windows/macOS 专用逻辑（注册表、AppKit、串口命名、配对恢复等）只进对应适配器或宿主，不得复制共用业务代码，恢复逻辑不得跨平台复用。
- **路径**：所有资源定位必须经 `runtime_paths.py`，禁止新增本机绝对路径、用户名、盘符、COM 口、局域网 IP、令牌。
- **模块默认状态**：Codex 与 Bambu 首次运行必须关闭，用户启用前不得检查、下载、安装、登录或连接外部服务；自定义页面与 Dotii 表情默认开启。
- **新管理接口**：限制回环地址、校验 Content-Type 与 body 大小、返回稳定 JSON 错误、不输出秘密，并为成功和失败路径加测试；设备接口（`/api/v1/snapshot`、`/api/v1/custom/frame`）还必须验证令牌。
- **schema 兼容**：新字段做成可选、不改变旧字段含义；不兼容变更必须提升 `schema_version`；所有字符串/数组/图片/HTTP body 须有长度上限；缺失数据显示 `--`，不做推算。
- **不手工编辑的区域**：`main/generated/`（字体图标生成结果）、`managed_components/`（锁定依赖的本地副本，不要修改第三方组件绕过应用层问题）。
- **临时文件**：统一放项目根目录 `.codx/`，任务完成后清理；不提交 `__pycache__`、日志、临时脚本。
- **自定义帧规格**：466×466、RGB565、大端、固定 434312 字节，固件按 revision 更新，失败保留上一有效帧。

## 提交前检查

开发指南要求的最小集合：上面四条命令（unittest、compileall、node --check、idf.py build）全部通过。发布前另有平台验收清单，见 `开发指南.md` 第 14-15 节与 `docs/development/windows.md`、`docs/development/macos.md`。

## 文档分工

面向用户的说明只在根 `README.md`；通用开发规范在 `开发指南.md`（协议、模块扩展流程、圆屏交互、烧录）；平台细节在 `docs/development/windows.md` 与 `docs/development/macos.md`。修改对应领域时先读对应文档。
