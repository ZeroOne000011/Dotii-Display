# Dotii Windows 开发指南

本文记录 Windows 管理中心的平台实现、开发环境、测试和发布流程。固件、协议、模块扩展和通用安全要求见 [通用开发指南](../../开发指南.md)。

## 平台边界

- 支持 Windows 10/11 x64。
- 运行数据位于 `%LOCALAPPDATA%\StateDisplay`。
- 登录启动写入 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\DotiiManagementCenter`。
- 串口使用 `COMx`，只有 USB VID/PID `303A:1001` 的 ESP32-S3 设备可以进入 Dotii 烧录流程。
- Windows 专用注册表、PowerShell/CIM、`.exe`、配对恢复和启动项逻辑必须保留在 Windows 平台边界内。

## 从源码运行

开发环境需要 Python 3.11+。先运行测试，再启动托盘宿主：

```powershell
python -m unittest discover -s bridge\tests -v
python -m compileall -q bridge
python bridge\bridge_app.py --open-dashboard
```

也可以双击 `bridge\启动 Dotii 管理中心.vbs`。VBS 源码入口不要求从已激活的 ESP-IDF 终端启动。

公开源码不跟踪体积较大的 `tools/`。需要复现已验证的 Windows 工具环境时，可以从当前 Windows 便携包中提取 `tools/` 到源码根目录；也可以自行安装兼容版本并使用现有回退查找路径。不得删除工具目录中要求保留的许可证和上游说明。

## 托盘与登录启动

`bridge/bridge_app.py` 负责 Windows 托盘、单实例和后台子进程生命周期。源码模式的登录启动项指向绝对路径的 `pythonw.exe -B bridge_app.py --startup`；冻结模式必须指向同目录的 `DotiiManagementCenter.exe --startup`，不能把后台 `DotiiBridge.exe` 配置为启动入口。

只要用户保持登录启动开关启用，管理中心正常启动时就应修复旧版本或移动目录后失效的启动路径。测试必须使用隔离的运行目录，不得复用用户的真实配置。

## 蓝牙与串口

Windows BLE 连接会请求系统配对。Dotii 重置绑定后，Windows 可能仍保留旧配对：扫描和状态读取可以成功，但启用加密响应通知时可能返回 `Could not start notify ... Unreachable`。

管理中心只对这一特定错误执行一次恢复：解除系统旧配对、等待蓝牙栈刷新、创建新客户端重新配对，然后重新发送配置。不得循环删除配对，也不得把该逻辑带入 macOS。

烧录器应依次检查当前 Python、`IDF_PYTHON_ENV_PATH`、`IDF_TOOLS_PATH`、用户目录下的官方 ESP-IDF Python 环境和系统盘 Espressif 官方安装目录。候选环境必须同时包含 esptool 与 `targets/stub_flasher/*/esp32s3.json`。

## 构建发布包

先使用 ESP-IDF 6.0.2 构建固件并运行管理中心测试，然后执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\build_windows.ps1 -Clean
```

发布前至少完成：

1. 检查固件、管理中心和两个 EXE 的版本一致。
2. 检查管理页面、托盘、登录启动、后台退出和 `/health`。
3. 校验发布目录 `SHA256SUMS.txt` 和便携 ZIP 的 SHA-256。
4. 在新的 Windows 用户目录中解压并启动。
5. 验证 USB 烧录、BLE 配网、重置后的配对恢复、Codex 与 Bambu 真实功能。
6. 检查固定工具链与第三方许可证。

安装 Inno Setup 时，以 `/DAppVersion=<版本>` 编译 `packaging/DotiiManagementCenter.iss`。打包脚本从固件版本派生发布版本，并拒绝不一致的版本参数。

`packaging/DotiiBridge.spec` 必须使用 `collect_data_files("esptool")` 收集 flasher stub JSON。后台桥接使用控制台型 PyInstaller bootloader，但由托盘程序以 `CREATE_NO_WINDOW` 隐藏启动，确保 esptool 可以回传进度且不会弹出未处理异常窗口。

## Windows 验收边界

自动化测试、EXE 启动、真实服务和实体设备是不同验收层级，必须分别记录。发布前还应验证：

- 系统托盘与登录启动。
- Windows 防火墙首次授权。
- `COMx` 识别与受限烧录。
- 重置配网后的旧配对恢复。
- Codex、Bambu MQTT、相机和控制。
- 便携包更新时不覆盖用户运行配置。
