# Dotii 桌面交互屏

Dotii 是一套由 ESP32-S3 圆形 AMOLED 桌面屏与 Windows/macOS 端“Dotii 管理中心”组成的开源状态显示系统。它可以显示 Codex 用量与任务状态、Bambu Lab 打印进度、自定义内容，并通过 Dotii 表情提供轻量互动。

![Dotii 桌面交互屏产品渲染图](assets/dotii-product-render.png)

[Windows 下载](https://github.com/ZeroOne000011/Dotii-Display/releases/tag/v1.1.1) · [macOS 下载（预览版）](https://github.com/ZeroOne000011/Dotii-Display/releases/tag/v1.1.1-macos-preview.1) · [MakerWorld 模型与打印文件](https://makerworld.com.cn/zh/models/2918764-dotii-zhuo-mian-jiao-hu-ping#profileId-3421401) · [开发指南](开发指南.md)

## 选择你的系统

| 系统 | 支持范围 | 下载内容 | 当前状态 |
| --- | --- | --- | --- |
| Windows | Windows 10/11 x64 | `DotiiManagementCenter-1.1.1-portable.zip` | 正式版 |
| macOS | Apple Silicon、macOS 13 及以上 | `DotiiManagementCenter-macOS-arm64-1.1.1.dmg` | 预览版，未经苹果公证 |

macOS 预览版目前不支持 Intel Mac。由于尚未经过苹果公证，首次打开时需要在“系统设置 > 隐私与安全性”中手动允许。Windows 和 macOS 安装包均已包含运行所需组件，普通用户无需安装 Python、Node.js、Codex CLI、FFmpeg 或 ESP-IDF。

## 使用前准备

| 类别 | 项目 | 数量 | 购买/资料链接 |
| --- | --- | ---: | --- |
| 核心硬件 | 微雪 Waveshare ESP32-S3-Touch-AMOLED-1.75 开发板（含 1.75 英寸圆形屏，无外壳） | ×1 | [淘宝购买链接](https://e.tb.cn/h.8msjONXF0M0Vdg1?tk=X4anTUTbPSP) · [官方说明文档](https://docs.waveshare.net/ESP32-S3-Touch-AMOLED-1.75/) |
| 供电 | 3.7 V、400 mAh 602030 锂电池，带 **MX1.25 2P 插头** | ×1 | [拼多多购买链接](https://mobile.yangkeduo.com/goods.html?ps=nMO2vEcuRY) |
| 结构配件 | Type-C 弯公头转母头，用于底座内的接口引出 | ×1 | [拼多多购买链接](https://mobile.yangkeduo.com/goods.html?ps=m4Bzx5p2iF) |
| 紧固件 | M2 螺丝，长度 4–7 mm 均可 | ×3 | — |
| 3D 打印件 | 按 MakerWorld 模型打印的外壳、底座、按钮等结构件 | ×5 | [MakerWorld 模型、打印文件和装配资料](https://makerworld.com.cn/zh/models/2918764-dotii-zhuo-mian-jiao-hu-ping#profileId-3421401) |
| 连接与网络 | 支持数据传输的 USB 线、2.4 GHz Wi-Fi | 各 ×1 | — |

## Windows 快速上手

1. 从 [Windows Release](https://github.com/ZeroOne000011/Dotii-Display/releases/tag/v1.1.1) 下载便携包，完整解压到一个新文件夹。
2. 双击 `DotiiManagementCenter.exe`。程序会驻留在系统托盘，并在浏览器打开管理中心；默认地址为 `http://127.0.0.1:8787`。
3. 用 USB 线连接 Dotii，在“设置”页面识别设备。首次使用时可通过“一键烧录”写入随包固件。
4. 使用蓝牙配网，将 2.4 GHz Wi-Fi 和管理中心连接信息同步到 Dotii。
5. 按需启用 Codex 或 Bambu，并在对应页面完成配置。

请保持解压后的目录结构不变，不要单独移动或运行 `DotiiBridge.exe`。关闭浏览器不会退出管理中心；重新打开页面、查看日志或退出程序时，请使用系统托盘中的 Dotii 图标。

### 更新 Windows 便携包

先从系统托盘彻底退出旧版管理中心，再把新版 ZIP 完整解压到新的空文件夹。确认新版正常后，可以删除旧版程序文件夹；不要把新版直接覆盖到旧目录。

现有设置保存在 `%LOCALAPPDATA%\StateDisplay`，删除旧版程序文件夹不会清除配置。若新版本包含 Dotii 固件更新，请在新版管理中心中重新执行“一键烧录”。

## macOS 快速上手

1. 从 [macOS Release](https://github.com/ZeroOne000011/Dotii-Display/releases/tag/v1.1.1-macos-preview.1) 下载 DMG。当前版本仅支持 Apple Silicon Mac 和 macOS 13 及以上。
2. 打开 DMG，将 `DotiiManagementCenter-1.1.1.app` 拖到“应用程序”。
3. 从“应用程序”打开 Dotii 管理中心。如果系统阻止启动，请打开“系统设置 > 隐私与安全性”，在对应提示旁选择“仍要打开”，然后再次确认。
4. 首次扫描 Dotii 时允许蓝牙权限；连接 Dotii 或 Bambu 时按系统提示允许本地网络权限。
5. 应用启动后会驻留在菜单栏。后续烧录、蓝牙配网及模块设置均在管理页面中完成。

不需要也不建议全局关闭 macOS 的安全检查。受管理的公司或学校 Mac 可能禁止打开未经公证的应用。

### 可选：校验下载文件

将 DMG 和 `.dmg.sha256` 文件放在同一目录，在终端进入该目录并执行：

```bash
shasum -a 256 -c DotiiManagementCenter-macOS-arm64-1.1.1.dmg.sha256
```

结果应显示 `OK`；如果校验失败，请不要打开该 DMG。

### 更新与卸载

更新前先从菜单栏退出 Dotii 管理中心，再用新版 App 替换“应用程序”中的旧版。运行配置保存在 `~/Library/Application Support/Dotii`，替换或删除 App 不会自动清除配置。

卸载时先退出应用，再将 App 移到废纸篓。如果不再需要原有配置，可以手动删除上述运行目录；其中可能包含 Bambu 访问码和设备连接信息，请勿上传或分享。

## 主要功能

- **Codex 状态**：显示官方接口提供的额度、用量、任务状态、计划进度和用户可见消息。
- **Bambu 打印状态**：通过局域网读取打印进度、温度、耗材和图层，并在打印机支持时显示相机画面。
- **自定义页面**：编辑文字、颜色、图片和圆环，保存后同步到 466 × 466 圆屏。
- **Dotii 表情**：显示待机、眨眼、连接、工作、完成、失败等状态动画。
- **设备管理**：提供蓝牙配网、重置配网、显示设置、休眠设置、登录自启动和受保护的一键烧录。

Codex 与 Bambu 首次运行默认关闭，不会在用户启用前自动安装、登录或连接外部服务。

## 常见问题

<details>
<summary><strong>管理页面没有自动打开</strong></summary>

Windows 请检查系统托盘，macOS 请检查菜单栏，然后通过 Dotii 图标重新打开管理页面。也可以在程序正在运行时访问 `http://127.0.0.1:8787`。

</details>

<details>
<summary><strong>Codex 页面没有数据</strong></summary>

先确认 Codex 模块已启用，并已在官方 Codex 中完成登录。随后在 Codex 设置中运行“Codex 运行检测”，根据分项结果排查。接口没有提供的数值会显示为 `--`。

</details>

<details>
<summary><strong>Bambu 页面没有打印数据或相机画面</strong></summary>

确认电脑与打印机位于同一局域网，并检查打印机 IP、序列号、访问码和局域网模式。相机不可用不会影响打印状态。

</details>

<details>
<summary><strong>管理中心找不到 Dotii</strong></summary>

更换支持数据传输的 USB 线后重新扫描。若设备未进入下载模式，可按开发板说明使用 BOOT 按键后重试。程序只会向识别为 Dotii ESP32-S3 的设备开放烧录。

macOS 用户还应在“系统设置 > 隐私与安全性 > 蓝牙”中确认已允许 Dotii 管理中心使用蓝牙。

</details>

<details>
<summary><strong>更换路由器或电脑后无法连接</strong></summary>

请长按 Dotii 右侧按钮进入“设置”，再长按“重置配网”1.2 秒。设备会清除 Wi-Fi、管理中心绑定和蓝牙配对后重启；随后在新电脑的管理中心重新扫描并配网，通常无需重新烧录固件。

若系统仍保留旧配对，Windows 1.1.1 会尝试自动恢复；macOS 请在“系统设置 > 蓝牙”中忽略 Dotii 后重新扫描。

</details>

<details>
<summary><strong>如何查找日志</strong></summary>

Windows 日志位于 `%LOCALAPPDATA%\StateDisplay\bridge.log`，macOS 日志位于 `~/Library/Application Support/Dotii/bridge.log`。分享日志前，请检查并移除不希望公开的本机信息。

</details>

## 数据与安全

- 管理网页和管理 API 只允许本机访问；Dotii 读取数据时需要设备访问令牌。
- Wi-Fi 密码、Bambu 访问码、设备令牌和运行配置保存在本机用户目录，不应上传到公开仓库。
- Bambu 数据通过局域网获取。暂停、继续和停止只会在打印机状态允许时开放，并需要用户明确操作。
- 常规一键烧录不会擦除 NVS。请勿对未知设备、串口或固件执行烧录。
- Windows 首次允许局域网访问时可能显示防火墙窗口，程序名称应为“Dotii 管理中心后台服务”。请按实际使用的网络类型授权。

## 开发与二次开发

普通用户不需要准备开发环境。准备修改源码时，请先选择对应文档：

- [通用开发指南](开发指南.md)：项目架构、固件、协议、模块扩展、圆屏交互与通用测试规范。
- [Windows 开发指南](docs/development/windows.md)：Windows 环境、托盘、蓝牙、串口、便携包与发布流程。
- [macOS 开发指南](docs/development/macos.md)：macOS 菜单栏宿主、CoreBluetooth、串口、签名、公证与 DMG 构建。

平台共用固件、后台业务、管理网页和设备协议。平台差异应保留在对应适配器、宿主与打包目录中，不应复制共用业务代码。
