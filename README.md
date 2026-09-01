# Dotii 桌面交互屏

Dotii 是一套由 ESP32-S3 圆形 AMOLED 桌面屏和 Windows 端“Dotii 管理中心”组成的状态显示系统。当前项目版本为 **1.0.0**。

本仓库是 Dotii 的公开源码仓库。为避免提交超过 GitHub 单文件限制的第三方运行时和重复打包内容，仓库不包含 `tools/` 与 `release/`；完整 Windows 便携包应从 GitHub Releases 获取，源码开发所需的 Node.js、Codex CLI、FFmpeg 等外部依赖请按《开发指南.md》准备。仓库不包含 Wi-Fi、Bambu、设备令牌或 Codex 登录凭据。

设备端负责圆屏显示、触摸、按键、电源与联网；管理中心负责读取 Codex 状态、连接 Bambu 打印机、编辑自定义页面、配置 Dotii 动画、蓝牙配网和受保护的固件烧录。

## 主要功能

- 466 × 466 圆形 AMOLED 界面，支持 Codex、Bambu、自定义内容和 Dotii 表情页面。
- Codex 页面显示官方接口可用的额度、任务状态、计划进度和用户可见消息；缺失值显示 `--`，不读取内部日志或隐藏推理。
- Bambu 页面通过局域网 MQTT/TLS 获取打印状态，并在打印机提供兼容地址时显示相机画面。
- 自定义页面可编辑文字、颜色、图片和圆环，保存后生成 RGB565 画面并同步到设备。
- Windows 管理中心提供托盘常驻、本机管理网页、登录自启动、蓝牙配网和一键烧录。
- Codex 与 Bambu 首次启动默认关闭，不会静默下载、安装或连接外部服务。

## 硬件与系统要求

- Waveshare ESP32-S3-Touch-AMOLED-1.75，ESP32-S3、466 × 466 AMOLED、CST9217 触摸和 AXP2101 电源管理。
- Windows 10/11 x64。
- 普通用户运行发布包不需要安装 Python、Node.js 或 FFmpeg。
- 从源码编译固件需要 ESP-IDF 6.0.2；开发管理中心需要 Python 3.11 或更高版本。
- Dotii 连接 2.4 GHz Wi-Fi；管理中心和 Dotii 应位于同一局域网。

## 目录结构

```text
State-Display/
├─ main/                 ESP32 固件业务代码与生成资源
├─ components/           项目修改过的 Waveshare BSP
├─ managed_components/   ESP-IDF 锁定依赖的本地副本
├─ bridge/               管理中心后台、托盘、网页和回归测试
├─ firmware/             VBS 与 EXE 共用的最小烧录固件包
├─ packaging/            Windows EXE 与安装器构建配置
├─ tools/                随包提供的 Node、Codex CLI 与 FFmpeg
├─ README.md              用户说明
└─ 开发指南.md            架构、协议、构建、测试和扩展规范
```

`build/`、`release/`、`.codx/`、`sdkconfig`、缓存和日志都是生成或本机文件，不属于源码。`firmware/` 是正式运行资源，只保存烧录所需的清单和三个镜像。项目使用相对路径定位资源，运行配置保存在 `%LOCALAPPDATA%\StateDisplay`，不会写回源码目录。

项目说明只维护根目录的 `README.md` 和 `开发指南.md`。`release/` 及便携 ZIP 只放运行程序、内置依赖和校验清单，不再复制说明文档；使用与开发说明统一以源码根目录版本为准。

## 普通用户快速开始

1. 解压 `DotiiManagementCenter-1.0.0-portable.zip`，保持目录结构不变。
2. 双击 `DotiiManagementCenter.exe`。程序会驻留在系统托盘，并打开 `http://127.0.0.1:8787`。
3. 在“设置”页面连接 Dotii。首次使用可先执行一键烧录，再通过蓝牙写入 2.4 GHz Wi-Fi、管理中心地址和设备令牌。
4. 需要 Codex 或 Bambu 时，在对应页面主动启用模块并完成账号或打印机配置。
5. 关闭浏览器不会退出管理中心；从托盘菜单可重新打开、查看日志或退出。

源码/VBS 入口面向开发者，会自动查找当前 Python、已激活的 ESP-IDF 环境，以及 Espressif 官方安装器的常见 Python 目录；只有在 esptool 和 ESP32-S3 flasher stub 都完整时才开放烧录按钮。便携 EXE 已内置同版本 esptool 与 stub 数据，不依赖本机 Python。

发布包包含两个程序：`DotiiManagementCenter.exe` 是托盘入口，`DotiiBridge.exe` 是由前者管理的后台服务。不要单独移动或删除其中任意一个。

开启“登录 Windows 后自动启动”后，注册表启动目标必须是 `DotiiManagementCenter.exe --startup`；后台 `DotiiBridge.exe` 不应直接出现在启动命令中。管理中心每次正常启动都会在保留开关状态的前提下修复旧版本写入的后台路径或已移动的发布目录路径。

## 模块说明

### Codex

启用后，管理中心优先使用发布包内固定版本的 Node.js 与 Codex CLI，通过本地 stdio App Server 读取公开结构化字段。它不会读取 `auth.json`、API Key、内部 rollout 日志、命令输出或隐藏推理。首次使用前请先在官方 Codex 中完成登录。Codex 设置中的“运行检测”由用户手动触发，会显示 CLI 版本与来源、App Server、后台采集器、登录账户、订阅类型、额度、用量和任务列表读取状态；检测只读，不会自动安装、登录或修改 Codex 配置。

### Bambu

在打印机中开启局域网模式或开发者模式，然后填写局域网 IP、序列号和访问码。访问码只保存在本机用户目录，不下发给 Dotii。暂停、继续和停止只在打印机状态允许时开放，并始终需要明确操作。

### 自定义页面与 Dotii 表情

自定义页面在浏览器本地处理原图，确认后才上传编辑结果。Dotii 表情页面包含固定的本地状态动画和可分配给 Codex/Bambu 业务状态的动画；动画资源不可上传或替换。

## 固件构建

在英文路径中打开 ESP-IDF 6.0.2 PowerShell，进入项目根目录后执行：

```powershell
idf.py build
```

需要写入设备时，再明确指定实际串口：

```powershell
idf.py -p <DOTII串口> flash monitor
```

不要把示例串口写入项目配置。日常构建不需要 `fullclean`；该命令会删除构建目录，并可能要求组件管理器重新恢复依赖。

## 从源码运行管理中心

```powershell
python -m unittest discover -s bridge\tests -v
python bridge\bridge_app.py --open-dashboard
```

也可以双击 `bridge\启动 Dotii 管理中心.vbs`。源码运行同样优先使用项目 `tools` 下的固定依赖，找不到时才检查用户目录或系统 `PATH`。

## 构建 Windows 发布包

先完成与当前源码一致的固件构建和管理中心测试，再执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\build_windows.ps1 -Clean
```

脚本从 `CMakeLists.txt` 读取版本，将当前 `build/` 中的最小烧录集合更新到共用 `firmware/`，构建两个带 Windows 版本信息的 EXE，复制固定工具链，生成逐文件 `SHA256SUMS.txt`，并输出：

```text
release\DotiiManagementCenter-1.0.0\
release\DotiiManagementCenter-1.0.0-portable.zip
```

发布目录和 ZIP 不重复打包说明文档。如果安装了 Inno Setup，可使用 `packaging\DotiiManagementCenter.iss` 生成当前用户安装器。完整的开发、协议、测试和发布检查见 [开发指南.md](开发指南.md)。

## 数据与安全

- 管理网页只监听本机；Dotii 数据接口需要设备访问令牌。
- Wi-Fi 密码、Bambu 访问码、设备令牌和登录信息不得提交到源码或发布说明。
- 常规一键烧录不擦除 NVS；不要对未知串口或未知固件执行烧录。
- Windows 首次允许局域网访问时可能显示防火墙授权窗口；程序名称应显示为“Dotii 管理中心后台服务”。只需按实际使用网络授权，不需要允许不受信任的公共网络。
- Bambu 数据走局域网，不依赖未经支持的云端逆向接口。
- 发布包内含 Node.js、npm、OpenAI Codex CLI、FFmpeg、PyInstaller、Bleak、Pillow、esptool 及其依赖；许可证文本随相应目录或二进制保留，版本和用途见开发指南。

## 故障排查

- 管理页面打不开：确认托盘中存在 Dotii 图标；退出后重新启动，并查看 `%LOCALAPPDATA%\StateDisplay\bridge.log`。
- 已启用但登录后未启动：在任务管理器“启动应用”中确认 Dotii 管理中心为“已启用”，然后手动启动一次当前目录中的 `DotiiManagementCenter.exe` 以修复旧路径；不要把 `DotiiBridge.exe` 单独加入启动项。
- Codex 无数据：确认模块已启用，然后在 Codex 设置中运行检测；根据 CLI、App Server、账户、额度/用量和任务列表的分项结果检查官方 Codex 登录及发布包 `tools\node`、`tools\codex-cli` 是否完整。
- Bambu 无数据：确认同一局域网、IP/序列号/访问码正确，并检查打印机局域网模式。
- 相机不可用：打印状态不受影响；检查打印机是否提供 RTSPS 地址以及 `tools\ffmpeg\bin\ffmpeg.exe` 是否完整。
- 找不到 Dotii：换用支持数据传输的 USB 线，重新扫描；必要时按住 BOOT 接线进入下载模式。
- 更换路由器或电脑局域网地址：重新执行蓝牙配网，不必重新烧录。

## 当前验证边界

1.0.0 源码和发布流程可以通过离线测试、静态检查、固件构建与 EXE 启动检查验证。跨电脑、不同网络、所有 Bambu 机型、全新设备烧录以及最终圆屏显示/触摸效果仍应由发布者在对应真实环境逐项验收，不能由构建成功替代。
