import AppKit
import Darwin
import Foundation
import ServiceManagement

private func runtimeURL() -> URL {
    if let override = ProcessInfo.processInfo.environment["DOTII_RUNTIME_DIR"], override.hasPrefix("/") {
        return URL(fileURLWithPath: override, isDirectory: true)
    }
    return FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("Dotii", isDirectory: true)
}

// The web app persists the application language here. Read it when presenting
// native UI as well, including before the backend has finished starting.
private func text(_ chinese: String, _ english: String) -> String {
    let url = runtimeURL().appendingPathComponent("connection.json")
    guard let data = try? Data(contentsOf: url),
          let settings = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          settings["language"] as? String == "en" else { return chinese }
    return english
}

private var appName: String { text("Dotii 管理中心", "Dotii Management Center") }
private let dashboardPort = Int(ProcessInfo.processInfo.environment["DOTII_PORT"] ?? "") ?? 8787
private let dashboardURL = URL(string: "http://127.0.0.1:\(dashboardPort)")!

private func loginItemCommand() -> Int32? {
    let arguments = CommandLine.arguments
    guard arguments.count == 3, arguments[1] == "--login-item" else { return nil }
    guard #available(macOS 13.0, *) else {
        FileHandle.standardError.write(Data((text("需要 macOS 13 或更高版本", "macOS 13 or later is required") + "\n").utf8))
        return 2
    }

    let service = SMAppService.mainApp
    do {
        switch arguments[2] {
        case "status":
            if service.status == .enabled {
                print("enabled")
                return 0
            }
            print("disabled")
            return 3
        case "enable":
            if service.status != .enabled { try service.register() }
            if service.status == .requiresApproval {
                FileHandle.standardError.write(Data((text("请在系统设置的登录项中批准 Dotii 管理中心", "Approve Dotii Management Center in System Settings > Login Items") + "\n").utf8))
                return 3
            }
            let enabled = service.status == .enabled
            print(enabled ? "enabled" : "disabled")
            return enabled ? 0 : 3
        case "disable":
            if service.status == .enabled { try service.unregister() }
            print("disabled")
            return 0
        default:
            FileHandle.standardError.write(Data((text("未知登录启动操作", "Unknown login item action") + "\n").utf8))
            return 2
        }
    } catch {
        FileHandle.standardError.write(Data("\(error.localizedDescription)\n".utf8))
        return 1
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem?
    private var statusMenuItem: NSMenuItem?
    private var loginMenuItem: NSMenuItem?
    private var bridgeProcess: Process?
    private var logHandle: FileHandle?
    private var lockDescriptor: Int32 = -1
    private var ownsInstanceLock = false
    private var statusText = ("正在启动后台服务", "Starting background service")
    private var shuttingDown = false
    private var restartAttempts = 0

    private lazy var runtimeDirectory = runtimeURL()

    private var logURL: URL { runtimeDirectory.appendingPathComponent("bridge.log") }
    private var hostPIDURL: URL { runtimeDirectory.appendingPathComponent("management-center.pid") }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        do {
            try FileManager.default.createDirectory(
                at: runtimeDirectory,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            guard acquireSingleInstanceLock() else {
                NSWorkspace.shared.open(dashboardURL)
                NSApp.terminate(nil)
                return
            }
            try String(ProcessInfo.processInfo.processIdentifier).write(
                to: hostPIDURL,
                atomically: true,
                encoding: .ascii
            )
            createStatusMenu()
            launchBridge()
        } catch {
            showError(text("Dotii 管理中心无法启动", "Dotii Management Center could not start"), detail: error.localizedDescription)
            NSApp.terminate(nil)
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        shuttingDown = true
        guard ownsInstanceLock else { return }
        stopBridge()
        try? FileManager.default.removeItem(at: hostPIDURL)
        if lockDescriptor >= 0 {
            flock(lockDescriptor, LOCK_UN)
            close(lockDescriptor)
            lockDescriptor = -1
        }
    }

    private func acquireSingleInstanceLock() -> Bool {
        let lockURL = runtimeDirectory.appendingPathComponent("management-center.lock")
        lockDescriptor = open(lockURL.path, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
        guard lockDescriptor >= 0 else { return false }
        ownsInstanceLock = flock(lockDescriptor, LOCK_EX | LOCK_NB) == 0
        if !ownsInstanceLock {
            close(lockDescriptor)
            lockDescriptor = -1
        }
        return ownsInstanceLock
    }

    private func createStatusMenu() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = item.button {
            let dotiiImage = NSImage(named: "StatusBarIcon")
            dotiiImage?.isTemplate = true
            button.image = dotiiImage ?? NSImage(systemSymbolName: "display", accessibilityDescription: appName)
            button.image?.accessibilityDescription = appName
            button.toolTip = appName
        }
        let menu = NSMenu()
        menu.delegate = self
        let status = NSMenuItem(title: text(statusText.0, statusText.1), action: nil, keyEquivalent: "")
        status.isEnabled = false
        statusMenuItem = status
        menu.addItem(status)
        menu.addItem(.separator())
        menu.addItem(withTitle: "打开 Dotii 管理中心", action: #selector(openDashboard), keyEquivalent: "o").target = self
        menu.addItem(withTitle: "查看运行日志", action: #selector(openLog), keyEquivalent: "l").target = self
        let login = NSMenuItem(title: "登录时启动", action: #selector(toggleLoginItem), keyEquivalent: "")
        login.target = self
        loginMenuItem = login
        menu.addItem(login)
        menu.addItem(.separator())
        menu.addItem(withTitle: "退出 Dotii 管理中心", action: #selector(quit), keyEquivalent: "q").target = self
        item.menu = menu
        statusItem = item
        menuWillOpen(menu)
    }

    func menuWillOpen(_ menu: NSMenu) {
        let titles: [(Selector, String, String)] = [
            (#selector(openDashboard), "打开 Dotii 管理中心", "Open Dotii Management Center"),
            (#selector(openLog), "查看运行日志", "View Logs"),
            (#selector(toggleLoginItem), "登录时启动", "Launch at Login"),
            (#selector(quit), "退出 Dotii 管理中心", "Quit Dotii Management Center"),
        ]
        for (action, chinese, english) in titles {
            menu.items.first { $0.action == action }?.title = text(chinese, english)
        }
        statusMenuItem?.title = text(statusText.0, statusText.1)
        statusItem?.button?.toolTip = appName
        statusItem?.button?.image?.accessibilityDescription = appName
        refreshLoginItemState()
    }

    private func setStatus(_ chinese: String, _ english: String) {
        statusText = (chinese, english)
        statusMenuItem?.title = text(chinese, english)
    }

    private func rotateLog() throws {
        let manager = FileManager.default
        guard let attributes = try? manager.attributesOfItem(atPath: logURL.path),
              let size = attributes[.size] as? NSNumber,
              size.intValue >= 1_000_000 else { return }
        let backup = runtimeDirectory.appendingPathComponent("bridge.log.1")
        try? manager.removeItem(at: backup)
        try manager.moveItem(at: logURL, to: backup)
    }

    private func launchBridge() {
        do {
            try rotateLog()
            if !FileManager.default.fileExists(atPath: logURL.path) {
                FileManager.default.createFile(atPath: logURL.path, contents: nil)
            }
            let handle = try FileHandle(forWritingTo: logURL)
            try handle.seekToEnd()
            let executable = Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/DotiiBridgeRuntime/DotiiBridge")
            guard FileManager.default.isExecutableFile(atPath: executable.path) else {
                throw NSError(
                    domain: "DotiiManagementCenter",
                    code: 1,
                    userInfo: [NSLocalizedDescriptionKey: text("安装包缺少后台服务程序 DotiiBridge", "The application is missing the DotiiBridge background service")]
                )
            }

            let process = Process()
            process.executableURL = executable
            process.arguments = [
                "--app-server",
                // Dotii receives this Mac's LAN address during provisioning and
                // pulls its snapshot directly. Admin routes still reject
                // non-loopback clients in the bridge HTTP handler.
                "--host", "0.0.0.0",
                "--port", String(dashboardPort),
                "--parent-pid", String(ProcessInfo.processInfo.processIdentifier),
            ]
            process.currentDirectoryURL = Bundle.main.resourceURL
            process.standardInput = FileHandle.nullDevice
            process.standardOutput = handle
            process.standardError = handle
            process.terminationHandler = { [weak self, weak process] _ in
                DispatchQueue.main.async { self?.bridgeDidTerminate(process) }
            }
            try process.run()
            bridgeProcess = process
            logHandle = handle
            setStatus("正在启动后台服务", "Starting background service")
            waitForHealth(process: process)
        } catch {
            appendHostDiagnostic("后台服务启动失败：\(error.localizedDescription)")
            setStatus("后台服务启动失败", "Background service failed to start")
            showError(text("Dotii 管理中心未能启动", "Dotii Management Center failed to start"), detail: "\(error.localizedDescription)\n\n\(text("日志", "Log"))：\(logURL.path)")
        }
    }

    private func appendHostDiagnostic(_ message: String) {
        guard let data = "[host] \(message)\n".data(using: .utf8),
              let handle = try? FileHandle(forWritingTo: logURL) else { return }
        do {
            try handle.seekToEnd()
            try handle.write(contentsOf: data)
            try handle.close()
        } catch {
            try? handle.close()
        }
    }

    private func waitForHealth(process: Process) {
        // The first launch of an unsigned/ad-hoc development bundle can spend
        // noticeable time in macOS code validation before Python starts.
        let deadline = Date().addingTimeInterval(180)
        DispatchQueue.global(qos: .utility).async { [weak self, weak process] in
            while Date() < deadline, let process, process.isRunning {
                if let data = try? Data(contentsOf: dashboardURL.appendingPathComponent("health")),
                   let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   payload["ok"] as? Bool == true,
                   let parentPID = payload["parent_pid"] as? Int,
                   parentPID == ProcessInfo.processInfo.processIdentifier {
                    DispatchQueue.main.async {
                        guard let self, self.bridgeProcess === process, !self.shuttingDown else { return }
                        self.restartAttempts = 0
                        self.setStatus("管理中心在线", "Management Center Online")
                    }
                    return
                }
                Thread.sleep(forTimeInterval: 0.25)
            }
            DispatchQueue.main.async {
                guard let self, let process, !self.shuttingDown, self.bridgeProcess === process else { return }
                self.setStatus("后台服务未就绪", "Background service is not ready")
                self.showError(text("Dotii 管理中心未就绪", "Dotii Management Center is not ready"), detail: "\(text("请查看运行日志", "Please view the log"))：\n\(self.logURL.path)")
            }
        }
    }

    private func bridgeDidTerminate(_ process: Process?) {
        guard bridgeProcess === process else { return }
        bridgeProcess = nil
        try? logHandle?.close()
        logHandle = nil
        guard !shuttingDown else { return }
        setStatus("后台服务已停止", "Background service stopped")
        if restartAttempts < 3 {
            restartAttempts += 1
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in self?.launchBridge() }
        } else {
            showError(text("后台服务反复退出", "Background service keeps stopping"), detail: "\(text("请查看运行日志", "Please view the log"))：\n\(logURL.path)")
        }
    }

    private func stopBridge() {
        guard let process = bridgeProcess, process.isRunning else { return }
        process.terminate()
        let deadline = Date().addingTimeInterval(5)
        while process.isRunning && Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        }
        if process.isRunning { kill(process.processIdentifier, SIGKILL) }
        bridgeProcess = nil
        try? logHandle?.close()
        logHandle = nil
    }

    private func refreshLoginItemState() {
        guard #available(macOS 13.0, *) else {
            loginMenuItem?.isEnabled = false
            return
        }
        let status = SMAppService.mainApp.status
        loginMenuItem?.state = status == .enabled ? .on : status == .requiresApproval ? .mixed : .off
        loginMenuItem?.title = status == .requiresApproval
            ? text("登录时启动（待系统批准）", "Launch at Login (Approval Required)")
            : text("登录时启动", "Launch at Login")
    }

    @objc private func openDashboard() { NSWorkspace.shared.open(dashboardURL) }

    @objc private func openLog() {
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        NSWorkspace.shared.open(logURL)
    }

    @objc private func toggleLoginItem() {
        guard #available(macOS 13.0, *) else { return }
        do {
            let service = SMAppService.mainApp
            if service.status == .requiresApproval {
                SMAppService.openSystemSettingsLoginItems()
                return
            }
            if service.status == .enabled { try service.unregister() } else { try service.register() }
            refreshLoginItemState()
            if service.status == .requiresApproval { SMAppService.openSystemSettingsLoginItems() }
        } catch {
            showError(text("无法更新登录启动", "Could not update Launch at Login"), detail: error.localizedDescription)
        }
    }

    @objc private func quit() {
        shuttingDown = true
        NSApp.terminate(nil)
    }

    private func showError(_ message: String, detail: String) {
        let alert = NSAlert()
        alert.messageText = message
        alert.informativeText = detail
        alert.alertStyle = .warning
        alert.addButton(withTitle: text("好", "OK"))
        alert.runModal()
    }
}

if let exitCode = loginItemCommand() { exit(exitCode) }

let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.run()
