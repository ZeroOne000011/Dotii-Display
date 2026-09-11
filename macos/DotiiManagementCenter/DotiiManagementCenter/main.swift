import AppKit
import Darwin
import Foundation
import ServiceManagement

private let appName = "Dotii 管理中心"
private let dashboardPort = Int(ProcessInfo.processInfo.environment["DOTII_PORT"] ?? "") ?? 8787
private let dashboardURL = URL(string: "http://127.0.0.1:\(dashboardPort)")!

private func loginItemCommand() -> Int32? {
    let arguments = CommandLine.arguments
    guard arguments.count == 3, arguments[1] == "--login-item" else { return nil }
    guard #available(macOS 13.0, *) else {
        FileHandle.standardError.write(Data("需要 macOS 13 或更高版本\n".utf8))
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
            print("enabled")
            return 0
        case "disable":
            if service.status == .enabled { try service.unregister() }
            print("disabled")
            return 0
        default:
            FileHandle.standardError.write(Data("未知登录启动操作\n".utf8))
            return 2
        }
    } catch {
        FileHandle.standardError.write(Data("\(error.localizedDescription)\n".utf8))
        return 1
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem?
    private var statusMenuItem: NSMenuItem?
    private var loginMenuItem: NSMenuItem?
    private var bridgeProcess: Process?
    private var logHandle: FileHandle?
    private var lockDescriptor: Int32 = -1
    private var shuttingDown = false
    private var restartAttempts = 0

    private lazy var runtimeDirectory: URL = {
        if let override = ProcessInfo.processInfo.environment["DOTII_RUNTIME_DIR"],
           override.hasPrefix("/") {
            return URL(fileURLWithPath: override, isDirectory: true)
        }
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return base.appendingPathComponent("Dotii", isDirectory: true)
    }()

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
            showError("\(appName) 无法启动", detail: error.localizedDescription)
            NSApp.terminate(nil)
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        shuttingDown = true
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
        return lockDescriptor >= 0 && flock(lockDescriptor, LOCK_EX | LOCK_NB) == 0
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
        let status = NSMenuItem(title: "正在启动后台服务", action: nil, keyEquivalent: "")
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
        refreshLoginItemState()
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
                    userInfo: [NSLocalizedDescriptionKey: "安装包缺少后台服务程序 DotiiBridge"]
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
            statusMenuItem?.title = "正在启动后台服务"
            waitForHealth(process: process)
        } catch {
            appendHostDiagnostic("后台服务启动失败：\(error.localizedDescription)")
            statusMenuItem?.title = "后台服务启动失败"
            showError("\(appName) 未能启动", detail: "\(error.localizedDescription)\n\n日志：\(logURL.path)")
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
                        self?.restartAttempts = 0
                        self?.statusMenuItem?.title = "管理中心在线"
                    }
                    return
                }
                Thread.sleep(forTimeInterval: 0.25)
            }
            DispatchQueue.main.async {
                guard let self, !self.shuttingDown else { return }
                self.statusMenuItem?.title = "后台服务未就绪"
                self.showError("\(appName) 未就绪", detail: "请查看运行日志：\n\(self.logURL.path)")
            }
        }
    }

    private func bridgeDidTerminate(_ process: Process?) {
        guard bridgeProcess === process else { return }
        bridgeProcess = nil
        try? logHandle?.close()
        logHandle = nil
        guard !shuttingDown else { return }
        statusMenuItem?.title = "后台服务已停止"
        if restartAttempts < 3 {
            restartAttempts += 1
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in self?.launchBridge() }
        } else {
            showError("后台服务反复退出", detail: "请查看运行日志：\n\(logURL.path)")
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
        loginMenuItem?.state = SMAppService.mainApp.status == .enabled ? .on : .off
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
            if service.status == .enabled { try service.unregister() } else { try service.register() }
            refreshLoginItemState()
        } catch {
            showError("无法更新登录启动", detail: error.localizedDescription)
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
        alert.runModal()
    }
}

if let exitCode = loginItemCommand() { exit(exitCode) }

let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.run()
