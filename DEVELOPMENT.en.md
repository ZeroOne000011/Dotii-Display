# Dotii Developer Guide

[简体中文](开发指南.md) | **English**

This guide covers source changes, new data sources, and round-screen modules. See the [README](README.en.md) for downloads, installation, usage, and release support.

Start by running Management Center and its tests, then follow data collection → snapshot → firmware parsing → screen rendering. Backend or web changes alone do not require flashing; firmware changes require a build and device update.

[Architecture](#architecture) · [Environment](#environment) · [Protocol](#protocol) · [Modules](#modules) · [Testing](#validation) · [Windows](#windows-development) · [macOS](#macos-development)

<a id="architecture"></a>

## 1. Architecture and source entry points

Windows and macOS share the ESP32-S3 firmware, Python business logic, dashboard, and device protocol. Platform differences belong in adapters, native hosts, and packaging entry points. Keep one implementation of shared behavior. The user-facing name is “Dotii Management Center”; names such as `bridge` remain internal paths or compatibility fields.

```text
Codex App Server ─┐
Bambu LAN MQTT ───┼─> Dotii Management Center ── USB DTU1 / HTTP ──> Dotii firmware
Bambu camera ─────┘             └─ BLE ── provisioning and status
```

| Directory / file | Responsibility |
| --- | --- |
| `main/main.c`, `main/app_state.*` | Firmware entry, page registration, schema v1 parsing, and business state |
| `main/state_ui.*` | Round-screen pages, expressions, refresh, and interaction |
| `main/connectivity.*`, `main/usb_transport.*` | Wi-Fi/HTTP, USB frames, authentication, image sync, and USB provisioning |
| `main/device_config.*`, `main/ble_bridge.*`, `main/board_input.*` | NVS, BLE provisioning/bond removal, touch, and buttons |
| `main/ui_language.*`, `main/ui_translations.inc`, `main/generated/` | Fixed text translations and generated fonts/icons; do not hand-edit generated data |
| `components/waveshare__esp32_s3_touch_amoled_1_75/` | Local BSP based on Waveshare 3.0.1, including orientation, gap, software rotation, and dirty-rectangle alignment |
| `bridge/codex_bridge.py` | Local Web/API server, settings, and module orchestration |
| `bridge/codex_app_server.py`, `bridge/bambu_client.py` | Codex collection, Bambu MQTT/camera, and legacy control compatibility |
| `bridge/usb_bridge.py`, `bridge/bluetooth_bridge.py`, `bridge/firmware_flasher.py` | USB sessions and flashing exclusion, BLE provisioning, identification, and flashing |
| `bridge/connection_settings.py`, `bridge/runtime_paths.py` | Language/connection persistence and resource/runtime paths |
| `bridge/platforms/` | Platform interfaces for paths, tools, login startup, Bluetooth, serial, and subprocesses |
| `bridge/bridge_app.py`, `macos/DotiiManagementCenter/` | Windows tray host and Mac AppKit menu bar host |
| `bridge/web/`, `bridge/localization.py` | Shared dashboard, `translations.json`, and backend translations |
| `firmware/`, `packaging/` | Shared minimal flashing package and Windows/macOS build entry points |
| `bridge/tests/`, `scripts/generate_dotii_expression_assets.py` | Tests and expression preview generation |

<a id="environment"></a>

## 2. Environment and paths

Firmware uses **ESP-IDF 6.0.2**, targets `esp32s3`, and takes its version from `PROJECT_VER` in `CMakeLists.txt`. From an ESP-IDF environment, run at the repository root:

```powershell
idf.py --version
idf.py -B .codex/build build
```

`dependencies.lock`, `sdkconfig.defaults`, and the locked `managed_components/` are build inputs. The project uses `MINIMAL_BUILD`; if local dependencies are removed, restore them through Component Manager using the lock file. Do not alter third-party algorithms to bypass application issues. Recheck local adaptations when updating the BSP.

Management Center development requires Python 3.11+. Windows dependencies are in `bridge/requirements.txt`; pinned Mac build dependencies are in `packaging/macos/requirements.txt`. Platform sections contain run and packaging commands.

Resolve resources through `runtime_paths.py`:

| Path / function | Purpose |
| --- | --- |
| `application_root()`, `bundle_root()` | Source/EXE root and PyInstaller resources |
| `resource_path()`, `tools_root()` | Web, icon, firmware, and private tool resources |
| `%LOCALAPPDATA%\StateDisplay` | Writable Windows settings, resources, and logs |
| `~/Library/Application Support/Dotii` | Writable macOS settings, resources, and logs |
| `.app/Contents/Resources`, `.app/Contents/MacOS` | Read-only Mac resources/backend/tools and native executable |
| `.codex/` | Virtual environments, caches, isolated test runtimes, intermediate builds, and candidates |
| `release/` | Validated deliverables; ignored by Git |

Do not hard-code usernames, drive letters, serial ports, IPs, or credentials. Fail safely if fallback tools are missing. Keep runtime settings separate from the initial seed `bridge/state.json`. Tests use isolated settings and must not take over user device sessions. Logs and generated comments must not disclose local paths or secrets.

<a id="protocol"></a>

## 3. Device protocol and management API

Devices retrieve the same `schema_version = 1` snapshot through token-authenticated HTTP or USB. The default port is `8787`; all `/api/v1/admin/*` routes accept loopback clients only.

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Service liveness |
| `GET /api/v1/snapshot` | Device snapshot; token required |
| `GET /api/v1/custom-screen.rgb565` | Custom RGB565 frame; token required |
| `POST /api/v1/admin/codex/check` | User-triggered read-only check after enabling the module |
| `POST /api/v1/admin/connection` | Save language and connection mode |
| `POST /api/v1/admin/usb/configure` | Send network settings through a bound USB session |
| `/assets/*`, web JS/CSS | Local dashboard resources |

Consult `BridgeHandler` and frontend calls for the full route list. New management endpoints require loopback access, Content-Type/body-size/input validation, stable JSON errors, and success/failure tests. Device endpoints also validate tokens.

USB uses `DTU1`, little-endian JSON byte length, CRC32, and UTF-8 JSON, with a 2 MiB frame limit. Images use 4096-byte chunks with offset and whole-image CRC verification. Sessions include device tokens and stable IDs. Automatic mode prefers USB; USB-only and Wi-Fi-only modes are also supported. Flashing releases the data session and resumes it afterward. An active data session suppresses automatic deep sleep while preserving manual sleep.

Compatibility rules:

- Prefer optional fields and preserve existing meanings. Breaking changes require a schema increment plus migration or explicit rejection.
- Firmware safely handles missing, mistyped, overlong, and out-of-range values. Bound all strings, arrays, images, and request bodies.
- Validate and trim before writing. Do not include secrets in business snapshots. Missing Codex data stays `--`.
- The optional 1.2 fields `display.language` and `display.connection_mode` fall back to existing settings when absent.

Example Dotii fields:

```json
{
  "schema_version": 1,
  "modules": {"codex": false, "bambu": false, "custom": true, "dotii": true},
  "dotii": {
    "expression": "idle_breath",
    "state": "idle",
    "state_assigned": false,
    "state_duration_ms": 0,
    "state_hold": false,
    "state_token": 0
  }
}
```

Missing Dotii fields return the page to idle without disrupting page cycling, touch, or power handling.

<a id="modules"></a>

## 4. Adding modules and managing lifecycle

A module must implement the complete chain:

1. Management Center settings model, persistence, and safe defaults.
2. Local management API and shared dashboard controls.
3. Optional snapshot fields, validation, and safe firmware `app_state` parsing.
4. `state_ui` page registration, refresh, circular safe area, and interaction.
5. Module start/stop, page cycling, screen-off recovery, and settings behavior.
6. Offline tests and relevant builds, followed by real service/device validation.

For a read-only sensor, first convert readings into bounded snapshot fields, then parse and display them. Keep external service credentials off the firmware. Check disabled, normal, missing-field, disconnected, and old-firmware paths.

Codex/Bambu default to off. Do not detect, download, install, log in, or connect before the user enables them. Custom and Dotii pages default to on. Disabling a module stops its connections, collector, and camera and hides its device page.

The five built-in Dotii states have fixed mappings. Each business state belongs to at most one animation or may be unassigned; one animation can serve multiple business states. Durations are 1, 3, 5, or 30 seconds, or hold. Unassigned/expired states return to idle. Use `state_token`, assignment, duration, and expression to identify an entry; dashboard polling must not restart the timer. Only a new state retriggers it. Animation upload, replacement, and deletion are not supported.

Language changes translate system text only; task text, filenames, and user content retain their language. The dashboard header reports local service health; modules and device links report their own status. Determine the actual USB/Wi-Fi channel from session state and data timestamps.

## 5. Data sources and images

### Codex

Use public structured interfaces through the pinned CLI’s `codex app-server --listen stdio://`. Do not read `auth.json`, internal session files, rollout JSONL, hidden reasoning, or tool arguments.

- Isolate individual historical tasks that fail to parse unknown types. If all tasks are temporarily unreadable, retain the last snapshot while accurately reporting collection status.
- Keep multi-task ordering and selection stable. Do not estimate missing Time/Token fields. Cover long tasks, actual completion, and stale tasks in state logic.
- The user-triggered connection check starts a short-lived App Server and calls `account/read`, `account/rateLimits/read`, `account/usage/read`, and `thread/list`. It does not log in or alter settings, and returns no secrets, command paths, or task content.
- Pinned dependencies are Node.js 24.16.0, npm 11.13.0, and Codex CLI 0.151.0. Recheck schema, login reuse, task isolation, licensing, and bundle integrity after upgrades.

### Bambu

Read status over LAN MQTT/TLS, keeping the access code local. Do not substitute unsupported reverse-engineered cloud interfaces. Cover idle, preparing, printing, paused, completed, and failed states. The web and device detail pages display information only. Legacy backend controls remain for compatibility with state restrictions; current pages do not call them.

Camera transport uses RTSPS/FFmpeg or the older port-6000 JPEG connection as needed. Printing status must not depend on FFmpeg. Enable the relevant camera connection only with an enabled module, complete configuration, camera enabled, and a valid address. Windows pins BtbN LGPL-only FFmpeg `n8.1.2-50-g1a748fe2cd-20260831`; see the tool preparation scripts for Mac builds.

The backend obtains frame version and immutable pixels atomically. Firmware commits only complete CRC-verified frames and copies them under a lock into a separate 115200-byte PSRAM UI buffer. Retain the previous frame until a new one is complete; hide it when explicitly disabled/expired. LVGL must not reference a receive buffer being reused for writes.

### Custom screen

The browser crops and scales the source image locally before user-confirmed upload. Supported inputs are PNG/JPEG, with a 4 MiB source limit. The final frame is 466 × 466, big-endian RGB565, exactly 434312 bytes. Store settings and processed assets in the platform runtime directory. Update by revision; keep the last valid frame on failure or show a safe default if none exists.

## 6. Provisioning and flashing

Platform BLE implementations discover Dotii, read status, and send chunked Wi-Fi, Management Center URL, and token settings. Initial unbound provisioning requires encrypted GATT; later changes retain token verification. The ordinary dashboard does not accept or save an old token. Changing computers uses the device’s provisioning reset.

Holding reset for 1.2 seconds clears only `dotii_cfg` and NimBLE bonds, then restarts. Preserve language, orientation, brightness, and sleep settings in `display_ui`. See platform sections for stale system pairings.

Flashing follows this sequence:

1. Read image paths and offsets from shared `firmware/flasher_args.json`, restricting paths to the resource directory.
2. Validate SHA-256 and application version. Packaging synchronizes firmware resources after builds.
3. Enumerate serial ports and allow only Dotii ESP32-S3 devices with VID/PID `303A:1001`.
4. Require explicit confirmation, invoke esptool, and determine success from exit status and device hash checks.

Preserve single-task exclusion, failure recovery, and NVS. Do not allow arbitrary ports/images or default full-chip erasure. Normal automated tests must not connect to or write real devices.

## 7. Display and coding constraints

- Use true black on the 466 × 466 display and keep key content within its safe area. Swiping right is the only subpage return gesture; do not add back buttons. Preserve Button A’s existing page/control semantics and separately confirm dangerous actions.
- Keep `MADCTL=0x00`, gap `(6,0)`, BSP software composition/rotation, partial refresh, and even-aligned physical RGB565 dirty rectangles. Do not restore LVGL arbitrary-angle object rotation.
- Two 12-line CPU draw buffers live in PSRAM, copied through a separate 12-line internal DMA tile for QSPI. Do not use those PSRAM buffers directly as the current DMA source. Keep 10 static Wi-Fi RX buffers.
- Size status text using scaled dimensions and outlines, scale around its center, and do not restore fixed widths during snapshot refresh. The weekly page uses `Reset`; Token `k/m/b` conversion promotes before rounding to 1000 and uses 64-bit intermediates.
- Bambu filenames use single-line ellipsis at about 24 px. Center filament/layers vertically in separate rows. Avoid clipping at the lower safe-area container and center camera placeholder scaling.
- Deep-sleep recovery releases RTC IO before restoring touch, buttons, AXP2101, and TCA9554. Bound shared-I²C retries without permanent degradation. Evaluate Flash, PSRAM, and frame time before adding Chinese glyphs.
- Python uses type hints, `pathlib.Path`, bounded input, explicit exceptions, and stoppable threads. Keep C module interfaces small. JS references existing DOM elements and fully hides disabled content.
- Search references and regress before deletion. Preserve upstream examples and required licenses. Keep temporary files under `.codex/`; never commit caches, logs, private IDE configuration, or secrets.

<a id="validation"></a>

## 8. Testing and debugging

Run from the repository root:

```powershell
python -B -m unittest discover -s bridge/tests -v
python -X pycache_prefix=.codex/pycache -m compileall -q bridge
node --check bridge/web/app.js
node --check bridge/web/i18n.js
node --check bridge/web/markdown.js
```

Add the firmware build from section 2 only for firmware or related dependency changes. According to impact, check dashboard interaction/console, isolated host startup/exit, bundle resources/versions/hashes, secrets, and licenses. Then validate affected real services, networks, permissions, flashing, display, and power behavior. Do not mechanically repeat unaffected device checks.

Debug in this order: `/health` → dashboard overview → module settings/snapshots → device link. A serial port has one owner; external monitors can block sync/flashing. Watch uptime during reconnects so a reset is not mistaken for recovery.

`.github/workflows/validate.yml` covers shared tests on both platforms, Windows packaging-script parsing, and Mac arm64 backend/host builds. It does not exercise real provisioning, flashing, or permissions. Record source checks, app execution, real services, devices, other computers, and minimum OS testing separately. Assess both platforms for shared changes and mark untested platforms explicitly.

<a id="windows-development"></a>

## 9. Windows development and packaging

### Run from source

Use an English source path where possible. Create an isolated environment and start the tray host:

```powershell
python -m venv .codex/venv
.\.codex\venv\Scripts\python.exe -m pip install --cache-dir .codex/pip-cache -r bridge/requirements.txt
.\.codex\venv\Scripts\python.exe -B bridge/bridge_app.py --open-dashboard
```

Run section 8 checks with the same environment’s Python. You can also launch `bridge/启动 Dotii 管理中心.vbs` without an activated ESP-IDF terminal. Large `tools/` binaries are not tracked. Extract that directory from the current portable package to reproduce pinned tools, or use existing system fallbacks. Preserve tool licenses.

### Platform behavior

Keep Windows 10/11 x64 registry, PowerShell/CIM, `COMx`, `.exe`, and pairing recovery inside the platform boundary. The host owns single-instance and backend lifecycle management. The login key is `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\DotiiManagementCenter`.

Source login startup uses absolute paths to `pythonw.exe -B bridge_app.py --startup`; packaged startup uses adjacent `DotiiManagementCenter.exe --startup`, never `DotiiBridge.exe`. The release repairs stale/moved paths; source mode does not migrate an installed app’s startup entry.

If a stale pairing after reset causes `Could not start notify ... Unreachable`, remove it automatically and retry once only. After successful provisioning, retain `restarting` for a matching system cancellation error only within 30 seconds, ending on the first successful read. Report actual cancellation, denied permissions, and other errors normally.

Find esptool through the current Python, IDF environments, and standard official locations. Require `targets/stub_flasher/*/esp32s3.json`. Do not apply Windows recovery logic on Mac.

### Packaging

Pass the relevant checks, then run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\build_windows.ps1 -Clean
```

Prefer `.codex/build/flasher_args.json`; without a new build, validate and reuse `firmware/`. Explicitly reuse with `-FirmwareBuildDirectory firmware`, or choose separate output with `-OutputDirectory release/my-build`. Existing directories/ZIPs are not overwritten; `-Clean` removes intermediate packaging files only.

Derive the version from firmware and match both EXEs, Management Center, and firmware. Verify payload hashes, ZIP, dashboard/tray/backend exit, tools, and licenses. Regress affected login startup, firewall, serial, BLE, and real modules. For Inno Setup, use `packaging/DotiiManagementCenter.iss` with `/DAppVersion=<version>`.

Keep `collect_data_files("esptool")` in `DotiiBridge.spec`. The backend uses a console bootloader launched hidden through `CREATE_NO_WINDOW`, allowing flashing progress to be captured.

<a id="macos-development"></a>

## 10. macOS development and packaging

### Platform behavior

The target is Apple Silicon arm64, macOS 13.5+. Keep AppKit, CoreBluetooth, `/dev/cu.*`, `SMAppService`, signing, and DMG handling within the Mac boundary. Finder launches must not rely on interactive-shell PATH or silently install tools.

The host uses `SMAppService.mainApp` for login startup; Python must not write LaunchAgents. It manages single instances, health checks, bounded retries, and exit cleanup. A second instance must not remove the primary PID file; the backend must exit if its host crashes.

Native menus follow the language in `connection.json` when reopened. English/Chinese `InfoPlist.strings` provide permission descriptions. Prefer bundled private tools before safe system fallbacks.

Use only `/dev/cu.*`. Assert DTR before opening a Mac data session, then release RTS and DTR after opening to avoid ESP32 reset combinations. Keep the existing Windows signal order.

Store CoreBluetooth stable UUIDs only in restricted local settings. Let encrypted GATT access trigger system pairing; do not call explicit `pair()` or Windows `unpair()`. Distinguish undecided/allowed/denied permissions, Bluetooth off, unavailable hardware, and timeouts. Include Bluetooth/local-network usage descriptions in the plist. For stale bonds, guide users to forget Dotii in System Settings and scan again.

### Prepare and build

Full Xcode and Command Line Tools are required. Scripts prepare pinned Python 3.12.14, arm64 tools, and dependencies. Check downloads, versions, and SHA-256 before initial preparation or upgrades:

```bash
bash packaging/macos/prepare_python.sh
bash packaging/macos/prepare_tools.sh
bash packaging/macos/build_macos.sh --tools-dir .codex/macos-tools --dmg
```

`verify_bundle.py` checks web resources, firmware, tools, licenses, and the architecture/minimum OS of all nested Mach-O files. Node requires 13.5; no dependency may exceed the app’s declared minimum. The optional zsh resource in CLI 0.151.0 requires macOS 15, so packaging removes it from the copy while retaining the read-only App Server executable and licenses.

Candidates go to `.codex/macos-output/`, then `release/` after validation. A complete App includes `Contents/Resources/DotiiBridgeRuntime/` and `tools/`; the Xcode host alone is not a complete deliverable. Do not distribute candidates with missing resources, mixed architectures, or failed verification.

### Backend debugging, signing, and checks

Use an isolated runtime and nonproduction port without taking over an active USB session:

```bash
export DOTII_RUNTIME_DIR="$PWD/.codex/macos-runtime"
.codex/macos-python/bin/python3 -B bridge/codex_bridge.py --development --host 127.0.0.1 --port 18787
```

Keep external modules off initially and check `http://127.0.0.1:18787/health`, web resources, persistence, and exit. Backend-only testing does not validate native permission attribution, menus, or login startup.

Without `--sign`, signing is ad-hoc. With a Developer ID and keychain profile, add `--sign "<Developer ID Application identity>" --notary-profile "<keychain profile>" --dmg`. Scripts submit for notarization, staple, and run codesign/spctl checks. Keep credentials out of source; ad-hoc signing is not notarization.

In addition to shared checks, validate affected shell scripts, plists, native host, signing, read-only DMG mounting, resource/firmware hashes, and bundled secrets. Test USB/BLE, LAN, Codex/Bambu, logout/login, Mac sleep, other Macs, fresh permissions, and minimum OS separately. One machine does not establish broader compatibility. See the README for release limitations.

Icons live in the Xcode project’s `Assets.xcassets/` and derive from `bridge/web/assets/dotii.svg`. After changes, run:

```bash
swift packaging/macos/generate_icons.swift
```

## 11. Third-party licensing and maintenance

Use lock files and platform preparation scripts for dependency versions. Retain applicable licenses, copyright notices, and package metadata for ESP-IDF components, the local Waveshare BSP, Python, Node/npm, Codex CLI, FFmpeg, PyInstaller, Bleak, esptool, Pillow, and other dependencies. Do not discard legally required files to simplify directories.

Keep each platform’s FFmpeg build under its LGPL configuration; do not mix in GPL-only components. After upgrades or trimming, recheck architecture, protocols, host startup, licensing, and bundle integrity. Upstream Markdown licenses may be renamed to `.txt` only with equivalent contents.
