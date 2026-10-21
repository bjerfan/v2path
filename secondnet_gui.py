"""V2Path 3.2: Qt Quick / Direct3D frontend for the preserved VPN engine."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer, QUrl, Qt, QAbstractListModel, QModelIndex, qInstallMessageHandler
from PySide6.QtGui import QAction, QColor, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox, QSystemTrayIcon, QMenu
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

import secondnet
import legacy_engine as legacy
import windows_taskbar

VERSION = "3.2.1"
legacy.GUI_VERSION = VERSION
ASSETS_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets"
PREVIOUS_DEFAULT_TARGETS = legacy._default_discord_targets()
# New per-app defaults no longer automatically add Discord's updater.
legacy._default_discord_targets = lambda: "Discord.exe"
QT_MESSAGES: list[str] = []


def initial_processes(settings: dict[str, object]) -> str:
    value = str(settings.get("routing_processes", settings.get("tunnel_processes", "Discord.exe")))
    normalized = lambda text: {part.strip().strip('"').replace("/", "\\").casefold() for part in text.split(",") if part.strip()}
    if not value.strip() or normalized(value) == normalized(PREVIOUS_DEFAULT_TARGETS):
        return "Discord.exe"
    return value


def qt_message(_kind: object, context: object, message: str) -> None:
    line = f"{getattr(context, 'category', '')}: {message}"
    if len(QT_MESSAGES) < 400:
        QT_MESSAGES.append(line)
    if sys.stderr is not None:
        print(line, file=sys.stderr)


class Dialogs:
    @staticmethod
    def showerror(title: str, text: str, **_kwargs: object) -> None:
        QMessageBox.critical(None, title, text.replace("Discord per-app", "Per-App"))

    @staticmethod
    def showinfo(title: str, text: str, **_kwargs: object) -> None:
        QMessageBox.information(None, title, text.replace("Discord per-app", "Per-App"))

    @staticmethod
    def askyesno(title: str, text: str, **_kwargs: object) -> bool:
        return QMessageBox.question(None, title, text.replace("Discord per-app", "Per-App"), QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes


class Files:
    @staticmethod
    def askopenfilename(**kwargs: object) -> str:
        groups = kwargs.get("filetypes", [])
        filters = ";;".join(f"{label} ({pattern})" for label, pattern in groups)
        return QFileDialog.getOpenFileName(None, str(kwargs.get("title", "Select a file")), "", filters)[0]


# Operational methods keep their existing validation and prompts; dialogs use Qt.
legacy.messagebox = Dialogs
legacy.filedialog = Files


class Value:
    """Small value adapter for the existing handlers, without creating Tk widgets."""
    def __init__(self, changed: object, value: str = "") -> None:
        self.value = value
        self.options: dict[str, object] = {}
        self.changed = changed

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        if self.value != str(value):
            self.value = str(value)
            self.changed()

    def configure(self, **kwargs: object) -> None:
        if any(self.options.get(k) != v for k, v in kwargs.items()):
            self.options.update(kwargs)
            self.changed()

    def delete(self, *_args: object) -> None:
        self.set("")

    def insert(self, _index: object, value: str) -> None:
        self.set(value)


class Rows(QAbstractListModel):
    def __init__(self, roles: list[str], parent: QObject) -> None:
        super().__init__(parent)
        self.roles = {Qt.UserRole + i + 1: name.encode() for i, name in enumerate(roles)}
        self.rows: list[dict[str, object]] = []

    def roleNames(self) -> dict[int, bytes]:
        return self.roles

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index: QModelIndex, role: int) -> object:
        if index.isValid() and 0 <= index.row() < len(self.rows) and role in self.roles:
            return self.rows[index.row()].get(self.roles[role].decode())
        return None

    def replace(self, rows: list[dict[str, object]]) -> None:
        if len(rows) == len(self.rows) and all(a.get("profileId") == b.get("profileId") for a, b in zip(rows, self.rows)):
            for i, row in enumerate(rows):
                if self.rows[i] != row:
                    self.rows[i] = row
                    self.dataChanged.emit(self.index(i), self.index(i), list(self.roles))
            return
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def append(self, lines: list[str]) -> None:
        if not lines:
            return
        self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows) + len(lines) - 1)
        self.rows.extend({"lineText": line.rstrip()} for line in lines)
        self.endInsertRows()
        if len(self.rows) > 5000:
            count = len(self.rows) - 4000
            self.beginRemoveRows(QModelIndex(), 0, count - 1)
            del self.rows[:count]
            self.endRemoveRows()


class Tray:
    def __init__(self, owner: "Controller") -> None:
        self.icon = QSystemTrayIcon(owner.icon, owner)
        self.menu = QMenu()
        self.menu.addAction("Open V2Path", owner._show_window)
        self.start = self.menu.addAction("Connect", owner._start)
        self.stop_action = self.menu.addAction("Disconnect", owner._stop)
        self.menu.addSeparator()
        self.menu.addAction("Exit V2Path", owner._exit_application)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(lambda reason: owner._show_window() if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.icon.show()

    def notify(self, text: str, title: str = "V2Path") -> None:
        self.icon.showMessage(title, text)

    def stop(self) -> None:
        self.icon.hide()
        self.menu.close()


class Controller(QObject):
    changed = Signal()
    adaptersChanged = Signal()
    logUpdated = Signal()
    deferred = Signal(object, int)
    rendererReady = Signal(str)

    def __init__(self, application: QApplication, preview: bool = False) -> None:
        super().__init__()
        self.application = application
        self.preview = preview
        self.window: QQuickWindow | None = None
        self._pending_publish = False
        self._initializing = True
        self._adapter_values: list[str] = []
        self.renderer = "Initializing Direct3D 11…"
        self.closing = False
        self.window_hidden = False
        self.app_state = "idle"
        self.library = legacy.ConfigLibrary() if not preview else SimpleNamespace(records=[
            {"id": "sample1", "name": "Evening connection", "type": "link", "value": ""},
            {"id": "sample2", "name": "Backup configuration", "type": "file", "value": ""},
        ])
        self.settings = {} if preview else legacy._read_json(legacy.SETTINGS_PATH, {})
        if not isinstance(self.settings, dict):
            self.settings = {}
        self.adapters = []
        self.adapter_by_label = {}
        self.record_by_id = {}
        self.selected_config_id = ""
        self.events = queue.Queue()
        self.process = None
        self.direct_proxy_process = None
        self.filter_process = None
        self.direct_proxy_stop_expected = threading.Event()
        self.filter_stop_expected = threading.Event()
        self.process_lock = threading.Lock()
        self.stop_requested = threading.Event()
        try:
            self.child_job = secondnet.WindowsChildJob()
            self.child_job_error = ""
        except OSError as exc:
            self.child_job = None
            self.child_job_error = str(exc)
        self.profile_rows = Rows(["profileId", "profileName", "profileKind", "isSelected", "isEnabled"], self)
        self.log_rows = Rows(["lineText"], self)
        for name in ("adapter_combo", "adapter_detail", "config_detail", "remove_button", "refresh_button",
                     "process_entry", "process_button", "process_clear_button", "discord_launch_button",
                     "routing_detail", "mode_combo", "socks_entry", "direct_socks_entry"):
            setattr(self, name, Value(self.schedule_publish))
        self.socks_entry.set(str(self.settings.get("socks_port", 10808)))
        self.direct_socks_entry.set(str(self.settings.get("direct_socks_port", self.settings.get("http_port", 10809))))
        self.process_entry.set(initial_processes(self.settings))
        mode = str(self.settings.get("routing_mode", legacy.MODE_DISCORD))
        self.mode_combo.set(mode if mode in (legacy.MODE_PROXY, legacy.MODE_DISCORD, legacy.MODE_TUN) else legacy.MODE_DISCORD)
        self._routing_mode_changed(self.mode_combo.get())
        self._refresh_config_choices()
        self.tray_icon = None
        self.icon = QIcon(str(ASSETS_DIR / "secondnet.ico"))
        self.deferred.connect(self._defer, Qt.QueuedConnection)
        self.rendererReady.connect(self._renderer_ready, Qt.QueuedConnection)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._poll_events)
        self.timer.start()
        self._initializing = False
        if preview:
            adapter = {"name": "Wi-Fi", "status": "Up", "ipv4": ["192.168.1.20"], "gateways": ["192.168.1.1"], "physical": True}
            self.adapter_by_label["Wi-Fi · 192.168.1.20"] = adapter
            self.adapter_combo.configure(values=list(self.adapter_by_label))
            self.adapter_combo.set("Wi-Fi · 192.168.1.20")
            self._adapter_changed(self.adapter_combo.get(), persist=False)
            self._append_log("GPU UI preview. No VPN engines, routes, or drivers have been started.")
        else:
            self.tray_icon = Tray(self)
            self._refresh_adapters()
        self._set_state("idle")

    @Property("QVariantMap", notify=changed)
    def view(self) -> dict[str, object]:
        record = self._selected_config()
        adapter = self.adapter_by_label.get(self.adapter_combo.get())
        try:
            index = self._adapter_values.index(self.adapter_combo.get())
        except ValueError:
            index = -1
        return {
            "version": VERSION, "state": self.app_state,
            "adapterIndex": index, "adapterDetail": self.adapter_detail.options.get("text", "Choose an internet interface."),
            "adapterName": str(adapter.get("name")) if adapter else "No interface selected",
            "configName": record["name"] if record else "No profile selected",
            "configDetail": self.config_detail.options.get("text", ""),
            "profileCount": len(self.library.records), "mode": self.mode_combo.get(),
            "modeLabel": "Per-App" if self.mode_combo.get() == legacy.MODE_DISCORD else self.mode_combo.get(),
            "processes": self.process_entry.get(), "socksPort": self.socks_entry.get(),
            "directPort": self.direct_socks_entry.get(),
            "routingDetail": self.routing_detail.options.get("text", ""),
            "renderer": self.renderer, "animations": legacy.LiquidPowerButton._animations_allowed(),
            "refreshEnabled": self.refresh_button.options.get("state") != "disabled",
        }

    @Property("QStringList", notify=adaptersChanged)
    def adapterLabels(self) -> list[str]:
        return self._adapter_values

    def schedule_publish(self) -> None:
        if self._initializing or self._pending_publish:
            return
        self._pending_publish = True
        QTimer.singleShot(0, self._publish)

    def _publish(self) -> None:
        self._pending_publish = False
        values = list(self.adapter_combo.options.get("values", []))
        if values != self._adapter_values:
            self._adapter_values = values
            self.adaptersChanged.emit()
        self.changed.emit()

    def after(self, milliseconds: int, callback: object) -> None:
        self.deferred.emit(callback, milliseconds)

    @Slot(object, int)
    def _defer(self, callback: object, milliseconds: int) -> None:
        QTimer.singleShot(milliseconds, callback)

    @Slot(str)
    def _renderer_ready(self, api: str) -> None:
        self.renderer = api
        self.schedule_publish()

    def _render_config_list(self) -> None:
        self.profile_rows.replace([
            {"profileId": r["id"], "profileName": r["name"],
             "profileKind": "JSON configuration" if r["type"] == "file" else "VPN share link",
             "isSelected": r["id"] == self.selected_config_id, "isEnabled": self.app_state == "idle"}
            for r in self.library.records
        ])
        self.schedule_publish()

    def _update_summary(self) -> None:
        self.schedule_publish()

    def _set_state(self, state: str) -> None:
        self.app_state = state
        self._render_config_list()
        self._refresh_tray()
        self.schedule_publish()

    def _append_log(self, text: str) -> None:
        self._append_logs([text])

    def _append_logs(self, lines: list[str]) -> None:
        self.log_rows.append(["Mode: Per-App" if line == "Mode: Discord per-app" else line for line in lines])
        if lines:
            self.logUpdated.emit()

    def _clear_log(self) -> None:
        self.log_rows.beginResetModel()
        self.log_rows.rows = []
        self.log_rows.endResetModel()

    def _poll_events(self) -> None:
        logs = []
        for _ in range(500):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                logs.append(str(value))
            elif kind == "state":
                self._set_state(str(value))
                if value == "running":
                    logs.append("Connected. Local proxy listeners are ready.")
            elif kind == "exit":
                logs.append(f"Xray stopped unexpectedly with exit code {value}." if value and not self.stop_requested.is_set() else "V2Path stopped.")
                self._set_state("idle")
            elif kind == "error":
                logs.append(f"ERROR: {value}")
                if not self.closing:
                    if self.window_hidden:
                        if self.tray_icon:
                            self.tray_icon.notify(str(value), "V2Path error")
                        self._show_window()
                    Dialogs.showerror("V2Path error", str(value))
            elif kind == "restart_discord" and not self.closing and self.app_state == "running":
                self._restart_discord_direct(ask=False)
            elif kind == "adapters":
                self._apply_adapters(value)
        self._append_logs(logs)

    def _refresh_tray(self) -> None:
        if self.tray_icon:
            self.tray_icon.start.setEnabled(self.app_state == "idle")
            self.tray_icon.stop_action.setEnabled(self.app_state in {"starting", "running"})
            self.tray_icon.icon.setToolTip(f"V2Path {VERSION} · {self.app_state}")

    def _show_window(self) -> None:
        if self.window and not self.closing:
            self.window_hidden = False
            self.window.showNormal()
            self.window.raise_()
            self.window.requestActivate()

    def _hide_to_tray(self) -> None:
        if self.window and not self.closing:
            self.window_hidden = True
            self.window.hide()
            if self.tray_icon:
                self.tray_icon.notify("V2Path is still running. Use the tray menu to reopen or exit.")

    def destroy(self) -> None:
        self.timer.stop()
        self.application.quit()

    def _add_link(self) -> None:
        link, accepted = QInputDialog.getText(None, "Add VPN share link", "Paste a VLESS, VMess, or Trojan link:")
        if accepted and link.strip():
            try:
                record = self.library.add_link(link)
                self._refresh_config_choices(record["id"])
                self._append_log(f"Added share link: {record['name']}")
            except Exception as exc:
                Dialogs.showerror("Could not add share link", str(exc))

    def _restart_elevated(self) -> bool:
        arguments = ["--replace-existing"]
        if not getattr(sys, "frozen", False):
            arguments.insert(0, str(Path(__file__).resolve()))
        return int(ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, subprocess.list2cmdline(arguments), str(secondnet.APP_DIR), 1,
        )) > 32

    @Slot(str, str)
    def setField(self, name: str, value: str) -> None:
        if self.app_state != "idle":
            return
        field = {"processes": self.process_entry, "socksPort": self.socks_entry, "directPort": self.direct_socks_entry}.get(name)
        if field:
            field.set(value)
        elif name == "adapter" and value in self.adapter_by_label:
            self.adapter_combo.set(value)
            self._adapter_changed(value, persist=not self.preview)
        elif name == "mode" and value in (legacy.MODE_PROXY, legacy.MODE_DISCORD, legacy.MODE_TUN):
            self.mode_combo.set(value)
            self._routing_mode_changed(value)

    @Slot(str)
    def selectProfile(self, profile_id: str) -> None:
        if self.app_state == "idle":
            self._select_config(profile_id, persist=not self.preview)

    @Slot(str)
    def action(self, name: str) -> None:
        if name == "toggle":
            if self.preview:
                self._set_state("running" if self.app_state == "idle" else "idle")
            else:
                self._toggle_connection()
            return
        if name == "hide":
            self._hide_to_tray()
            return
        if name == "clearLog":
            self._clear_log()
            return
        if self.preview:
            return
        if name == "restartDiscord" and self.app_state == "running" and self.mode_combo.get() == legacy.MODE_DISCORD:
            self._restart_discord_direct()
            return
        if self.app_state != "idle":
            return
        actions = {"refresh": self._refresh_adapters, "import": self._import_config, "link": self._add_link,
                   "remove": self._remove_config, "browse": self._select_process, "clearApps": self._clear_processes}
        if name in actions:
            actions[name]()


# Bind the original functions rather than duplicating or rewriting network behavior.
UNCHANGED_METHODS = (
    "_load_adapters_worker", "_apply_adapters", "_adapter_changed", "_refresh_adapters",
    "_selected_config", "_profile_subtitle", "_refresh_config_choices", "_select_config",
    "_routing_mode_changed", "_find_discord_executable", "_restart_discord_direct", "_restart_discord_worker",
    "_select_process", "_is_admin", "_ensure_routing_prerequisites", "_import_config", "_remove_config",
    "_ports", "_start", "_start_worker", "_watch_direct_proxy", "_watch_filter", "_terminate_process",
    "_terminate_children", "_stop", "_stop_worker", "_clear_processes", "_toggle_connection", "_exit_application",
)
for method in UNCHANGED_METHODS:
    setattr(Controller, method, legacy.V2PathGUI.__dict__[method])


def main() -> int:
    sys.modules.setdefault("secondnet_gui", sys.modules[__name__])
    preview = "--preview" in sys.argv or "--smoke-test" in sys.argv
    guard = None
    if not preview:
        guard = legacy.SingleInstanceGuard()
        if not guard.primary:
            replacing = "--replace-existing" in sys.argv
            signaled = guard.request_replacement() if replacing else guard.activate_existing()
            guard.close()
            if not replacing:
                return 0 if signaled else 1
            if not signaled:
                return 1
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                guard = legacy.SingleInstanceGuard()
                if guard.primary:
                    break
                guard.close()
                time.sleep(0.1)
            else:
                return 1
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    os.environ.setdefault("QSG_INFO", "1")
    os.environ.setdefault("QSG_RENDER_LOOP", "threaded")
    qInstallMessageHandler(qt_message)
    QQuickWindow.setGraphicsApi(QSGRendererInterface.Direct3D11)
    windows_taskbar.set_process_identity()
    app = QApplication(sys.argv)
    app.setApplicationName("V2Path")
    app.setApplicationVersion(VERSION)
    app.setQuitOnLastWindowClosed(False)
    controller = Controller(app, preview)
    qml = QQmlApplicationEngine()
    qml.rootContext().setContextProperty("bridge", controller)
    qml.rootContext().setContextProperty("profileModel", controller.profile_rows)
    qml.rootContext().setContextProperty("logModel", controller.log_rows)
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    qml.load(QUrl.fromLocalFile(str(root / "qml" / "Main.qml")))
    if not qml.rootObjects():
        return 2
    window = qml.rootObjects()[0]
    controller.window = window
    app.setWindowIcon(controller.icon)
    window.setIcon(controller.icon)
    windows_taskbar.set_window_identity(int(window.winId()), ASSETS_DIR / "secondnet.ico", Path(__file__))
    window.sceneGraphInitialized.connect(lambda: controller.rendererReady.emit(window.rendererInterface().graphicsApi().name), Qt.DirectConnection)
    if guard:
        guard.watch(lambda: controller.after(0, controller._show_window), lambda: controller.after(0, controller._exit_application))
    window.show()
    if "--smoke-test" in sys.argv:
        from gpu_smoke import schedule_checks
        schedule_checks(app, controller, window)
    result = app.exec()
    if guard:
        guard.close()
    if controller.child_job:
        controller.child_job.close()
    return result


if __name__ == "__main__":
    raise SystemExit(main())