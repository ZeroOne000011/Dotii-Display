#!/usr/bin/env python3
"""Windowless Windows host with a native notification-area icon."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from ctypes import wintypes
from pathlib import Path

from runtime_paths import application_root, is_frozen, resource_path, sibling_executable


APP_NAME = "Dotii 管理中心"
MUTEX_NAME = "Local\\StateDisplayBridgeApp"
DASHBOARD_URL = "http://127.0.0.1:8787"
ADMIN_URL = f"{DASHBOARD_URL}/api/v1/admin/overview"
STARTUP_DELAY_SECONDS = 8.0
STARTUP_READY_TIMEOUT_SECONDS = 45.0
ICON_PATH = resource_path("assets", "dotii.ico")
BRIDGE_EXECUTABLE_NAME = "DotiiBridge.exe"
WM_APP = 0x8000
WM_TRAY = WM_APP + 21
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_COMMAND = 0x0111
WM_DESTROY = 0x0002
WM_NULL = 0x0000
NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080
MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
IDI_APPLICATION = 32512
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
CMD_OPEN = 1001
CMD_LOG = 1002
CMD_EXIT = 1003
ERROR_ALREADY_EXISTS = 183
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
PROCESS_PER_MONITOR_DPI_AWARE = 2


if os.name == "nt":
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", ctypes.c_wchar * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", ctypes.c_wchar * 256),
            ("uTimeoutOrVersion", wintypes.UINT),
            ("szInfoTitle", ctypes.c_wchar * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", wintypes.HICON),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


def runtime_folder() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    return (Path(local_app_data) if local_app_data else Path.home() / ".state-display") / "StateDisplay"


def attach_kill_on_close_job(process: subprocess.Popen[bytes]) -> int:
    """Tie the bridge child to this tray process, including abrupt tray termination."""
    if os.name != "nt":
        return 0
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(kernel32.GetLastError())
    limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        job,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    ):
        error = kernel32.GetLastError()
        kernel32.CloseHandle(job)
        raise ctypes.WinError(error)
    if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(process._handle)):
        error = kernel32.GetLastError()
        kernel32.CloseHandle(job)
        raise ctypes.WinError(error)
    return int(job)


def rotate_log(path: Path, maximum: int = 1_000_000) -> None:
    if not path.is_file() or path.stat().st_size < maximum:
        return
    backup = path.with_suffix(".log.1")
    if backup.exists():
        backup.unlink()
    path.replace(backup)


def read_admin(timeout: float = 0.5) -> dict[str, object] | None:
    try:
        with urllib.request.urlopen(ADMIN_URL, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def wait_until_ready(process: subprocess.Popen[bytes], timeout: float = 12.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        overview = read_admin()
        bridge = overview.get("bridge") if overview else None
        if isinstance(bridge, dict) and bridge.get("online") is True:
            return True
        time.sleep(0.2)
    return False


class TrayHost:
    def __init__(self, process: subprocess.Popen[bytes], log_path: Path) -> None:
        self.process = process
        self.log_path = log_path
        self.user32 = ctypes.windll.user32
        self.shell32 = ctypes.windll.shell32
        self.kernel32 = ctypes.windll.kernel32
        self._configure_win32()
        self.class_name = "StateDisplayBridgeTrayWindow"
        self.hwnd: int | None = None
        self.icon_data = NOTIFYICONDATAW()
        self._wndproc = WNDPROC(self._window_proc)

    def _configure_win32(self) -> None:
        """Declare pointer-sized Win32 signatures so 64-bit menu handles are not truncated."""
        hmenu = wintypes.HANDLE
        uint_ptr = ctypes.c_size_t
        self.user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self.user32.GetCursorPos.restype = wintypes.BOOL
        self.user32.CreatePopupMenu.argtypes = []
        self.user32.CreatePopupMenu.restype = hmenu
        self.user32.AppendMenuW.argtypes = [hmenu, wintypes.UINT, uint_ptr, wintypes.LPCWSTR]
        self.user32.AppendMenuW.restype = wintypes.BOOL
        self.user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user32.SetForegroundWindow.restype = wintypes.BOOL
        self.user32.TrackPopupMenu.argtypes = [
            hmenu, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, ctypes.c_void_p,
        ]
        self.user32.TrackPopupMenu.restype = wintypes.UINT
        self.user32.DestroyMenu.argtypes = [hmenu]
        self.user32.DestroyMenu.restype = wintypes.BOOL
        self.user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.PostMessageW.restype = wintypes.BOOL
        self.user32.DestroyWindow.argtypes = [wintypes.HWND]
        self.user32.DestroyWindow.restype = wintypes.BOOL
        self.user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.DefWindowProcW.restype = LRESULT
        self.user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        self.user32.RegisterClassW.restype = wintypes.ATOM
        self.user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, hmenu, wintypes.HINSTANCE, ctypes.c_void_p,
        ]
        self.user32.CreateWindowExW.restype = wintypes.HWND
        self.user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
        self.user32.LoadIconW.restype = wintypes.HICON
        self.user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        self.user32.LoadImageW.restype = wintypes.HANDLE
        self.user32.DestroyIcon.argtypes = [wintypes.HICON]
        self.user32.DestroyIcon.restype = wintypes.BOOL
        self.shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        self.shell32.Shell_NotifyIconW.restype = wintypes.BOOL
        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    def _open_dashboard(self) -> None:
        webbrowser.open(DASHBOARD_URL)

    def _open_log(self) -> None:
        try:
            os.startfile(self.log_path)  # type: ignore[attr-defined]
        except OSError:
            self.user32.MessageBoxW(self.hwnd, str(self.log_path), "日志文件", 0x40)

    def _load_icon(self) -> tuple[int, bool]:
        icon = self.user32.LoadImageW(
            None,
            str(ICON_PATH),
            IMAGE_ICON,
            0,
            0,
            LR_LOADFROMFILE | LR_DEFAULTSIZE,
        )
        if icon:
            return int(icon), True
        fallback = self.user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
        if not fallback:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(fallback), False

    def _create_menu(self) -> int:
        menu = self.user32.CreatePopupMenu()
        if not menu:
            raise ctypes.WinError(ctypes.get_last_error())
        entries = (
            (MF_STRING, CMD_OPEN, "打开 Dotii 管理中心"),
            (MF_STRING, CMD_LOG, "查看运行日志"),
            (MF_SEPARATOR, 0, None),
            (MF_STRING, CMD_EXIT, "退出 Dotii 管理中心"),
        )
        for flags, command_id, label in entries:
            if not self.user32.AppendMenuW(menu, flags, command_id, label):
                self.user32.DestroyMenu(menu)
                raise ctypes.WinError(ctypes.get_last_error())
        return menu

    def _show_menu(self) -> None:
        point = wintypes.POINT()
        self.user32.GetCursorPos(ctypes.byref(point))
        menu = self._create_menu()
        self.user32.SetForegroundWindow(self.hwnd)
        command = self.user32.TrackPopupMenu(
            menu,
            TPM_RETURNCMD | TPM_NONOTIFY,
            point.x,
            point.y,
            0,
            self.hwnd,
            None,
        )
        self.user32.DestroyMenu(menu)
        self.user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        if command == CMD_OPEN:
            self._open_dashboard()
        elif command == CMD_LOG:
            self._open_log()
        elif command == CMD_EXIT:
            self.user32.DestroyWindow(self.hwnd)

    def _window_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        if message == WM_TRAY:
            if lparam == WM_LBUTTONUP:
                self._open_dashboard()
            elif lparam == WM_RBUTTONUP:
                self._show_menu()
            return 0
        if message == WM_COMMAND:
            return 0
        if message == WM_DESTROY:
            self.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.icon_data))
            self.user32.PostQuitMessage(0)
            return 0
        return self.user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def run(self) -> None:
        instance = self.kernel32.GetModuleHandleW(None)
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = instance
        window_class.lpszClassName = self.class_name
        icon, owns_icon = self._load_icon()
        window_class.hIcon = icon
        if not self.user32.RegisterClassW(ctypes.byref(window_class)):
            error = ctypes.get_last_error()
            if error not in (0, 1410):
                raise ctypes.WinError(error)
        self.hwnd = self.user32.CreateWindowExW(
            0,
            self.class_name,
            APP_NAME,
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            instance,
            None,
        )
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

        self.icon_data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self.icon_data.hWnd = self.hwnd
        self.icon_data.uID = 1
        self.icon_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self.icon_data.uCallbackMessage = WM_TRAY
        self.icon_data.hIcon = window_class.hIcon
        self.icon_data.szTip = APP_NAME
        if not self.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.icon_data)):
            raise ctypes.WinError(ctypes.get_last_error())

        try:
            message = wintypes.MSG()
            while self.user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                self.user32.TranslateMessage(ctypes.byref(message))
                self.user32.DispatchMessageW(ctypes.byref(message))
        finally:
            if owns_icon:
                self.user32.DestroyIcon(icon)


def enable_high_dpi() -> str:
    """Opt the tray UI into native-resolution rendering before USER32 creates it."""
    if os.name != "nt":
        return "unsupported"

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    context = ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)

    # Windows 10 1703+: best quality when the taskbar moves between monitors.
    try:
        set_process_context = user32.SetProcessDpiAwarenessContext
        set_process_context.argtypes = [ctypes.c_void_p]
        set_process_context.restype = wintypes.BOOL
        if set_process_context(context):
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass

    # If a host manifest already fixed the process mode, make this UI thread V2.
    try:
        set_thread_context = user32.SetThreadDpiAwarenessContext
        set_thread_context.argtypes = [ctypes.c_void_p]
        set_thread_context.restype = ctypes.c_void_p
        if set_thread_context(context):
            return "per-monitor-v2-thread"
    except (AttributeError, OSError):
        pass

    # Windows 8.1 fallback.
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        set_process_awareness = shcore.SetProcessDpiAwareness
        set_process_awareness.argtypes = [ctypes.c_int]
        set_process_awareness.restype = ctypes.c_long
        if set_process_awareness(PROCESS_PER_MONITOR_DPI_AWARE) == 0:
            return "per-monitor"
    except (AttributeError, OSError):
        pass

    # Vista/7 fallback: system-DPI aware is still sharper than bitmap scaling.
    try:
        set_process_aware = user32.SetProcessDPIAware
        set_process_aware.argtypes = []
        set_process_aware.restype = wintypes.BOOL
        if set_process_aware():
            return "system"
    except (AttributeError, OSError):
        pass
    return "unchanged"


def main() -> int:
    parser = argparse.ArgumentParser(description="Dotii background host")
    parser.add_argument("--open-dashboard", action="store_true")
    parser.add_argument("--startup", action="store_true", help="从 Windows 登录启动，等待后台服务就绪")
    arguments = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("bridge_app.py only supports Windows")
    if arguments.startup:
        # At login Windows may still be restoring the network, Bluetooth and
        # the user's Codex session. Give those services a short head start.
        time.sleep(STARTUP_DELAY_SECONDS)
    enable_high_dpi()

    kernel32 = ctypes.windll.kernel32
    kernel32.SetLastError(0)
    mutex = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        webbrowser.open(DASHBOARD_URL)
        kernel32.CloseHandle(mutex)
        return 0

    folder = runtime_folder()
    folder.mkdir(parents=True, exist_ok=True)
    log_path = folder / "bridge.log"
    rotate_log(log_path)
    log_handle = log_path.open("a", encoding="utf-8", buffering=1)
    script = Path(__file__).with_name("codex_bridge.py")
    if is_frozen():
        bridge_executable = sibling_executable(BRIDGE_EXECUTABLE_NAME)
        if not bridge_executable.is_file():
            ctypes.windll.user32.MessageBoxW(
                None,
                f"找不到后台服务程序：\n{bridge_executable}",
                APP_NAME,
                0x10,
            )
            return 1
        command = [str(bridge_executable), "--app-server"]
        working_root = application_root()
    else:
        command = [sys.executable, "-B", str(script), "--app-server"]
        working_root = script.parent.parent
    process = subprocess.Popen(
        command,
        cwd=working_root,
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    job_handle: int | None = None
    pid_path = folder / "bridge-app.pid"
    pid_path.write_text(str(os.getpid()), encoding="ascii")
    try:
        job_handle = attach_kill_on_close_job(process)
        ready_timeout = STARTUP_READY_TIMEOUT_SECONDS if arguments.startup else 12.0
        if not wait_until_ready(process, timeout=ready_timeout):
            if arguments.startup and process.poll() is None:
                # Do not show a hidden-session MessageBox or exit just because
                # login startup is slower than the ready probe. Keep the tray
                # host alive while the child finishes initializing.
                TrayHost(process, log_path).run()
                return 0
            ctypes.windll.user32.MessageBoxW(
                None,
                f"{APP_NAME} 未能启动。请查看日志：\n{log_path}",
                APP_NAME,
                0x10,
            )
            return 1
        if arguments.open_dashboard:
            webbrowser.open(DASHBOARD_URL)
        TrayHost(process, log_path).run()
        return 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        if job_handle:
            kernel32.CloseHandle(job_handle)
        log_handle.close()
        if pid_path.exists():
            pid_path.unlink()
        kernel32.ReleaseMutex(mutex)
        kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    raise SystemExit(main())
