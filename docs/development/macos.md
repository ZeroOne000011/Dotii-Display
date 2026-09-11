# Dotii macOS 开发指南

本文记录 macOS 管理中心的平台实现、开发环境、测试和发布流程。固件、协议、模块扩展和通用安全要求见 [通用开发指南](../../开发指南.md)。

## 支持范围与平台边界

- 当前支持 Apple Silicon arm64、macOS 13 及以上。
- 运行数据位于 `~/Library/Application Support/Dotii`。
- `.app/Contents/Resources` 保存只读资源、Python 后台和应用私有工具。
- `.app/Contents/MacOS` 保存原生主程序。
- Finder 启动不得依赖交互式 shell 的 `PATH`，也不得静默安装 Homebrew、Python、Node.js、Codex CLI、FFmpeg 或驱动。
- macOS 专用 AppKit、CoreBluetooth、`/dev/cu.*`、`SMAppService`、签名和 DMG 逻辑必须保留在 macOS 平台边界内。

## 原生宿主与运行目录

`macos/DotiiManagementCenter/` 包含 AppKit 菜单栏宿主和 Xcode 工程。宿主负责单实例、后台启动、健康检查、有限重试、日志入口、登录启动和退出清理。

原生宿主使用 `SMAppService.mainApp` 管理登录启动，不允许 Python 后台写入 `~/Library/LaunchAgents`。真实注销/登录或重启后的自动拉起必须单独验证；只完成接口调用不能记录为完整验收。

应用私有工具优先从 `Contents/Resources` 解析，再考虑 `/opt/homebrew/bin`、`/usr/local/bin` 等安全回退。依赖缺失时只报告不可用，不自动安装。

## CoreBluetooth 与串口

macOS 串口层只枚举 `/dev/cu.*`，不使用 `/dev/tty.*`。只有 USB VID/PID `303A:1001` 的设备可以进入 Dotii 烧录流程。

BLE 使用 CoreBluetooth/Bleak。本机稳定 UUID 不是 Windows 风格的 MAC 地址，只能保存在权限受限的本地运行文件中，不得显示给用户或写入公开日志。首次访问加密 GATT 特征时由 macOS 触发系统配对，不调用显式 `pair()` 或 Windows `unpair()`。

蓝牙权限未决定、已允许、已拒绝、蓝牙关闭、硬件不可用和连接超时需要显示为不同状态。`Info.plist` 必须包含蓝牙和本地网络用途说明。设备端已清除绑定但 macOS 保留旧记录时，应引导用户在“系统设置 > 蓝牙”中忽略 Dotii 后重新扫描。

## 准备与构建环境

Apple Silicon 的可重复构建使用项目脚本，所有下载、缓存和候选产物都进入源码根目录 `.codx/`：

```bash
packaging/macos/prepare_python.sh
packaging/macos/prepare_tools.sh
packaging/macos/build_macos.sh --tools-dir .codx/macos-tools --dmg
```

- `prepare_python.sh` 准备固定的 arm64 Python 和构建依赖。
- `prepare_tools.sh` 准备固定版 Node.js、Codex CLI，以及使用 LGPL 配置构建的 FFmpeg。
- `build_macos.sh` 组装原生宿主、Python 后台、固件、网页、应用私有工具和许可证。

从源码运行或测试时也必须使用隔离运行目录和非生产端口，不能干扰用户正在使用的管理中心。

## 签名、公证与预览版

没有 Developer ID 和苹果公证凭据时，只能生成 ad-hoc 签名的预览包。公开发布时必须：

1. 标记为 GitHub Pre-release。
2. 明确支持 Apple Silicon 和最低 macOS 版本。
3. 在 Release 中说明尚未经过苹果公证以及首次打开方式。
4. 不指导用户全局关闭 Gatekeeper。
5. 同时提供 DMG 与 `.sha256` 校验文件。

稳定版或扩大站外分发范围前，应使用 Developer ID 签名并完成苹果公证，同时在另一台 Apple Silicon Mac 和全新用户环境复核安装与权限流程。

## 发布前验证

发布前至少完成：

1. 运行通用管理中心测试、Python 编译检查、JavaScript 语法检查、shell 语法检查和 `plutil -lint`。
2. 检查原生宿主、后台、Node.js 与 FFmpeg 均为 arm64。
3. 检查应用签名结构、菜单栏宿主、后台健康检查、退出和 PID 清理。
4. 挂载 DMG，复核 App、`Applications` 链接、签名和 SHA-256。
5. 从与版本标签一致的干净源码重新构建，不直接上传旧候选。
6. 检查包内没有用户名、绝对工作区路径、令牌、Wi-Fi 或访问码。
7. 分别记录 USB 烧录、BLE 配网、局域网同步、Codex、Bambu 和登录启动的真实验证状态。

本机测试、实体 Dotii 验证、另一台 Mac 验证和苹果公证是不同验收层级，不得互相替代。

## 第三方组件与许可证

macOS 应用包含固定的 arm64 Python、Node.js、Codex CLI 和 LGPL 配置的 FFmpeg。升级任一组件后必须重新验证架构、运行协议、Finder 启动、许可证和包体校验。

许可证文本、版权声明和包元数据必须随应用保留。不得为了减小包体误删第三方要求分发的内容，也不得混入 GPL-only FFmpeg 组件。
