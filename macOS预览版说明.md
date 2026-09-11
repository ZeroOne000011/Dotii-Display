# Dotii 管理中心 macOS 1.1.1 预览版

这是面向尝鲜用户的 Apple Silicon macOS 预览版。它使用 ad-hoc 本地签名，未经 Developer ID 身份签名，未经苹果公证。GitHub Release 必须标记为 **Pre-release / 预发布版**。

建议使用独立标签 `v1.1.1-macos-preview.1` 和标题“Dotii 1.1.1 macOS Preview 1”。该预发布不替代已发布的 Windows `v1.1.1` 正式版，Windows 用户仍从原有 `v1.1.1` Release 下载便携包。

## 支持范围

- Apple Silicon arm64 Mac。
- macOS 13 及以上。
- Dotii 1.1.1 固件。
- 不宣称支持 Intel Mac 或 Universal 2。

## 发布文件

- `DotiiManagementCenter-macOS-arm64-1.1.1.dmg`
- `DotiiManagementCenter-macOS-arm64-1.1.1.dmg.sha256`

下载后在终端进入文件所在目录，执行：

```bash
shasum -a 256 -c DotiiManagementCenter-macOS-arm64-1.1.1.dmg.sha256
```

结果应显示 `OK`。如果校验失败，不要打开该 DMG。

## 安装与首次启动

1. 打开 DMG，将 `DotiiManagementCenter-1.1.1.app` 拖到 `Applications` 快捷方式。
2. 从“应用程序”打开 Dotii 管理中心。
3. 如果 Gatekeeper 阻止启动，打开“系统设置 > 隐私与安全性”，在对应提示旁选择“仍要打开”，再确认启动。
4. 不要全局关闭 Gatekeeper，也不需要运行 `sudo spctl --master-disable`。
5. 首次扫描 Dotii 时允许蓝牙权限；连接 Dotii 或 Bambu 时按系统提示允许本地网络权限。

应用会驻留在菜单栏。关闭浏览器不会退出管理中心；可通过菜单栏图标重新打开页面、查看日志或退出。

## 已验证功能

- 管理中心启动、菜单栏宿主、后台健康检查与退出清理。
- Codex 和 Bambu 真实功能。
- Dotii USB 识别与 1.1.1 固件烧录。
- CoreBluetooth 扫描、配对、Wi-Fi 配网和重置后重新配网。
- Dotii 通过局域网鉴权接口同步管理中心数据。

## 已知限制

- 当前只在一台 Apple Silicon Mac 和一台实体 Dotii 上完成验收，未做跨 Mac 机型验收。
- 登录启动的启用、状态读取和关闭接口已验证；真实注销/登录或重启后的自动启动未验证。
- 未单独验证烧录中断或设备在烧录中拔出后的恢复。
- Dotii 重置配网后，如果 macOS 仍保留旧蓝牙记录，需要在“系统设置 > 蓝牙”中手动忽略 Dotii 后重新扫描。
- 电脑局域网 IP 变化后，可能需要重新扫描并配网，以向 Dotii 同步新地址。
- 受管理的公司或学校 Mac 可能禁止打开未公证应用。

## 运行数据与卸载

运行数据保存在 `~/Library/Application Support/Dotii`。卸载时先从菜单栏退出 Dotii 管理中心，然后将“应用程序”中的 App 移到废纸篓。如果不再需要原有配置，可再手动删除上述运行目录；其中可能包含 Bambu 访问码和设备令牌，不应上传或分享。

如果需要排查启动问题，日志位于 `~/Library/Application Support/Dotii/bridge.log`。提供日志前请检查并移除不希望分享的本机信息。
