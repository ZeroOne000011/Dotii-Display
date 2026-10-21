# Dotii Desktop Display

[简体中文](README.md) | **English**

Dotii is an open-source status display built around an ESP32-S3 circular AMOLED screen and Dotii Management Center for Windows/macOS. It shows Codex usage and task status, Bambu Lab printing progress, custom content, and interactive expressions.

![Dotii desktop display product rendering](assets/dotii-product-render.png)

[Windows download](https://github.com/ZeroOne000011/Dotii-Display/releases/tag/v1.2) · [macOS download (preview)](https://github.com/ZeroOne000011/Dotii-Display/releases/tag/v1.2-macos-preview.1) · [MakerWorld models and assembly](https://makerworld.com.cn/zh/models/2918764-dotii-zhuo-mian-jiao-hu-ping#profileId-3421401) · [Developer guide](DEVELOPMENT.en.md)

## Downloads and compatibility

The current version is **1.2**. Both platforms use the same device firmware and include the components needed to run the app. You do not need to install Python, Node.js, Codex CLI, FFmpeg, or ESP-IDF separately.

| Platform | Requirements | Download | Release type |
| --- | --- | --- | --- |
| Windows | Windows 10/11 x64 | `DotiiManagementCenter-1.2-portable.zip` | Stable |
| macOS | Apple Silicon, macOS 13.5 or later | `DotiiManagementCenter-macOS-arm64-1.2.dmg` | Preview, ad-hoc signed, not notarized by Apple |

## What you need

| Category | Item | Quantity | Purchase / documentation |
| --- | --- | ---: | --- |
| Main board | Waveshare ESP32-S3-Touch-AMOLED-1.75, including the 1.75-inch round screen, without enclosure | ×1 | [Taobao](https://e.tb.cn/h.8msjONXF0M0Vdg1?tk=X4anTUTbPSP) · [Official documentation](https://docs.waveshare.net/ESP32-S3-Touch-AMOLED-1.75/) |
| Battery | 3.7 V, 400 mAh 602030 lithium battery with an **MX1.25 2P connector** | ×1 | [Pinduoduo](https://mobile.yangkeduo.com/goods.html?ps=nMO2vEcuRY) |
| Connector | Right-angle USB-C male-to-female adapter for the base | ×1 | [Pinduoduo](https://mobile.yangkeduo.com/goods.html?ps=m4Bzx5p2iF) |
| Fasteners | M2 screws, 4–7 mm long | ×3 | — |
| Printed parts | Enclosure, base, buttons, and other structural parts | ×5 | [Models, print files, and assembly](https://makerworld.com.cn/zh/models/2918764-dotii-zhuo-mian-jiao-hu-ping#profileId-3421401) |
| Connection | USB data cable; a 2.4 GHz network when using Wi-Fi | ×1 each | — |

## Installation and first connection

### Windows

1. Download the Windows ZIP, extract the entire archive into a new folder, and run `DotiiManagementCenter.exe`.
2. The app stays in the system tray. Right-click the tray icon to open the dashboard, or visit `http://127.0.0.1:8787`.
3. Follow “Connect Dotii” below to set up the device.

Keep the extracted directory structure intact. Do not move or run `DotiiBridge.exe` separately. Closing the browser leaves the app running; use the tray icon to open the dashboard, view logs, or quit.

### macOS

1. Download and open the DMG, then drag `DotiiManagementCenter-1.2.app` into Applications.
2. Open the app. If macOS blocks it, choose “Open Anyway” in System Settings → Privacy & Security and confirm. You do not need to disable system security globally. Managed Macs may prevent unnotarized apps from running.
3. Allow Bluetooth and local network access when prompted. Use the menu bar icon to open the dashboard, view logs, or quit.
4. Follow “Connect Dotii” below to set up the device.

### Connect Dotii

1. Switch languages under Settings → Dotii Management Center → App settings → 语言/Language. The dashboard and screen change together; task text, filenames, and custom content retain their original language.
2. Connect the device with a USB data cable and identify Dotii on the dashboard’s Settings page. For first use, flash the included firmware using the firmware flashing action.
3. Choose Auto (USB preferred), USB only, or Wi-Fi only as needed. For Wi-Fi, send the 2.4 GHz network and Management Center connection settings through USB or Bluetooth. USB-only data sync does not require a router.
4. Enable and configure Codex or Bambu as needed. Both modules are off on first launch and do not automatically install software, log in, or connect to external services before you enable them.

## Features

- **Codex status**: usage limits, usage, and task status from official interfaces. Swipe left on the screen to view detailed conversation information.
- **Bambu printing status**: 3D printer progress over the local network. Swipe left on the screen to view temperatures, filament, and other details, with camera images where supported.
- **Custom screen**: edit text, colors, images, and rings, then sync to Dotii.
- **Dotii expressions**: idle, blinking, connecting, working, completed, and failed animations.

## Updating and removal

Quit the old app from the tray or menu bar before updating.

| Action | Windows | macOS |
| --- | --- | --- |
| Update | Extract the full ZIP into a new empty folder; remove the old program folder once the new version works | Replace the old App in Applications |
| Settings folder | `%LOCALAPPDATA%\StateDisplay` | `~/Library/Application Support/Dotii` |
| Remove | Quit and delete the program folder | Quit and move the App to Trash |

Replacing or deleting the program preserves settings. Remove the settings folder manually only if you want a complete uninstall. It may contain a Bambu access code and device connection details; do not upload or share it.

If a version updates device features, flash the firmware included with the new Management Center. Normal flashing preserves NVS settings. Switching computer platforms does not require reflashing the same firmware version.

## Troubleshooting

AI tools such as Codex can help investigate software issues and develop additional features.

<details>
<summary><strong>The dashboard did not open</strong></summary>

Confirm the app is running and open it from the Windows tray or Mac menu bar. You can also visit `http://127.0.0.1:8787`.

</details>

<details>
<summary><strong>Codex shows no data</strong></summary>

Enable the module and sign in through official Codex software. Run “Codex connection check” in the Codex settings and follow the individual results. Values not supplied by the interface appear as `--`.

</details>

<details>
<summary><strong>Bambu has no printing data or camera image</strong></summary>

Keep the computer and printer on the same local network. Check the printer IP, serial number, access code, and LAN mode. Camera availability does not affect printing status.

</details>

<details>
<summary><strong>Dotii cannot be found</strong></summary>

Try another USB data cable and scan again. If download mode is needed, follow the board documentation for the BOOT button. Flashing is available only for identified Dotii ESP32-S3 devices. On Mac, also check Bluetooth access in System Settings → Privacy & Security.

</details>

<details>
<summary><strong>Connection fails after changing computers, routers, or resetting</strong></summary>

Hold Dotii’s right-side button to enter Settings, then hold “Reset network” for 1.2 seconds. The device clears Wi-Fi, its Management Center binding, and Bluetooth bonds, then restarts. Scan and provision it from the new computer. Language, orientation, and sleep settings are preserved.

If the computer retains an old pairing, Windows attempts automatic recovery once. On Mac, forget Dotii in System Settings → Bluetooth and scan again. A changed computer LAN IP may also require resending the Management Center address.

</details>

<details>
<summary><strong>Where are the logs?</strong></summary>

Open logs from the tray or menu bar, or find `bridge.log` in the settings folder listed above. Review and remove private local information before sharing.

</details>

## Data and security

The dashboard and management API accept local access only. Device data requests require an access token. Wi-Fi passwords, Bambu access codes, and device tokens stay in local settings. Flashing is restricted to identified devices and valid firmware packages.

Windows may show a firewall prompt for “Dotii 管理中心后台服务” when allowing local network access for the first time. Authorize the network types you actually use.

## Development

Read the [developer guide](DEVELOPMENT.en.md) for [architecture and source](DEVELOPMENT.en.md#architecture), [module development](DEVELOPMENT.en.md#modules), [testing and debugging](DEVELOPMENT.en.md#validation), [Windows builds](DEVELOPMENT.en.md#windows-development), and [macOS builds](DEVELOPMENT.en.md#macos-development).

## Community contributions

Thank you to the community developers extending Dotii. The contribution below has not been merged and is not part of the current release.

### Z.ai usage and Claude Code status modules

[@wfzylcl878-creator](https://github.com/wfzylcl878-creator) contributed GLM Coding Plan usage display for Z.ai, Claude Code status from hooks with expression integration, and improvements including module status and proxy fake-IP address filtering.

The maintainer currently lacks independent validation conditions, so the contribution is listed for reference without merging. The contributor reports macOS source tests, firmware builds, and device validation; Windows has not been tested.

[PR #1](https://github.com/ZeroOne000011/Dotii-Display/pull/1) · [Contributor repository](https://github.com/wfzylcl878-creator/Dotii-Display) · [Implementation referenced by the PR](https://github.com/wfzylcl878-creator/Dotii-Display/tree/529d962aefd3747fc671c16152a1a4df79ec12cb)
