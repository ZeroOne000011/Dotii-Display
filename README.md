# Dotii 桌面交互屏

Dotii 是一套由 ESP32-S3 圆形 AMOLED 桌面屏与 Windows 端“Dotii 管理中心”组成的开源状态显示系统。它可以显示 Codex 用量与任务状态、Bambu Lab 打印进度、自定义内容，并通过 Dotii 表情提供轻量互动。当前版本为 **1.0.0**。

![Dotii 桌面交互屏产品渲染图](assets/dotii-product-render.png)

[下载 Windows 便携包](https://github.com/ZeroOne000011/Dotii-Display/releases) · [MakerWorld 模型与打印文件](https://makerworld.com.cn/zh/models/2918764-dotii-zhuo-mian-jiao-hu-ping#profileId-3421401) · [查看开发指南](开发指南.md)

## 第一部分：便携包快速上手与常见问题

### 使用前准备

先准备以下硬件和材料。带“购买链接”的商品链接来自随项目装配说明书提供的链接；电商商品、库存和规格可能变化，下单前请再次核对型号、接口和尺寸。

| 类别 | 项目 | 数量 | 购买/资料链接 |
| --- | --- | ---: | --- |
| 核心硬件 | 微雪 Waveshare ESP32-S3-Touch-AMOLED-1.75 开发板（含 1.75 英寸圆形屏，无外壳） | ×1 | [淘宝购买链接](https://e.tb.cn/h.8msjONXF0M0Vdg1?tk=X4anTUTbPSP) · [官方说明文档](https://docs.waveshare.net/ESP32-S3-Touch-AMOLED-1.75/) |
| 供电 | 400 mAh 602030 锂电池，必须带 **MX1.25 端子头** | ×1 | [拼多多购买链接](https://mobile.yangkeduo.com/goods.html?ps=nMO2vEcuRY) |
| 结构配件 | Type-C 弯公头转母头，用于底座内的接口引出 | ×1 | [拼多多购买链接](https://mobile.yangkeduo.com/goods.html?ps=m4Bzx5p2iF) |
| 紧固件 | M2 螺丝，长度 4–7 mm 均可 | ×3 | — |
| 3D 打印件 | 按 MakerWorld 模型打印的外壳、底座、按钮等结构件 | ×5 | [MakerWorld 模型、打印文件和装配资料](https://makerworld.com.cn/zh/models/2918764-dotii-zhuo-mian-jiao-hu-ping#profileId-3421401) |
| 连接与运行环境 | 支持数据传输的 USB 线、Windows 10/11 x64 电脑、可用的 2.4 GHz Wi-Fi | 各 ×1 | — |

普通用户使用便携包无需安装 Python、Node.js、ESP-IDF 或 FFmpeg。

### 五步开始使用

1. 从 [GitHub Releases](https://github.com/ZeroOne000011/Dotii-Display/releases) 下载 `DotiiManagementCenter-1.0.0-portable.zip`，解压后保持目录结构不变。
2. 双击 `DotiiManagementCenter.exe`。程序会驻留在系统托盘，并在浏览器打开 Dotii 管理中心；默认地址为 `http://127.0.0.1:8787`。
3. 用 USB 线连接 Dotii，在“设置”页面识别设备。首次使用时可通过“一键烧录”写入随包固件。
4. 使用蓝牙配网，将 2.4 GHz Wi-Fi、管理中心地址和设备访问令牌同步到 Dotii。
5. 按需启用 Codex 或 Bambu，并在对应页面完成配置。两个模块首次启动均为关闭状态，不会自动安装、登录或连接外部服务。

关闭浏览器不会退出管理中心。需要重新打开页面、查看日志或退出程序时，请使用系统托盘中的 Dotii 图标。

### 主要功能

- **Codex 状态**：显示官方接口提供的额度、用量、任务状态、计划进度和用户可见消息。
- **Bambu 打印状态**：通过局域网读取打印进度、温度、耗材和图层，并在打印机支持时显示相机画面。
- **自定义页面**：编辑文字、颜色、图片和圆环，保存后同步到 466 × 466 圆屏。
- **Dotii 表情**：显示待机、眨眼、连接、工作、完成、失败等状态动画。
- **设备管理**：提供蓝牙配网、显示设置、休眠设置、登录自启动和受保护的一键烧录。

### 常见问题

<details>
<summary><strong>双击程序后管理页面没有打开</strong></summary>

确认系统托盘中是否有 Dotii 图标。可以从托盘重新打开管理页面；若后台启动失败，请退出后重新运行，并查看 `%LOCALAPPDATA%\StateDisplay\bridge.log`。

</details>

<details>
<summary><strong>登录 Windows 后 Dotii 没有自动启动</strong></summary>

在任务管理器的“启动应用”中确认 Dotii 管理中心已启用，然后手动运行一次当前位置的 `DotiiManagementCenter.exe`，程序会修复旧版本或移动目录后失效的启动路径。不要单独启动或添加 `DotiiBridge.exe`。

</details>

<details>
<summary><strong>Codex 页面没有数据</strong></summary>

先确认 Codex 模块已启用，并已在官方 Codex 中完成登录。随后在 Codex 设置中运行“Codex 运行检测”，根据账户、额度、用量和任务列表的分项结果排查。接口没有提供的数值会显示为 `--`。

</details>

<details>
<summary><strong>Bambu 页面没有打印数据或相机画面</strong></summary>

确认电脑与打印机位于同一局域网，并检查打印机 IP、序列号、访问码和局域网模式。相机不可用不会影响打印状态；相机还要求打印机提供兼容的视频地址。

</details>

<details>
<summary><strong>管理中心找不到 Dotii</strong></summary>

更换支持数据传输的 USB 线后重新扫描。若设备未进入下载模式，可按开发板说明使用 BOOT 按键后重试。程序只会向识别为 Dotii ESP32-S3 的设备开放烧录。

</details>

<details>
<summary><strong>更换路由器或电脑局域网地址后无法连接</strong></summary>

重新执行蓝牙配网并同步新的网络与管理中心地址，通常无需重新烧录固件。

</details>

<details>
<summary><strong>便携包中的两个 EXE 可以分开移动吗</strong></summary>

不可以。`DotiiManagementCenter.exe` 是托盘入口，`DotiiBridge.exe` 是由它管理的后台服务；请保留完整解压目录，不要单独移动或删除其中任意一个。

</details>

### 数据与安全

- 管理网页只监听本机；Dotii 读取数据时需要设备访问令牌。
- Wi-Fi 密码、Bambu 访问码、设备令牌和运行配置保存在本机用户目录，不应上传到公开仓库。
- Bambu 数据通过局域网获取。暂停、继续和停止只会在打印机状态允许时开放，并需要用户明确操作。
- 常规一键烧录不会擦除 NVS。请勿对未知设备、串口或固件执行烧录。
- Windows 首次允许局域网访问时可能显示防火墙窗口，程序名称应为“Dotii 管理中心后台服务”。请按实际使用的网络类型授权。

## 第二部分：开发者指南与二次开发

本仓库包含 Dotii 固件、Windows 管理中心、管理网页、测试和打包配置。完整的架构、协议、模块扩展、圆屏交互、构建、测试及发布规范统一记录在 [开发指南.md](开发指南.md)；开始修改前请先阅读该文档。

### 技术组成

- **设备端**：ESP32-S3、ESP-IDF 6.0.2、LVGL、466 × 466 CO5300 AMOLED、CST9217 触摸和 AXP2101 电源管理。
- **电脑端**：Python 3.11+ 后台与托盘程序、本机 HTML/CSS/JavaScript 管理页面。
- **通信**：带设备令牌的局域网 HTTP、Windows BLE 配网、Bambu LAN MQTT/TLS，以及 Codex App Server 的公开结构化接口。

```text
Codex App Server ─┐
                  ├─> Dotii 管理中心 ── HTTP/schema v1 ──> Dotii 固件
Bambu LAN MQTT ───┤          │
Bambu 相机链路 ───┘          └─ Windows BLE ──> 配网与设备状态
```

### 目录结构

```text
State-Display/
├─ main/                 ESP32 固件业务代码与生成资源
├─ components/           项目修改过的 Waveshare BSP
├─ managed_components/   ESP-IDF 锁定依赖的本地副本
├─ bridge/               管理中心后台、托盘、网页和回归测试
├─ firmware/             源码入口与发布程序共用的最小烧录固件包
├─ packaging/            Windows EXE 与安装器构建配置
├─ assets/               README 使用的产品图片
├─ README.md             用户入口与开发导航
└─ 开发指南.md           架构、协议、构建、测试和扩展规范
```

公开仓库不包含体积较大的 `tools/` 和重复打包内容 `release/`。`build/`、`.codx/`、`sdkconfig`、缓存、日志及 `%LOCALAPPDATA%\StateDisplay` 下的运行数据也不属于源码。仓库不包含 Wi-Fi、Bambu、设备令牌或 Codex 登录凭据。

### 获取源码开发工具

需要运行完整源码或离线复现 Windows 环境的开发者，可以从 [GitHub Release 下载 Windows x64 工具包](https://github.com/ZeroOne000011/Dotii-Display/releases/download/v1.0.0/Dotii-Tools-Windows-x64-1.0.0.zip)，并下载对应的 [SHA-256 校验文件](https://github.com/ZeroOne000011/Dotii-Display/releases/download/v1.0.0/Dotii-Tools-Windows-x64-1.0.0-SHA256.txt)。工具包包含项目当前验证过的 Node.js 24.16.0、npm 11.13.0、OpenAI Codex CLI 0.151.0 和 Windows x64 LGPL-only FFmpeg。

校验通过后，将 ZIP 解压到源码根目录，使 `tools/` 与 `bridge/`、`main/`、`firmware/` 位于同一级。PowerShell 校验示例：

```powershell
(Get-FileHash .\Dotii-Tools-Windows-x64-1.0.0.zip -Algorithm SHA256).Hash
```

工具包仅面向 Windows x64 源码开发；普通用户下载便携包无需单独下载或安装它。各工具目录中的许可证和上游说明文件必须保留。

### 从源码运行管理中心

```powershell
python -m unittest discover -s bridge\tests -v
python bridge\bridge_app.py --open-dashboard
```

也可以双击 `bridge\启动 Dotii 管理中心.vbs`。源码运行所需的外部依赖、版本要求和查找顺序见开发指南。

### 构建固件

请在英文路径中使用 ESP-IDF 6.0.2：

```powershell
idf.py build
```

构建成功只代表固件可以编译。需要写入设备时，请确认实际串口并单独执行烧录；不要把本机串口写入项目配置。

### 构建 Windows 发布包

在固件构建和管理中心测试通过后执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\build_windows.ps1 -Clean
```

脚本会依据项目版本构建管理中心与后台服务、收集固定工具链，并生成逐文件校验清单和便携 ZIP。公开发布前仍需完成许可证复核、干净 Windows 环境测试、真实服务测试和实体设备验收。

### 二次开发约束

- 新模块需要同时完成管理中心配置、网页、协议字段、固件解析、圆屏页面和模块关闭行为。
- 新协议字段优先保持可选，并为旧管理中心或旧固件提供安全回退；不兼容变更应提升 `schema_version`。
- Codex 与 Bambu 必须保持首次运行默认关闭，用户启用前不得检查、安装、下载、登录或连接外部服务。
- 管理网页由源码运行与发布包共用，修改功能时应保持两种入口行为一致。
- 密钥、访问码、令牌、用户名、绝对路径、局域网地址和串口不得写入源码。
- 固件构建、桌面测试、真实服务和实体设备属于不同验收层级，应分别记录结果。

进一步阅读：[Dotii 1.0 开发指南](开发指南.md)。
