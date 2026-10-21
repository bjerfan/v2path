"""Modern Windows GUI for V2Path."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
import ctypes

import customtkinter as ctk
from PIL import Image, ImageDraw
import pystray
from tkinter import filedialog, messagebox

import secondnet


GUI_VERSION = "2.2.2"
DATA_DIR = secondnet.APP_DIR
CONFIG_DIR = DATA_DIR / "configs"
LIBRARY_PATH = CONFIG_DIR / "library.json"
SETTINGS_PATH = DATA_DIR / "runtime" / "gui-settings.json"
GENERATED_CONFIG = DATA_DIR / "runtime" / "gui-config.json"
VALIDATION_CONFIG = DATA_DIR / "runtime" / "gui-validation.json"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Discord-style dark palette: the app mostly sits beside Discord, so it should feel native there.
BG = "#111214"
GLASS = "#1A1B1E"
GLASS_SOFT = "#232428"
PANEL_ALT = "#2B2D31"
GLASS_HIGHLIGHT = "#35373C"
BORDER = "#3F4147"
TEXT = "#F2F3F5"
MUTED = "#B5BAC1"
SUBTLE = "#949BA4"
BLUE = "#5865F2"
BLUE_HOVER = "#4752C4"
BLUE_SOFT = "#3C4380"
BLUE_LIGHT = "#949CF7"
GREEN = "#23A55A"
GREEN_LIGHT = "#57F287"
RED = "#FA777C"
FONT = "Segoe UI"
MONO = "Consolas"  # Segoe UI Variable and Cascadia are not installed on stock Windows 10

MODE_PROXY = "Proxy only"
MODE_DISCORD = "Discord per-app"
MODE_TUN = "Xray TUN"


class SingleInstanceGuard:
    """Use Windows kernel objects to activate an existing GUI instance."""

    MUTEX_NAME = r"Local\V2Path.GUI.Singleton.v2"
    EVENT_NAME = r"Local\V2Path.GUI.Activate.v2"
    RESTART_EVENT_NAME = r"Local\V2Path.GUI.Restart.v2"
    ERROR_ALREADY_EXISTS = 183
    ERROR_ACCESS_DENIED = 5
    EVENT_MODIFY_STATE = 0x0002
    WAIT_OBJECT_0 = 0

    def __init__(self) -> None:
        self.primary = True
        self.mutex: int | None = None
        self.activation_event: int | None = None
        self.restart_event: int | None = None
        self.stop_event = threading.Event()
        self.watchers: list[threading.Thread] = []
        if os.name != "nt":
            return

        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        self.kernel32.CreateMutexW.restype = ctypes.c_void_p
        self.kernel32.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
        self.kernel32.CreateEventW.restype = ctypes.c_void_p
        self.kernel32.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        self.kernel32.OpenEventW.restype = ctypes.c_void_p
        self.kernel32.SetEvent.argtypes = [ctypes.c_void_p]
        self.kernel32.SetEvent.restype = ctypes.c_int
        self.kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        self.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self.kernel32.CloseHandle.restype = ctypes.c_int

        ctypes.set_last_error(0)
        self.mutex = self.kernel32.CreateMutexW(None, False, self.MUTEX_NAME)
        error = ctypes.get_last_error()
        if not self.mutex:
            if error == self.ERROR_ACCESS_DENIED:
                self.primary = False
                return
            raise ctypes.WinError(error)
        if error == self.ERROR_ALREADY_EXISTS:
            self.primary = False
            return

        self.activation_event = self.kernel32.CreateEventW(None, False, False, self.EVENT_NAME)
        if not self.activation_event:
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)
        self.restart_event = self.kernel32.CreateEventW(None, False, False, self.RESTART_EVENT_NAME)
        if not self.restart_event:
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def _signal_existing(self, event_name: str) -> bool:
        if os.name != "nt" or self.primary:
            return False
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            event = self.kernel32.OpenEventW(self.EVENT_MODIFY_STATE, False, event_name)
            if event:
                try:
                    return bool(self.kernel32.SetEvent(event))
                finally:
                    self.kernel32.CloseHandle(event)
            time.sleep(0.05)
        return False

    def activate_existing(self) -> bool:
        return self._signal_existing(self.EVENT_NAME)

    def request_replacement(self) -> bool:
        return self._signal_existing(self.RESTART_EVENT_NAME)

    def watch(self, activate_callback: object, restart_callback: object) -> None:
        if os.name != "nt" or not self.primary or not self.activation_event:
            return

        def wait_for_event(event: int, callback: object) -> None:
            while not self.stop_event.is_set():
                result = self.kernel32.WaitForSingleObject(event, 250)
                if result == self.WAIT_OBJECT_0 and not self.stop_event.is_set() and callable(callback):
                    callback()

        for name, event, callback in (
            ("V2PathActivate", self.activation_event, activate_callback),
            ("V2PathRestart", self.restart_event, restart_callback),
        ):
            if event:
                watcher = threading.Thread(
                    target=wait_for_event, args=(event, callback), name=name, daemon=True,
                )
                self.watchers.append(watcher)
                watcher.start()

    def close(self) -> None:
        self.stop_event.set()
        if os.name == "nt" and getattr(self, "activation_event", None):
            self.kernel32.SetEvent(self.activation_event)
        if os.name == "nt" and getattr(self, "restart_event", None):
            self.kernel32.SetEvent(self.restart_event)
        for watcher in self.watchers:
            if watcher is not threading.current_thread():
                watcher.join(timeout=0.5)
        self.watchers.clear()
        if os.name == "nt" and getattr(self, "activation_event", None):
            self.kernel32.CloseHandle(self.activation_event)
            self.activation_event = None
        if os.name == "nt" and getattr(self, "restart_event", None):
            self.kernel32.CloseHandle(self.restart_event)
            self.restart_event = None
        if os.name == "nt" and getattr(self, "mutex", None):
            self.kernel32.CloseHandle(self.mutex)
            self.mutex = None


def _default_discord_targets() -> str:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return f"Discord.exe, {local / 'Discord' / 'Update.exe'}"


def _read_json(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class ConfigLibrary:
    def __init__(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        raw = _read_json(LIBRARY_PATH, {"configs": []})
        configs = raw.get("configs", []) if isinstance(raw, dict) else []
        self.records: list[dict[str, str]] = [item for item in configs if isinstance(item, dict)]
        self._discover_json_files()

    def _discover_json_files(self) -> None:
        known = {item.get("value") for item in self.records if item.get("type") == "file"}
        changed = False
        for path in sorted(CONFIG_DIR.glob("*.json")):
            if path.name == LIBRARY_PATH.name or path.name in known:
                continue
            self.records.append({"id": uuid.uuid4().hex, "name": path.stem, "type": "file", "value": path.name})
            changed = True
        if changed:
            self.save()

    def save(self) -> None:
        _atomic_json(LIBRARY_PATH, {"configs": self.records})

    def unique_name(self, requested: str) -> str:
        base = requested.strip() or "VPN config"
        existing = {record["name"].casefold() for record in self.records}
        if base.casefold() not in existing:
            return base
        number = 2
        while f"{base} ({number})".casefold() in existing:
            number += 1
        return f"{base} ({number})"

    def import_file(self, source: Path) -> dict[str, str]:
        secondnet.load_source(str(source))
        stem = self.unique_name(source.stem)
        safe_stem = "".join(character if character.isalnum() or character in "-_. " else "_" for character in stem).strip()
        target = CONFIG_DIR / f"{safe_stem or 'config'}.json"
        counter = 2
        while target.exists():
            target = CONFIG_DIR / f"{safe_stem or 'config'}-{counter}.json"
            counter += 1
        shutil.copy2(source, target)
        record = {"id": uuid.uuid4().hex, "name": stem, "type": "file", "value": target.name}
        self.records.append(record)
        self.save()
        return record

    def add_link(self, link: str) -> dict[str, str]:
        link = link.strip()
        config = secondnet.load_source(link)
        del config
        parsed = urllib.parse.urlsplit(link)
        label = urllib.parse.unquote(parsed.fragment).strip() or parsed.hostname or parsed.scheme.upper()
        record = {"id": uuid.uuid4().hex, "name": self.unique_name(label), "type": "link", "value": link}
        self.records.append(record)
        self.save()
        return record

    def remove(self, record_id: str) -> None:
        match = next((record for record in self.records if record.get("id") == record_id), None)
        if not match:
            return
        if match.get("type") == "file":
            path = (CONFIG_DIR / match.get("value", "")).resolve()
            if path.parent == CONFIG_DIR.resolve() and path.is_file():
                path.unlink()
        self.records.remove(match)
        self.save()

    def source(self, record: dict[str, str]) -> str:
        return str(CONFIG_DIR / record["value"]) if record["type"] == "file" else record["value"]


class LiquidPowerButton(ctk.CTkFrame):
    """Large circular liquid-glass power control rendered with native Tk shapes."""

    # Kept visually dominant, while fitting below the profile list at the
    # minimum supported window height.
    WIDTH = 218
    HEIGHT = 186

    def __init__(self, master: ctk.CTkBaseClass, command: object) -> None:
        super().__init__(master, width=self.WIDTH, height=self.HEIGHT, fg_color="transparent")
        self.grid_propagate(False)
        self.command = command
        self.visual_state = "off"
        self.enabled = True
        self.hovered = False
        self.focused = False
        self.progress = 0.0
        self._requested_size = (self.WIDTH, self.HEIGHT)
        self.animation_id: str | None = None
        self.busy_animation_id: str | None = None
        self.busy_phase = 0.0
        self.canvas = ctk.CTkCanvas(
            self, width=self.WIDTH, height=self.HEIGHT, bg=GLASS,
            highlightthickness=0, cursor="hand2", takefocus=1,
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._activate)
        self.canvas.bind("<Return>", self._activate)
        self.canvas.bind("<space>", self._activate)
        self.canvas.bind("<Enter>", self._enter)
        self.canvas.bind("<Leave>", self._leave)
        self.canvas.bind("<FocusIn>", self._focus_in)
        self.canvas.bind("<FocusOut>", self._focus_out)
        self.canvas.bind("<Configure>", lambda _event: self._render_control())
        self._render_control()

    def set_size(self, width: int, height: int) -> None:
        """Resize the control without changing its grid allocation or state."""
        width = max(160, width)
        height = max(150, height)
        if self._requested_size == (width, height):
            return
        self._requested_size = (width, height)
        self.configure(width=width, height=height)
        self.canvas.configure(width=width, height=height)
        self.after_idle(self._render_control)

    @staticmethod
    def _mix(start: str, end: str, amount: float) -> str:
        amount = max(0.0, min(1.0, amount))
        first = tuple(int(start[index:index + 2], 16) for index in (1, 3, 5))
        second = tuple(int(end[index:index + 2], 16) for index in (1, 3, 5))
        values = tuple(round(a + (b - a) * amount) for a, b in zip(first, second))
        return f"#{values[0]:02X}{values[1]:02X}{values[2]:02X}"

    def _draw_power_icon(self, center_x: float, center_y: float, color: str, scale: float) -> None:
        radius = 34 * scale
        self.canvas.create_arc(
            center_x - radius, center_y - radius, center_x + radius, center_y + radius,
            start=135, extent=270, style="arc", outline=color, width=max(6, round(9 * scale)),
        )
        self.canvas.create_line(
            center_x, center_y - 45 * scale, center_x, center_y - 7 * scale,
            fill=color, width=max(7, round(10 * scale)), capstyle="round",
        )

    def _render_control(self) -> None:
        self.canvas.delete("all")
        active = self.progress
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width <= 1:
            width = self.WIDTH
        if height <= 1:
            height = self.HEIGHT
        center_x = width / 2
        status_space = 48
        circle_area_height = max(120, height - status_space)
        base_radius = min(72, width * 0.32, circle_area_height * 0.43)
        center_y = max(base_radius + 9, circle_area_height / 2)
        busy = self.visual_state in {"starting", "stopping"}
        pulse = (math.sin(math.radians(self.busy_phase * 2)) + 1) / 2 if busy else 0
        radius = base_radius + (3 if self.hovered and self.enabled else 0) + pulse * 1.5
        scale = base_radius / 72
        face = self._mix(PANEL_ALT, GREEN, active)
        edge = self._mix(BLUE, GREEN_LIGHT, active)
        icon = self._mix("#DBDEE1", "#FFFFFF", active)
        glow = self._mix("#2A2F5E", "#1A6B3C", active)
        if not self.enabled and not busy:
            face = self._mix(face, "#4E5058", 0.22)
            icon = self._mix(icon, SUBTLE, 0.3)

        # Layered rings approximate a soft liquid-glass bloom without costly blur.
        self.canvas.create_oval(
            center_x - radius - 12, center_y - radius - 12,
            center_x + radius + 12, center_y + radius + 12,
            fill=self._mix(GLASS, glow, 0.30 + active * 0.25), outline="",
        )
        self.canvas.create_oval(
            center_x - radius - 6, center_y - radius - 6,
            center_x + radius + 6, center_y + radius + 6,
            fill=self._mix(GLASS, glow, 0.62), outline="",
        )
        self.canvas.create_oval(
            center_x - radius, center_y - radius,
            center_x + radius, center_y + radius,
            fill=face, outline=edge, width=3,
        )
        self.canvas.create_arc(
            center_x - radius + 8, center_y - radius + 8,
            center_x + radius - 8, center_y + radius - 8,
            start=32, extent=112, style="arc",
            outline=self._mix("#4E5058", "#A6F5C2", active), width=3,
        )
        self._draw_power_icon(center_x, center_y + 3 * scale, icon, scale)

        label = (
            "STARTING" if self.visual_state == "starting" else
            "STOPPING" if self.visual_state == "stopping" else
            "CONNECTED" if active >= 0.5 else "DISCONNECTED"
        )
        self.canvas.create_text(
            center_x, height - 34, text=label, anchor="center",
            fill=self._mix(TEXT, GREEN_LIGHT, active),
            font=(FONT, 12, "bold"),
        )
        self.canvas.create_text(
            center_x, height - 15,
            text="Connecting…" if self.visual_state == "starting" else
                 "Disconnecting…" if self.visual_state == "stopping" else
                 "Click to disconnect" if active >= 0.5 else "Click to connect",
            anchor="center", fill=self._mix(MUTED, "#B9F6CE", active),
            font=(FONT, 9),
        )
        if busy:
            self.canvas.create_arc(
                center_x - radius - 8, center_y - radius - 8,
                center_x + radius + 8, center_y + radius + 8,
                start=self.busy_phase, extent=88, style="arc",
                outline="#FFFFFF", width=4,
            )
            self.canvas.create_arc(
                center_x - radius - 8, center_y - radius - 8,
                center_x + radius + 8, center_y + radius + 8,
                start=(self.busy_phase + 180) % 360, extent=38, style="arc",
                outline=self._mix(BLUE_LIGHT, GREEN_LIGHT, active), width=2,
            )
        if self.focused:
            self.canvas.create_oval(
                center_x - radius - 9, center_y - radius - 9,
                center_x + radius + 9, center_y + radius + 9,
                outline=BLUE_LIGHT, width=3,
            )

    @staticmethod
    def _animations_allowed() -> bool:
        animations_enabled = ctypes.c_int(1)
        try:
            ctypes.windll.user32.SystemParametersInfoW(
                0x1042, 0, ctypes.byref(animations_enabled), 0,
            )
        except OSError:
            pass
        return bool(animations_enabled.value)

    def _animate_to(self, target: float) -> None:
        if self.animation_id:
            self.after_cancel(self.animation_id)
        if not self._animations_allowed():
            self.progress = target
            self.animation_id = None
            self._render_control()
            return
        start = self.progress
        steps = 12

        def frame(index: int) -> None:
            amount = index / steps
            eased = 1 - (1 - amount) ** 3
            self.progress = start + (target - start) * eased
            self._render_control()
            if index < steps:
                self.animation_id = self.after(18, frame, index + 1)
            else:
                self.animation_id = None

        frame(1)

    def _start_busy_animation(self) -> None:
        if self.busy_animation_id:
            self.after_cancel(self.busy_animation_id)
            self.busy_animation_id = None
        if not self._animations_allowed():
            self._render_control()
            return

        def tick() -> None:
            if self.visual_state not in {"starting", "stopping"}:
                self.busy_animation_id = None
                return
            self.busy_phase = (self.busy_phase + 12) % 360
            self._render_control()
            self.busy_animation_id = self.after(33, tick)

        tick()

    def _stop_busy_animation(self) -> None:
        if self.busy_animation_id:
            self.after_cancel(self.busy_animation_id)
            self.busy_animation_id = None

    def set_visual_state(self, state: str) -> None:
        self._stop_busy_animation()
        self.visual_state = state
        self.enabled = state in {"off", "on"}
        self.canvas.configure(cursor="hand2" if self.enabled else "arrow")
        target = {
            "off": 0.0,
            "starting": 0.16,
            "on": 1.0,
            "stopping": 0.84,
        }[state]
        self._animate_to(target)
        if state in {"starting", "stopping"}:
            self._start_busy_animation()

    def _activate(self, _event: object = None) -> str:
        if self.enabled and callable(self.command):
            self.command()
        return "break"

    def _enter(self, _event: object) -> None:
        self.hovered = True
        self._render_control()

    def _leave(self, _event: object) -> None:
        self.hovered = False
        self._render_control()

    def _focus_in(self, _event: object) -> None:
        self.focused = True
        self._render_control()

    def _focus_out(self, _event: object) -> None:
        self.focused = False
        self._render_control()


class V2PathGUI(ctk.CTk):
    def __init__(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        super().__init__(fg_color=BG)
        # Build and lay out while transparent, so the window appears fully drawn instead of painting in.
        self.attributes("-alpha", 0.0)
        self.title(f"V2Path {GUI_VERSION}")
        self.geometry("1180x800")
        self.minsize(1040, 720)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.library = ConfigLibrary()
        self.settings = _read_json(SETTINGS_PATH, {})
        if not isinstance(self.settings, dict):
            self.settings = {}
        self.adapters: list[dict[str, object]] = []
        self.adapter_by_label: dict[str, dict[str, object]] = {}
        self.record_by_id: dict[str, dict[str, str]] = {}
        self.selected_config_id = ""
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.direct_proxy_process: subprocess.Popen[str] | None = None
        self.filter_process: subprocess.Popen[str] | None = None
        self.direct_proxy_stop_expected = threading.Event()
        self.filter_stop_expected = threading.Event()
        self.process_lock = threading.Lock()
        try:
            self.child_job: secondnet.WindowsChildJob | None = secondnet.WindowsChildJob()
            self.child_job_error = ""
        except OSError as exc:
            self.child_job = None
            self.child_job_error = str(exc)
        self.stop_requested = threading.Event()
        self.closing = False
        self.window_hidden = False
        self.app_state = "idle"
        self.tray_icon: pystray.Icon | None = None
        self._resize_job: str | None = None

        self._build_ui()
        self.bind("<Configure>", self._queue_responsive_layout)
        self.after_idle(self._apply_responsive_layout)
        self._routing_mode_changed(self.mode_combo.get())
        self._refresh_config_choices()
        self._set_state("idle")
        self.after(100, self._poll_events)
        self._refresh_adapters()
        self._start_tray()
        self.update()
        self._apply_responsive_layout()
        self.update_idletasks()
        self.attributes("-alpha", 1.0)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=0, minsize=420)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(
            self, width=398, fg_color=GLASS, corner_radius=24,
            border_width=1, border_color=BORDER,
        )
        self.left_panel = left
        left.grid(row=0, column=0, sticky="nsew", padx=(22, 11), pady=22)
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(4, weight=1)

        brand = ctk.CTkFrame(left, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=26, pady=(18, 12))
        brand.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            brand, text="SN", width=52, height=52, corner_radius=16,
            fg_color=BLUE, text_color="white", font=(FONT, 18, "bold"),
        ).grid(row=0, column=0, rowspan=2, padx=(0, 14))
        ctk.CTkLabel(brand, text="V2Path", text_color=TEXT, font=(FONT, 24, "bold")).grid(
            row=0, column=1, sticky="sw"
        )
        ctk.CTkLabel(
            brand, text="Your second connection, isolated.", text_color=MUTED,
            font=(FONT, 11),
        ).grid(row=1, column=1, sticky="nw")

        ctk.CTkLabel(left, text="INTERNET", text_color=MUTED, font=(FONT, 10, "bold")).grid(
            row=1, column=0, sticky="w", padx=28, pady=(0, 7)
        )
        adapter_field = ctk.CTkFrame(left, fg_color=GLASS_SOFT, corner_radius=14, border_width=1, border_color=BORDER)
        adapter_field.grid(row=2, column=0, sticky="ew", padx=24)
        adapter_field.grid_columnconfigure(0, weight=1)
        selector_row = ctk.CTkFrame(adapter_field, fg_color="transparent")
        selector_row.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 7))
        selector_row.grid_columnconfigure(0, weight=1)
        self.adapter_combo = ctk.CTkComboBox(
            selector_row, values=["Loading adapters…"], state="readonly", height=42,
            corner_radius=10, fg_color=BG, border_color=BORDER, button_color=BLUE,
            button_hover_color=BLUE_HOVER, dropdown_fg_color=PANEL_ALT,
            command=self._adapter_changed,
        )
        self.adapter_combo.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.refresh_button = ctk.CTkButton(
            selector_row, text="↻", width=42, height=42, corner_radius=10,
            fg_color=PANEL_ALT, hover_color=BORDER, command=self._refresh_adapters,
            font=(FONT, 18),
        )
        self.refresh_button.grid(row=0, column=1)
        self.adapter_detail = ctk.CTkLabel(
            adapter_field, text="Detecting connected networks…", anchor="w", justify="left",
            wraplength=320, text_color=MUTED, font=(FONT, 10),
        )
        self.adapter_detail.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 12))

        config_area = ctk.CTkFrame(left, fg_color="transparent")
        config_area.grid(row=3, column=0, sticky="ew", padx=24, pady=(12, 0))
        config_area.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(config_area, text="VPN PROFILES", text_color=MUTED, font=(FONT, 10, "bold")).grid(
            row=0, column=0, sticky="w", padx=4, pady=(0, 7)
        )
        config_field = ctk.CTkFrame(
            config_area, fg_color=GLASS_SOFT, corner_radius=14, border_width=1, border_color=BORDER,
        )
        config_field.grid(row=1, column=0, sticky="ew")
        config_field.grid_columnconfigure(0, weight=1)
        config_header = ctk.CTkFrame(config_field, fg_color="transparent")
        config_header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        config_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            config_header, text="Choose a profile", text_color=TEXT,
            font=(FONT, 12, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self.profile_count_label = ctk.CTkLabel(
            config_header, text="0 saved", text_color=MUTED, font=(FONT, 10),
        )
        self.profile_count_label.grid(row=0, column=1, sticky="e")
        self.config_list = ctk.CTkScrollableFrame(
            config_field, height=112, fg_color=BG, corner_radius=10,
            scrollbar_button_color=BORDER, scrollbar_button_hover_color=BLUE,
        )
        # CustomTkinter wraps a scrollable frame in a private shell frame.  Its
        # content must not be allowed to increase the shell's grid request;
        # otherwise a long profile list can push the primary action off-screen.
        self.profile_list_shell = self.config_list._parent_frame
        self.profile_list_shell.grid_propagate(False)
        self.config_list.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        self.config_list.grid_columnconfigure(0, weight=1)
        config_buttons = ctk.CTkFrame(config_field, fg_color="transparent")
        config_buttons.grid(row=2, column=0, sticky="ew", padx=12)
        config_buttons.grid_columnconfigure((0, 1), weight=1)
        self.import_button = ctk.CTkButton(
            config_buttons, text="+  Import JSON", height=34, fg_color=PANEL_ALT,
            hover_color=BORDER, command=self._import_config,
        )
        self.import_button.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.link_button = ctk.CTkButton(
            config_buttons, text="+  Share link", height=34, fg_color=PANEL_ALT,
            hover_color=BORDER, command=self._add_link,
        )
        self.link_button.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        config_footer = ctk.CTkFrame(config_field, fg_color="transparent")
        config_footer.grid(row=3, column=0, sticky="ew", padx=14, pady=(9, 12))
        config_footer.grid_columnconfigure(0, weight=1)
        self.config_detail = ctk.CTkLabel(
            config_footer, text="Select a profile", anchor="w", wraplength=245,
            text_color=MUTED, font=(FONT, 10),
        )
        self.config_detail.grid(row=0, column=0, sticky="ew")
        self.remove_button = ctk.CTkButton(
            config_footer, text="Remove", width=62, height=30, fg_color="transparent",
            border_width=1, border_color=BORDER, hover_color=PANEL_ALT,
            text_color=MUTED, command=self._remove_config,
        )
        self.remove_button.grid(row=0, column=1)

        connection = ctk.CTkFrame(left, fg_color="transparent")
        self.connection_area = connection
        connection.grid(row=4, column=0, sticky="sew", padx=24, pady=(8, 4))
        connection.grid_columnconfigure(0, weight=1)
        self.power_button = LiquidPowerButton(connection, self._toggle_connection)
        self.power_button.grid(row=0, column=0, sticky="n", pady=(0, 2))
        ctk.CTkLabel(
            left, text=f"Release {GUI_VERSION}", text_color=SUBTLE, font=(FONT, 10),
        ).grid(row=5, column=0, pady=(0, 12))

        right = ctk.CTkFrame(
            self, fg_color=GLASS, corner_radius=24, border_width=1, border_color=BORDER,
        )
        right.grid(row=0, column=1, sticky="nsew", padx=(11, 22), pady=22)
        right.grid_columnconfigure(0, weight=1)
        right.grid_columnconfigure(1, weight=0, minsize=148)
        right.grid_rowconfigure(0, weight=1)

        self.page_host = ctk.CTkFrame(right, fg_color="transparent")
        self.page_host.grid(row=0, column=0, sticky="nsew", padx=(24, 14), pady=24)
        self.page_host.grid_columnconfigure(0, weight=1)
        self.page_host.grid_rowconfigure(0, weight=1)

        nav = ctk.CTkFrame(right, width=138, fg_color=BG, corner_radius=18)
        nav.grid(row=0, column=1, sticky="ns", padx=(0, 18), pady=18)
        nav.grid_columnconfigure(0, weight=1)
        nav.grid_rowconfigure(4, weight=1)
        ctk.CTkLabel(nav, text="MENU", text_color=SUBTLE, font=(FONT, 9, "bold")).grid(
            row=0, column=0, sticky="w", padx=18, pady=(22, 10)
        )
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        for row, name in enumerate(("General", "Log", "Settings"), start=1):
            button = ctk.CTkButton(
                nav, text=name, height=44, anchor="w",
                corner_radius=11, fg_color="transparent", hover_color=PANEL_ALT,
                text_color=MUTED, font=(FONT, 12, "bold"),
                command=lambda page=name: self._show_tab(page),
            )
            button.grid(row=row, column=0, sticky="ew", padx=10, pady=4)
            self.nav_buttons[name] = button

        self.author_label = ctk.CTkLabel(
            nav,
            text="Written by ZzeK\nfor gamers, with love",
            justify="center",
            text_color=SUBTLE,
            font=(FONT, 10),
        )
        self.author_label.grid(row=5, column=0, sticky="s", padx=12, pady=(10, 18))

        self.pages: dict[str, ctk.CTkFrame] = {}
        for name in ("General", "Log", "Settings"):
            page = ctk.CTkFrame(self.page_host, fg_color="transparent")
            page.grid(row=0, column=0, sticky="nsew")
            page.grid_columnconfigure(0, weight=1)
            page.grid_rowconfigure(0, weight=1)
            self.pages[name] = page

        general = self.pages["General"]
        general.grid_rowconfigure(0, weight=0)
        general.grid_rowconfigure(3, weight=1)
        ctk.CTkLabel(general, text="General", text_color=TEXT, font=(FONT, 25, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkLabel(
            general, text="Connection overview", text_color=MUTED, font=(FONT, 11),
        ).grid(row=1, column=0, sticky="w", pady=(2, 22))
        hero = ctk.CTkFrame(
            general, fg_color=GLASS_SOFT, corner_radius=18, border_width=2, border_color=BORDER,
        )
        self.hero = hero
        hero.grid(row=2, column=0, sticky="ew")
        hero.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            hero, text="SECONDNET STATUS", text_color=MUTED, font=(FONT, 9, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=22, pady=(20, 4))
        self.status_badge = ctk.CTkLabel(
            hero, text="●  STOPPED", height=28, corner_radius=14,
            fg_color=PANEL_ALT, text_color=MUTED, font=(FONT, 10, "bold"),
        )
        self.status_badge.grid(row=0, column=1, rowspan=2, sticky="ne", padx=22, pady=(18, 0), ipadx=10)
        self.general_status = ctk.CTkLabel(
            hero, text="Ready to connect", text_color=TEXT, font=(FONT, 22, "bold"),
        )
        self.general_status.grid(row=1, column=0, sticky="w", padx=22)
        self.summary_label = ctk.CTkLabel(
            hero, text="Select an internet connection and VPN config to begin",
            text_color=MUTED, font=(FONT, 11), anchor="w", justify="left", wraplength=480,
        )
        self.summary_label.grid(row=2, column=0, columnspan=2, sticky="ew", padx=22, pady=(8, 22))
        endpoint_card = self._card(
            general, "Split local proxy endpoints",
            "VPN via Xray  •  direct via background Node.js on the selected internet",
        )
        endpoint_card.grid(row=3, column=0, sticky="new", pady=(16, 0))
        self.endpoint_label = ctk.CTkLabel(
            endpoint_card, text="VPN SOCKS5       127.0.0.1:10808\nDIRECT SOCKS5    127.0.0.1:10809",
            text_color="#DBDEE1", font=(MONO, 13), anchor="w", justify="left",
        )
        self.endpoint_label.grid(row=2, column=0, sticky="ew", padx=18, pady=(8, 18))

        log_page = self.pages["Log"]
        log_page.grid_rowconfigure(0, weight=0)
        log_page.grid_rowconfigure(1, weight=0)
        log_page.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(log_page, text="Connection log", text_color=TEXT, font=(FONT, 25, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        log_toolbar = ctk.CTkFrame(log_page, fg_color="transparent")
        log_toolbar.grid(row=1, column=0, sticky="ew", pady=(6, 12))
        log_toolbar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            log_toolbar, text="Live Xray and routing activity", text_color=MUTED, font=(FONT, 11),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            log_toolbar, text="Clear log", width=82, height=30, fg_color=PANEL_ALT,
            hover_color=BORDER, text_color=MUTED, command=self._clear_log,
        ).grid(row=0, column=1)
        self.log_box = ctk.CTkTextbox(
            log_page, fg_color=BG, border_width=1, border_color=BORDER,
            corner_radius=14, text_color="#DBDEE1", font=(MONO, 11),
            scrollbar_button_color=BORDER, scrollbar_button_hover_color=BLUE,
        )
        self.log_box.grid(row=2, column=0, sticky="nsew")
        self.log_box.configure(state="disabled")

        settings = ctk.CTkScrollableFrame(
            self.pages["Settings"], fg_color="transparent", scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=BLUE,
        )
        settings.grid(row=0, column=0, sticky="nsew")
        settings.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(settings, text="Settings", text_color=TEXT, font=(FONT, 25, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkLabel(
            settings, text="Routing mode, applications, and local proxy ports",
            text_color=MUTED, font=(FONT, 11),
        ).grid(row=1, column=0, sticky="w", pady=(2, 20))

        app_routing = self._card(settings, "Routing mode", "Discord per-app is the recommended default")
        app_routing.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        app_routing.grid_columnconfigure(0, weight=1)
        saved_mode = str(self.settings.get("routing_mode", ""))
        if saved_mode not in {MODE_PROXY, MODE_DISCORD, MODE_TUN}:
            saved_mode = MODE_DISCORD
        self.mode_combo = ctk.CTkComboBox(
            app_routing, values=[MODE_PROXY, MODE_DISCORD, MODE_TUN], state="readonly", height=38,
            fg_color=BG, border_color=BORDER, button_color=BLUE, button_hover_color=BLUE_HOVER,
            dropdown_fg_color=PANEL_ALT, command=self._routing_mode_changed,
        )
        self.mode_combo.grid(row=2, column=0, sticky="ew", padx=18, pady=(7, 8))
        self.mode_combo.set(saved_mode)
        self.routing_detail = ctk.CTkLabel(
            app_routing, text="", anchor="w", justify="left", wraplength=500,
            text_color=MUTED, font=(FONT, 10),
        )
        self.routing_detail.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 15))

        apps = self._card(settings, "Applications", "Executable names or full paths, separated by commas")
        apps.grid(row=3, column=0, sticky="ew", pady=(0, 14))
        apps.grid_columnconfigure(0, weight=1)
        process_row = ctk.CTkFrame(apps, fg_color="transparent")
        process_row.grid(row=2, column=0, sticky="ew", padx=18, pady=(7, 8))
        process_row.grid_columnconfigure(0, weight=1)
        self.process_entry = ctk.CTkEntry(
            process_row, height=42, fg_color=BG, border_color=BORDER,
            placeholder_text="Discord.exe, C:\\...\\Discord\\Update.exe",
        )
        self.process_entry.grid(row=0, column=0, sticky="ew", padx=(0, 7))
        self.process_entry.insert(0, str(self.settings.get("routing_processes", self.settings.get("tunnel_processes", _default_discord_targets()))))
        self.process_button = ctk.CTkButton(
            process_row, text="Browse .exe", width=92, height=42, fg_color=PANEL_ALT,
            hover_color=BORDER, command=self._select_process,
        )
        self.process_button.grid(row=0, column=1)
        app_actions = ctk.CTkFrame(apps, fg_color="transparent")
        app_actions.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 8))
        app_actions.grid_columnconfigure((0, 1), weight=1)
        self.discord_launch_button = ctk.CTkButton(
            app_actions, text="Restart Discord now", height=34,
            fg_color=PANEL_ALT, hover_color=BORDER, command=self._restart_discord_direct,
        )
        self.discord_launch_button.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.process_clear_button = ctk.CTkButton(
            app_actions, text="Clear list", height=34, fg_color="transparent",
            border_width=1, border_color=BORDER, hover_color=PANEL_ALT,
            text_color=MUTED, command=self._clear_processes,
        )
        self.process_clear_button.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        ctk.CTkLabel(
            apps, text="Discord restarts automatically after connecting; this button restarts it again.",
            text_color=MUTED, font=(FONT, 10), anchor="w",
        ).grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 15))

        ports = self._card(
            settings, "Split local proxy ports",
            "VPN via Xray  •  direct via hidden Node.js dispatcher",
        )
        ports.grid(row=4, column=0, sticky="ew", pady=(0, 18))
        ports.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(ports, text="VPN SOCKS5", text_color=MUTED, font=(FONT, 10, "bold")).grid(
            row=2, column=0, sticky="w", padx=(18, 7), pady=(5, 3)
        )
        ctk.CTkLabel(ports, text="DIRECT SOCKS5", text_color=MUTED, font=(FONT, 10, "bold")).grid(
            row=2, column=1, sticky="w", padx=(7, 18), pady=(5, 3)
        )
        self.socks_entry = ctk.CTkEntry(ports, height=40, fg_color=BG, border_color=BORDER)
        self.socks_entry.grid(row=3, column=0, sticky="ew", padx=(18, 7), pady=(0, 16))
        self.socks_entry.insert(0, str(self.settings.get("socks_port", 10808)))
        self.direct_socks_entry = ctk.CTkEntry(ports, height=40, fg_color=BG, border_color=BORDER)
        self.direct_socks_entry.grid(row=3, column=1, sticky="ew", padx=(7, 18), pady=(0, 16))
        self.direct_socks_entry.insert(
            0, str(self.settings.get("direct_socks_port", self.settings.get("http_port", 10809)))
        )
        self._show_tab("General")

    def _queue_responsive_layout(self, event: object) -> None:
        """Debounce top-level resize events so the canvas is redrawn once per resize."""
        if getattr(event, "widget", None) is not self:
            return
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(70, self._apply_responsive_layout)

    def _apply_responsive_layout(self) -> None:
        """Keep the selector, profile list, and primary action inside the window."""
        self._resize_job = None
        width = self.winfo_width()
        height = self.winfo_height()
        if width < 200 or height < 200:
            return

        # The left control panel uses a proportion of the window instead of a
        # permanent 420px column.  Its interior controls shrink in concert so
        # the large connection action never falls below the visible area.
        left_width = max(354, min(414, round(width * 0.34)))
        # At the minimum window height, profiles become a compact, scrollable
        # list.  This reserves enough vertical room for the button and both of
        # its status labels.  The list opens up again on taller windows.
        profile_height = max(54, min(118, round((height - 300) * 0.09)))
        power_width = max(196, min(244, round(left_width * 0.59)))
        power_height = max(160, min(210, round((height - 150) * 0.21)))

        self.grid_columnconfigure(0, minsize=left_width)
        self.left_panel.configure(width=left_width)
        self.config_list.configure(height=profile_height)
        self.profile_list_shell.configure(height=profile_height + 24)
        self.power_button.set_size(power_width, power_height)
        self.adapter_detail.configure(wraplength=max(240, left_width - 86))
        self.config_detail.configure(wraplength=max(150, left_width - 172))

    def _card(self, parent: ctk.CTkBaseClass, title: str, subtitle: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=GLASS_SOFT, corner_radius=16, border_width=1, border_color=BORDER)
        ctk.CTkLabel(card, text=title, text_color=TEXT, font=(FONT, 14, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(15, 0)
        )
        ctk.CTkLabel(card, text=subtitle, text_color=MUTED, font=(FONT, 10)).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=18, pady=(1, 8)
        )
        return card

    def _show_tab(self, name: str) -> None:
        # Unmap hidden pages instead of stacking them, so resizes only redraw the visible page.
        for page_name, page in self.pages.items():
            if page_name == name:
                page.grid()
            else:
                page.grid_remove()
        for page_name, button in self.nav_buttons.items():
            active = page_name == name
            button.configure(
                fg_color=BLUE_SOFT if active else "transparent",
                text_color=TEXT if active else MUTED,
            )

    def _clear_processes(self) -> None:
        self.process_entry.delete(0, "end")
        self._routing_mode_changed(self.mode_combo.get())

    def _append_log(self, text: str) -> None:
        self._append_logs([text])

    def _append_logs(self, lines: list[str]) -> None:
        if not lines:
            return
        self.log_box.configure(state="normal")
        self.log_box.insert("end", "\n".join(line.rstrip() for line in lines) + "\n")
        try:
            line_count = int(self.log_box.index("end-1c").split(".")[0])
            if line_count > 5000:
                self.log_box.delete("1.0", f"{line_count - 4000}.0")
        except (ValueError, TypeError):
            pass
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _set_state(self, state: str) -> None:
        self.app_state = state
        running = state in {"starting", "running", "stopping"}
        self.adapter_combo.configure(state="disabled" if running else "readonly")
        self._render_config_list()
        for widget in (self.import_button, self.link_button, self.remove_button,
                       self.socks_entry, self.direct_socks_entry, self.refresh_button):
            widget.configure(state="disabled" if running else "normal")
        self.mode_combo.configure(state="disabled" if running else "readonly")
        process_state = "disabled" if running or self.mode_combo.get() == MODE_PROXY else "normal"
        self.process_entry.configure(state=process_state)
        self.process_button.configure(state=process_state)
        self.process_clear_button.configure(state=process_state)
        discord_launch_state = "normal" if state == "running" and self.mode_combo.get() == MODE_DISCORD else "disabled"
        self.discord_launch_button.configure(state=discord_launch_state)
        if state == "running":
            self.status_badge.configure(text="●  CONNECTED", fg_color="#173B28", text_color=GREEN_LIGHT)
            self.general_status.configure(text="Connected", text_color=GREEN_LIGHT)
            self.hero.configure(border_color=GREEN)
            self.power_button.set_visual_state("on")
        elif state == "starting":
            self.status_badge.configure(text="●  STARTING", fg_color="#262B52", text_color=BLUE_LIGHT)
            self.general_status.configure(text="Starting…", text_color=BLUE_LIGHT)
            self.hero.configure(border_color=BLUE)
            self.power_button.set_visual_state("starting")
        elif state == "stopping":
            self.status_badge.configure(text="●  STOPPING", fg_color="#4A2224", text_color=RED)
            self.general_status.configure(text="Stopping…", text_color=RED)
            self.hero.configure(border_color=RED)
            self.power_button.set_visual_state("stopping")
        else:
            self.status_badge.configure(text="●  STOPPED", fg_color=PANEL_ALT, text_color=MUTED)
            self.general_status.configure(text="Ready to connect", text_color=TEXT)
            self.hero.configure(border_color=BORDER)
            self.power_button.set_visual_state("off")
        self._refresh_tray()

    def _toggle_connection(self) -> None:
        if self.app_state == "idle":
            self._start()
        elif self.app_state == "running":
            self._stop()

    @staticmethod
    def _tray_image(connected: bool = False) -> Image.Image:
        size = 64
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        fill = (35, 165, 90, 255) if connected else (88, 101, 242, 255)
        edge = (87, 242, 135, 255) if connected else (148, 156, 247, 255)
        draw.rounded_rectangle((5, 5, 59, 59), radius=16, fill=fill, outline=edge, width=3)
        draw.arc((18, 17, 46, 47), start=315, end=585, fill=(255, 255, 255, 255), width=5)
        draw.line((32, 14, 32, 31), fill=(255, 255, 255, 255), width=5)
        return image

    def _start_tray(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem("Show V2Path", self._tray_show, default=True),
            pystray.MenuItem(
                lambda _item: f"Status: {self.app_state.capitalize()}",
                None, enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Start V2Path", self._tray_start,
                enabled=lambda _item: self.app_state == "idle",
            ),
            pystray.MenuItem(
                "Stop V2Path", self._tray_stop,
                enabled=lambda _item: self.app_state in {"starting", "running"},
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit V2Path", self._tray_exit),
        )
        self.tray_icon = pystray.Icon(
            "V2Path", self._tray_image(), f"V2Path {GUI_VERSION}", menu,
        )
        threading.Thread(target=self.tray_icon.run, name="V2PathTray", daemon=True).start()

    def _refresh_tray(self) -> None:
        icon = self.tray_icon
        if icon is None:
            return
        connected = self.app_state in {"running", "stopping"}
        icon.icon = self._tray_image(connected)
        icon.title = f"V2Path — {self.app_state.capitalize()}"
        try:
            icon.update_menu()
        except AttributeError:
            pass

    def _tray_show(self, _icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        self.after(0, self._show_window)

    def _tray_start(self, _icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        self.after(0, self._show_window)
        self.after(50, self._start)

    def _tray_stop(self, _icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        self.after(0, self._stop)

    def _tray_exit(self, _icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        self.after(0, self._exit_application)

    def _show_window(self) -> None:
        if self.closing:
            return
        self.window_hidden = False
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()
        try:
            self.attributes("-topmost", True)
            self.after(150, lambda: self.attributes("-topmost", False))
        except Exception:
            pass

    def _hide_to_tray(self) -> None:
        if self.closing:
            return
        self.window_hidden = True
        self.withdraw()
        icon = self.tray_icon
        if icon is not None:
            try:
                icon.notify(
                    "V2Path is still running in the background. "
                    "Use the tray icon to reopen or exit.",
                    "V2Path",
                )
            except (NotImplementedError, OSError):
                pass

    def _refresh_adapters(self) -> None:
        if self.app_state != "idle":
            return
        self.adapter_combo.configure(values=["Loading adapters…"])
        self.adapter_combo.set("Loading adapters…")
        self.refresh_button.configure(state="disabled")
        threading.Thread(target=self._load_adapters_worker, daemon=True).start()

    def _load_adapters_worker(self) -> None:
        try:
            self.events.put(("adapters", secondnet.get_adapters()))
        except Exception as exc:
            self.events.put(("error", f"Adapter detection failed: {exc}"))

    def _apply_adapters(self, adapters: list[dict[str, object]]) -> None:
        self.adapters = adapters
        self.adapter_by_label.clear()
        eligible = [a for a in adapters if str(a.get("status", "")).lower() == "up" and secondnet._as_list(a.get("ipv4"))]
        for adapter in eligible:
            ip = ", ".join(secondnet._as_list(adapter.get("ipv4")))
            gateway = ", ".join(secondnet._as_list(adapter.get("gateways"))) or "no gateway"
            label = f"{adapter.get('name')}  •  {ip}  •  {gateway}"
            self.adapter_by_label[label] = adapter
        labels = list(self.adapter_by_label)
        self.adapter_combo.configure(values=labels or ["No connected adapters"])
        cached = secondnet.find_cached_adapter(eligible, self.settings)
        selected = next(
            (label for label, adapter in self.adapter_by_label.items() if adapter is cached), None,
        )
        if not selected:
            try:
                automatic = secondnet.select_adapter(adapters, None)
                selected = next(label for label, adapter in self.adapter_by_label.items() if adapter is automatic)
            except (secondnet.V2PathError, StopIteration):
                selected = labels[0] if labels else "No connected adapters"
        self.adapter_combo.set(selected)
        self.refresh_button.configure(state="normal")
        # Do not replace a temporarily unavailable remembered adapter with an
        # automatic fallback. A deliberate selection or VPN start persists it.
        self._adapter_changed(selected, persist=cached is not None)

    def _adapter_changed(self, label: str, persist: bool = True) -> None:
        adapter = self.adapter_by_label.get(label)
        if not adapter:
            self.adapter_detail.configure(text="No usable adapter selected")
        else:
            gateways = ", ".join(secondnet._as_list(adapter.get("gateways"))) or "none"
            kind = "Physical adapter" if adapter.get("physical") else "Virtual adapter"
            self.adapter_detail.configure(
                text=f"Selected: {adapter.get('name')}  •  Gateway: {gateways}\n{kind} — VPN traffic will leave through this connection"
            )
            if persist:
                self.settings.update({
                    "adapter": adapter.get("name"),
                    "adapter_guid": adapter.get("guid"),
                    "adapter_mac": adapter.get("mac"),
                    "adapter_ifindex": adapter.get("ifIndex"),
                })
                try:
                    _atomic_json(SETTINGS_PATH, self.settings)
                except OSError as exc:
                    self._append_log(f"WARNING: Could not remember internet adapter: {exc}")
        self._update_summary()

    def _selected_config(self) -> dict[str, str] | None:
        return self.record_by_id.get(self.selected_config_id)

    @staticmethod
    def _profile_subtitle(record: dict[str, str]) -> str:
        profile_type = record.get("type")
        if profile_type == "file":
            return "JSON  •  imported Xray/V2Ray configuration"
        return "LINK  •  VLESS, VMess, or Trojan"

    def _render_config_list(self) -> None:
        for widget in self.config_list.winfo_children():
            widget.destroy()
        records = self.library.records
        self.profile_count_label.configure(text=f"{len(self.library.records)} saved")
        disabled = self.app_state in {"starting", "running", "stopping"}
        if not records:
            empty = ctk.CTkLabel(
                self.config_list,
                text="No VPN profiles yet\nImport JSON or add a share link to begin.",
                justify="left", anchor="w", height=70, text_color=MUTED,
                font=(FONT, 11),
            )
            empty.grid(row=0, column=0, sticky="ew", padx=10, pady=8)
            return
        for row, record in enumerate(records):
            selected = record["id"] == self.selected_config_id
            button = ctk.CTkButton(
                self.config_list,
                text=f"{record['name']}\n{self._profile_subtitle(record)}",
                anchor="w", height=56, corner_radius=10,
                fg_color=BLUE_SOFT if selected else PANEL_ALT,
                hover_color=BLUE_HOVER if selected else GLASS_HIGHLIGHT,
                text_color=TEXT if selected else "#DBDEE1",
                font=(FONT, 11, "bold"),
                state="disabled" if disabled else "normal",
                command=lambda profile_id=record["id"]: self._select_config(profile_id),
            )
            button.grid(row=row, column=0, sticky="ew", pady=(0, 6))

    def _refresh_config_choices(self, preferred_id: str | None = None) -> None:
        self.record_by_id = {record["id"]: record for record in self.library.records}
        wanted = preferred_id or str(self.settings.get("config_id", ""))
        if wanted not in self.record_by_id:
            wanted = self.library.records[0]["id"] if self.library.records else ""
        if wanted:
            self._select_config(wanted, persist=False)
        else:
            self.selected_config_id = ""
            self.config_detail.configure(text="No VPN profile selected")
            self._render_config_list()
            self.remove_button.configure(state="disabled")
            self._update_summary()

    def _select_config(self, profile_id: str, persist: bool = True) -> None:
        record = self.record_by_id.get(profile_id)
        if record is None:
            return
        self.selected_config_id = profile_id
        config_type = self._profile_subtitle(record).replace("  •  ", " · ")
        self.config_detail.configure(text=f"Selected: {record['name']}  •  {config_type}")
        if persist:
            self.settings["config_id"] = profile_id
            try:
                _atomic_json(SETTINGS_PATH, self.settings)
            except OSError as exc:
                self._append_log(f"WARNING: Could not remember selected profile: {exc}")
        self._render_config_list()
        self.remove_button.configure(state="normal" if self.app_state == "idle" else "disabled")
        self._update_summary()

    def _routing_mode_changed(self, mode: str) -> None:
        self.settings["routing_mode"] = mode
        if self.app_state == "idle" and mode != MODE_PROXY:
            self.process_entry.configure(state="normal")
        if mode == MODE_DISCORD:
            try:
                current = secondnet.parse_process_names(self.process_entry.get()) if self.process_entry.get().strip() else []
            except secondnet.V2PathError:
                current = []
            required = secondnet.parse_process_names(_default_discord_targets())
            existing = {item.casefold() for item in current}
            for item in required:
                if item.casefold() not in existing:
                    current.append(item)
                    existing.add(item.casefold())
            self.process_entry.delete(0, "end")
            self.process_entry.insert(0, ", ".join(current))
            detail = "Only listed apps' UDP uses V2Path  •  use Proxifier for Discord TCP\nVPN SOCKS5 stays VPN-bound; DIRECT SOCKS5 stays on the selected internet."
        elif mode == MODE_TUN:
            detail = "Creates “V2Path TUN”  •  selected apps use VPN  •  others direct\nAll public IPv4 traffic enters TUN before process routing."
        else:
            detail = "No transparent routing  •  VPN SOCKS5 uses the profile; DIRECT SOCKS5 uses the selected internet."
        self.routing_detail.configure(text=detail)
        if self.app_state == "idle":
            state = "disabled" if mode == MODE_PROXY else "normal"
            self.process_entry.configure(state=state)
            self.process_button.configure(state=state)
            self.process_clear_button.configure(state=state)
        self.discord_launch_button.configure(
            state="normal" if self.app_state == "running" and mode == MODE_DISCORD else "disabled"
        )
        self._update_summary()

    @staticmethod
    def _find_discord_executable() -> Path | None:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Discord"
        candidates = [path for path in base.glob("app-*/Discord.exe") if path.is_file()]
        return max(candidates, key=lambda path: path.stat().st_mtime, default=None)

    def _restart_discord_direct(self, ask: bool = True) -> None:
        executable = self._find_discord_executable()
        if executable is None:
            if ask:
                messagebox.showerror("Discord not found", "Could not find the installed Discord.exe.", parent=self)
            else:
                self._append_log("WARNING: Discord could not be restarted because Discord.exe was not found.")
            return
        if ask and not messagebox.askyesno(
            "Restart Discord",
            "This will close every running Discord window and reopen the current installed Discord.exe.\n\n"
            "Configure Proxifier to send Discord.exe and Update.exe TCP to SOCKS5 127.0.0.1, while V2Path handles Discord UDP. Continue?",
            parent=self,
        ):
            return
        if not ask:
            self._append_log("Restarting Discord now that VPN and Discord UDP routing are ready…")
        threading.Thread(
            target=self._restart_discord_worker, args=(executable, ask), daemon=True,
        ).start()

    def _restart_discord_worker(self, executable: Path, report_error: bool) -> None:
        try:
            subprocess.run(
                ["taskkill.exe", "/F", "/IM", "Discord.exe"], capture_output=True,
                text=True, creationflags=CREATE_NO_WINDOW,
            )
            time.sleep(1)
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen(
                [str(executable)], cwd=str(executable.parent),
                close_fds=True, creationflags=flags,
            )
            self.events.put((
                "log",
                "Restarted Discord; the existing Proxifier rules handle TCP and V2Path handles UDP.",
            ))
        except OSError as exc:
            self.events.put(("error" if report_error else "log", f"Could not restart Discord: {exc}"))

    def _select_process(self) -> None:
        filename = filedialog.askopenfilename(
            parent=self, title="Select an application to tunnel",
            filetypes=[("Windows applications", "*.exe"), ("All files", "*.*")],
        )
        if not filename:
            return
        current = self.process_entry.get().strip()
        selected = str(Path(filename))
        values = secondnet.parse_process_names(current) if current else []
        if selected.casefold() not in {value.casefold() for value in values}:
            values.append(selected)
        self.process_entry.delete(0, "end")
        self.process_entry.insert(0, ", ".join(values))
        self._update_summary()

    def _is_admin(self) -> bool:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except OSError:
            return False

    def _restart_elevated(self) -> bool:
        executable = sys.executable
        parameters = (
            "--replace-existing"
            if getattr(sys, "frozen", False)
            else subprocess.list2cmdline([str(Path(__file__).resolve()), "--replace-existing"])
        )
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", executable, parameters, str(secondnet.APP_DIR), 1,
        )
        return int(result) > 32

    def _ensure_routing_prerequisites(self, mode: str) -> bool:
        if mode == MODE_PROXY:
            return True
        if not self._is_admin():
            if not messagebox.askyesno(
                "Administrator access required",
                f"{mode} needs Administrator access for transparent routing. Restart V2Path as Administrator?\n\n"
                "Proxy-only mode does not require Administrator access.", parent=self,
            ):
                return False
            if not self._restart_elevated():
                messagebox.showerror("Could not elevate", "Windows did not start the Administrator copy.", parent=self)
                return False
            self.after(100, self._exit_application)
            return False
        if mode == MODE_DISCORD:
            if not (secondnet.PROXIFYRE_SOURCE_DIR / "ProxiFyre.exe").is_file():
                messagebox.showerror(
                    "Per-app helper missing", "The bundled Discord routing helper is missing. Re-extract the full ZIP.",
                    parent=self,
                )
                return False
            if secondnet.packet_filter_installed():
                return True
            old_version = secondnet.packet_filter_version()
            installer = secondnet.PACKET_FILTER_INSTALLER
            if not installer.is_file():
                messagebox.showerror("Driver installer missing", "Re-extract the complete V2Path ZIP.", parent=self)
                return False
            old_text = ".".join(map(str, old_version)) if old_version else "not installed"
            if not messagebox.askyesno(
                "Install per-app network driver",
                f"Discord per-app mode requires the signed Windows Packet Filter 3.6.2.1.\n"
                f"Current version: {old_text}\n\nInstall or upgrade it now? Network adapters may reconnect briefly.",
                parent=self,
            ):
                return False
            result = subprocess.run(
                ["msiexec.exe", "/i", str(installer), "/passive", "/norestart"],
                creationflags=CREATE_NO_WINDOW,
            )
            if result.returncode not in {0, 1641, 3010}:
                messagebox.showerror("Driver installation failed", f"Windows Installer returned code {result.returncode}.", parent=self)
                return False
            installed = secondnet.packet_filter_version()
            if result.returncode in {1641, 3010} or installed is None or installed < secondnet.MIN_PACKET_FILTER_VERSION:
                messagebox.showinfo("Restart required", "Restart Windows, then start Discord per-app mode again.", parent=self)
                return False
            return True
        xray = secondnet.find_xray(None)
        if xray is None or not (xray.parent / "wintun.dll").is_file():
            messagebox.showerror(
                "Wintun runtime missing", "wintun.dll must be beside the bundled xray.exe. Re-extract the full ZIP.",
                parent=self,
            )
            return False
        return True

    def _import_config(self) -> None:
        filename = filedialog.askopenfilename(
            parent=self, title="Import Xray/V2Ray config", filetypes=[("JSON configs", "*.json"), ("All files", "*.*")]
        )
        if not filename:
            return
        try:
            record = self.library.import_file(Path(filename))
            self._refresh_config_choices(record["id"])
            self._append_log(f"Imported config: {record['name']}")
        except Exception as exc:
            messagebox.showerror("Could not import config", str(exc), parent=self)

    def _add_link(self) -> None:
        dialog = ctk.CTkInputDialog(text="Paste a VLESS, VMess, or Trojan share link:", title="Add VPN share link")
        link = dialog.get_input()
        if not link:
            return
        try:
            record = self.library.add_link(link)
            self._refresh_config_choices(record["id"])
            self._append_log(f"Added share link: {record['name']}")
        except Exception as exc:
            messagebox.showerror("Could not add share link", str(exc), parent=self)

    def _remove_config(self) -> None:
        record = self._selected_config()
        if not record:
            return
        if not messagebox.askyesno("Remove config", f"Remove “{record['name']}” from V2Path?", parent=self):
            return
        self.library.remove(record["id"])
        self.settings.pop("config_id", None)
        self._refresh_config_choices()

    def _ports(self) -> tuple[int, int]:
        try:
            socks = int(self.socks_entry.get().strip())
            direct_socks = int(self.direct_socks_entry.get().strip())
        except ValueError as exc:
            raise secondnet.V2PathError("VPN SOCKS5 and DIRECT SOCKS5 ports must be whole numbers.") from exc
        if not 1 <= socks <= 65535 or not 1 <= direct_socks <= 65535:
            raise secondnet.V2PathError("VPN SOCKS5 and DIRECT SOCKS5 ports must be between 1 and 65535.")
        if socks == direct_socks:
            raise secondnet.V2PathError("VPN SOCKS5 and DIRECT SOCKS5 ports must be different.")
        return socks, direct_socks

    def _update_summary(self) -> None:
        adapter = self.adapter_by_label.get(self.adapter_combo.get())
        record = self._selected_config()
        adapter_name = str(adapter.get("name")) if adapter else "No internet selected"
        config_name = record.get("name") if record else "No config selected"
        mode = self.mode_combo.get()
        self.summary_label.configure(text=f"Internet: {adapter_name}    •    Config: {config_name}    •    {mode}")
        try:
            socks, direct_socks = self._ports()
            self.endpoint_label.configure(
                text=f"VPN SOCKS5       127.0.0.1:{socks}\nDIRECT SOCKS5    127.0.0.1:{direct_socks}"
            )
        except secondnet.V2PathError:
            pass

    def _start(self) -> None:
        adapter = self.adapter_by_label.get(self.adapter_combo.get())
        record = self._selected_config()
        try:
            if not adapter:
                raise secondnet.V2PathError("Select a connected internet adapter first.")
            if not record:
                raise secondnet.V2PathError("Import and select a VPN profile first.")
            socks, direct_socks = self._ports()
            mode = self.mode_combo.get()
            process_names = secondnet.parse_process_names(self.process_entry.get()) if mode != MODE_PROXY else []
        except secondnet.V2PathError as exc:
            messagebox.showerror("Cannot start V2Path", str(exc), parent=self)
            return
        self.settings.update({
            "adapter": adapter["name"], "config_id": record["id"],
            "adapter_guid": adapter.get("guid"), "adapter_mac": adapter.get("mac"),
            "adapter_ifindex": adapter.get("ifIndex"),
            "socks_port": socks, "direct_socks_port": direct_socks,
            "routing_mode": mode, "routing_processes": self.process_entry.get().strip(),
        })
        for legacy in ("force_obs", "tunnel_enabled", "tunnel_processes", "http_port"):
            self.settings.pop(legacy, None)
        _atomic_json(SETTINGS_PATH, self.settings)
        if not self._ensure_routing_prerequisites(mode):
            return
        if self.child_job is None:
            messagebox.showerror(
                "Cannot safely start Xray",
                f"V2Path could not create its child-process safety guard:\n{self.child_job_error}",
                parent=self,
            )
            return
        self.stop_requested.clear()
        self.direct_proxy_stop_expected.clear()
        self.filter_stop_expected.clear()
        self._set_state("starting")
        self._clear_log()
        self._append_log(f"Starting with internet: {adapter['name']}")
        self._append_log(f"Using config: {record['name']}")
        self._append_log(f"Local proxies: VPN SOCKS5 127.0.0.1:{socks}  |  DIRECT SOCKS5 127.0.0.1:{direct_socks}")
        self._append_log(f"Mode: {mode}")
        if process_names:
            self._append_log(f"Target processes: {', '.join(process_names)}")
        threading.Thread(
            target=self._start_worker, args=(adapter, record, socks, direct_socks, mode, process_names), daemon=True
        ).start()

    def _start_worker(
        self, adapter: dict[str, object], record: dict[str, str],
        socks: int, direct_socks: int, mode: str, process_names: list[str],
    ) -> None:
        try:
            source = secondnet.load_source(self.library.source(record))
            bind_address = secondnet.adapter_ipv4_address(adapter)
            direct_interface = secondnet.get_default_ipv4_interface_name() if mode == MODE_TUN else None
            config, outbound_tag = secondnet.build_config(
                source, str(adapter["name"]), socks, 0,
                force_processes=process_names if mode == MODE_TUN else None,
                direct_interface_name=direct_interface,
            )
            log_config = config.setdefault("log", {})
            if isinstance(log_config, dict):
                log_config["loglevel"] = "warning" if mode == MODE_TUN else "info"
            secondnet.write_config(config, GENERATED_CONFIG)
            secondnet.check_ports([socks, direct_socks])
            xray = secondnet.find_xray(None)
            if xray is None:
                raise secondnet.V2PathError("Bundled Xray runtime is missing. Re-extract the complete V2Path ZIP.")
            node = secondnet.find_node()
            direct_script = secondnet.find_direct_socks_script()
            if node is None or direct_script is None:
                raise secondnet.V2PathError(
                    "The bundled Node.js direct-proxy runtime is missing. Re-extract the complete V2Path ZIP."
                )
            validation_path = GENERATED_CONFIG
            if mode == MODE_TUN:
                validation_config, _ = secondnet.build_config(
                    source, str(adapter["name"]), socks, 0,
                )
                validation_log = validation_config.setdefault("log", {})
                if isinstance(validation_log, dict):
                    validation_log["loglevel"] = "warning"
                secondnet.write_config(validation_config, VALIDATION_CONFIG)
                validation_path = VALIDATION_CONFIG
            validation = subprocess.run(
                [str(xray), "run", "-test", "-config", str(validation_path)],
                capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
            )
            if validation.returncode:
                detail = (validation.stderr or validation.stdout).strip()
                raise secondnet.V2PathError(f"Xray rejected the generated config:\n{detail}")
            if self.stop_requested.is_set():
                self.events.put(("state", "idle"))
                return
            self.events.put(("log", f"Validated VPN outbound “{outbound_tag}” bound to {adapter['name']}."))

            raw_dns = adapter.get("dnsServers", [])
            dns_servers = raw_dns if isinstance(raw_dns, list) else [raw_dns]
            direct_command = [
                str(node), str(direct_script),
                "--listen-host", "127.0.0.1",
                "--listen-port", str(direct_socks),
                "--bind-address", bind_address,
                "--dns-servers", ",".join(str(value) for value in dns_servers if value),
            ]
            direct_process = subprocess.Popen(
                direct_command, cwd=str(direct_script.parent), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                bufsize=1, creationflags=CREATE_NO_WINDOW,
            )
            assert self.child_job is not None
            try:
                self.child_job.assign(direct_process)
            except OSError:
                direct_process.terminate()
                direct_process.wait(timeout=5)
                raise
            with self.process_lock:
                self.direct_proxy_process = direct_process

            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if direct_process.poll() is not None:
                    output = direct_process.stdout.read().strip() if direct_process.stdout else ""
                    raise secondnet.V2PathError(
                        "The direct SOCKS5 background service could not start."
                        + (f"\n{output}" if output else "")
                    )
                try:
                    with socket.create_connection(("127.0.0.1", direct_socks), timeout=0.25):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise secondnet.V2PathError("Timed out waiting for the direct SOCKS5 background service.")
            threading.Thread(
                target=self._watch_direct_proxy, args=(direct_process,), daemon=True,
            ).start()
            self.events.put((
                "log",
                f"Node.js direct SOCKS5 127.0.0.1:{direct_socks} is bound to "
                f"{adapter['name']} ({bind_address}).",
            ))
            if direct_process.poll() is not None or self.stop_requested.is_set():
                raise secondnet.V2PathError("Direct SOCKS5 stopped before Xray could start.")
            if direct_interface:
                self.events.put(("log", f"Non-selected TUN traffic is bound direct to {direct_interface}."))
            process = subprocess.Popen(
                [str(xray), "run", "-config", str(GENERATED_CONFIG)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1, creationflags=CREATE_NO_WINDOW,
            )
            assert self.child_job is not None
            try:
                self.child_job.assign(process)
            except OSError:
                process.terminate()
                process.wait(timeout=5)
                raise
            with self.process_lock:
                self.process = process
            if self.stop_requested.is_set():
                process.terminate()
            else:
                if mode == MODE_TUN:
                    self.events.put(("log", "Xray is creating the visible “V2Path TUN” adapter."))
                    deadline = time.monotonic() + 25
                    tun_ready = False
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            break
                        try:
                            if secondnet.powershell_json(
                                "[bool](Get-NetAdapter -IncludeHidden -Name 'V2Path TUN' "
                                "-ErrorAction SilentlyContinue) | ConvertTo-Json -Compress"
                            ) is True:
                                time.sleep(0.5)
                                tun_ready = True
                                break
                        except Exception:
                            pass
                        time.sleep(0.5)
                    if process.poll() is not None:
                        output = process.stdout.read().strip() if process.stdout else ""
                        raise secondnet.V2PathError(
                            "Xray could not create the V2Path TUN adapter."
                            + (f"\n{output}" if output else "")
                        )
                    if not tun_ready:
                        process.terminate()
                        raise secondnet.V2PathError("Timed out waiting for the V2Path TUN adapter.")
                    secondnet.configure_windows_tun_routes()
                    self.events.put(("log", "Installed temporary IPv4 TUN routes; LAN routes remain direct."))
                elif mode == MODE_DISCORD:
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            break
                        try:
                            with socket.create_connection(("127.0.0.1", socks), timeout=0.25):
                                break
                        except OSError:
                            time.sleep(0.15)
                    else:
                        raise secondnet.V2PathError("Timed out waiting for the local SOCKS5 listener.")
                    if process.poll() is not None:
                        raise secondnet.V2PathError("Xray stopped before Discord routing could start.")
                    helper = secondnet.prepare_proxifyre(process_names, socks)
                    helper_version = secondnet.windows_file_version(helper)
                    if helper_version:
                        self.events.put(("log", f"Discord UDP helper: ProxiFyre {'.'.join(map(str, helper_version[:3]))}."))
                    secondnet.ensure_proxifyre_firewall_rule(helper)
                    self.events.put(("log", "Windows Firewall: Discord UDP relay allowed on all profiles."))
                    filter_process = subprocess.Popen(
                        [str(helper)], cwd=str(helper.parent), stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                        bufsize=1, creationflags=CREATE_NO_WINDOW,
                    )
                    try:
                        self.child_job.assign(filter_process)
                    except OSError:
                        filter_process.terminate()
                        filter_process.wait(timeout=5)
                        raise
                    with self.process_lock:
                        self.filter_process = filter_process
                    time.sleep(1)
                    if filter_process.poll() is not None:
                        output = filter_process.stdout.read().strip() if filter_process.stdout else ""
                        raise secondnet.V2PathError(
                            "Discord per-app routing could not start."
                            + (f"\n{output}" if output else " Check the packet-filter driver.")
                        )
                    self.events.put(("log", "Discord UDP-only routing is active; use Proxifier on SOCKS5 for Discord TCP."))
                    threading.Thread(target=self._watch_filter, args=(filter_process,), daemon=True).start()
                self.events.put(("state", "running"))
                if mode == MODE_DISCORD:
                    self.events.put(("restart_discord", None))
            assert process.stdout is not None
            for line in process.stdout:
                self.events.put(("log", line.rstrip()))
            code = process.wait()
            with self.process_lock:
                filter_process = self.filter_process
                direct_process = self.direct_proxy_process
            self.filter_stop_expected.set()
            self.direct_proxy_stop_expected.set()
            self._terminate_process(filter_process)
            self._terminate_process(direct_process)
            with self.process_lock:
                if self.process is process:
                    self.process = None
            self.events.put(("exit", code))
        except Exception as exc:
            self._terminate_children()
            self.events.put(("error", str(exc)))
            self.events.put(("state", "idle"))

    def _watch_direct_proxy(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is not None:
            for line in process.stdout:
                if line.strip():
                    self.events.put(("log", f"[Direct SOCKS5] {line.rstrip()}"))
        code = process.wait()
        with self.process_lock:
            if self.direct_proxy_process is process:
                self.direct_proxy_process = None
        if not self.stop_requested.is_set() and not self.direct_proxy_stop_expected.is_set():
            self.events.put(("error", f"Direct SOCKS5 background service stopped unexpectedly with exit code {code}."))
            self.stop_requested.set()
            self._terminate_children()

    def _watch_filter(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is not None:
            for line in process.stdout:
                if line.strip():
                    self.events.put(("log", f"[Discord routing] {line.rstrip()}"))
        code = process.wait()
        with self.process_lock:
            if self.filter_process is process:
                self.filter_process = None
        if not self.stop_requested.is_set() and not self.filter_stop_expected.is_set():
            self.events.put(("error", f"Discord per-app routing stopped unexpectedly with exit code {code}."))
            self._terminate_children()

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str] | None) -> None:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def _terminate_children(self) -> None:
        with self.process_lock:
            filter_process = self.filter_process
            direct_process = self.direct_proxy_process
            process = self.process
        self.filter_stop_expected.set()
        self.direct_proxy_stop_expected.set()
        self._terminate_process(filter_process)
        self._terminate_process(direct_process)
        self._terminate_process(process)
        with self.process_lock:
            if self.filter_process is filter_process:
                self.filter_process = None
            if self.direct_proxy_process is direct_process:
                self.direct_proxy_process = None
            if self.process is process:
                self.process = None

    def _stop(self) -> None:
        if self.app_state not in {"starting", "running"}:
            return
        self.stop_requested.set()
        self._set_state("stopping")
        self._append_log("Stopping V2Path background services…")
        threading.Thread(target=self._stop_worker, daemon=True).start()

    def _stop_worker(self) -> None:
        self._terminate_children()
        if self.app_state == "stopping":
            self.events.put(("state", "idle"))

    def _poll_events(self) -> None:
        pending_logs: list[str] = []
        try:
            for _ in range(500):
                kind, value = self.events.get_nowait()
                if kind == "log":
                    pending_logs.append(str(value))
                    continue
                self._append_logs(pending_logs)
                pending_logs.clear()
                if kind == "state":
                    self._set_state(str(value))
                    if value == "running":
                        self._append_log("Connected. Local proxy listeners are ready.")
                elif kind == "exit":
                    if not self.stop_requested.is_set() and value:
                        self._append_log(f"Xray stopped unexpectedly with exit code {value}.")
                    else:
                        self._append_log("V2Path stopped.")
                    self._set_state("idle")
                elif kind == "error":
                    self._append_log(f"ERROR: {value}")
                    if not self.closing:
                        if self.window_hidden:
                            icon = self.tray_icon
                            if icon is not None:
                                try:
                                    icon.notify(str(value), "V2Path error")
                                except (NotImplementedError, OSError):
                                    pass
                            self._show_window()
                        messagebox.showerror("V2Path error", str(value), parent=self)
                elif kind == "restart_discord":
                    if not self.closing and self.app_state == "running":
                        self._restart_discord_direct(ask=False)
                elif kind == "adapters":
                    self._apply_adapters(value)  # type: ignore[arg-type]
        except queue.Empty:
            pass
        self._append_logs(pending_logs)
        if not self.closing:
            self.after(100, self._poll_events)

    def _on_close(self) -> None:
        self._hide_to_tray()

    def _exit_application(self) -> None:
        self.closing = True
        self.stop_requested.set()
        self._terminate_children()
        try:
            socks, direct_socks = self._ports()
            self.settings.update({
                "socks_port": socks, "direct_socks_port": direct_socks,
                "routing_mode": self.mode_combo.get(),
                "routing_processes": self.process_entry.get().strip(),
            })
            for legacy in ("force_obs", "tunnel_enabled", "tunnel_processes", "http_port"):
                self.settings.pop(legacy, None)
            _atomic_json(SETTINGS_PATH, self.settings)
        except secondnet.V2PathError:
            pass
        if self.child_job is not None:
            self.child_job.close()
        if self.tray_icon is not None:
            self.tray_icon.stop()
            self.tray_icon = None
        self.destroy()


def main() -> None:
    replace_existing = "--replace-existing" in sys.argv[1:]
    try:
        single_instance = SingleInstanceGuard()
    except OSError as exc:
        ctypes.windll.user32.MessageBoxW(None, str(exc), "V2Path startup error", 0x10)
        return
    if not single_instance.primary:
        if replace_existing:
            replacement_requested = single_instance.request_replacement()
            single_instance.close()
            if replacement_requested:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    candidate = SingleInstanceGuard()
                    if candidate.primary:
                        single_instance = candidate
                        break
                    candidate.close()
                    time.sleep(0.1)
                else:
                    replacement_requested = False
            if not replacement_requested:
                ctypes.windll.user32.MessageBoxW(
                    None,
                    "The existing V2Path instance could not close for the Administrator restart.",
                    "V2Path",
                    0x10,
                )
                return
        else:
            activated = single_instance.activate_existing()
            single_instance.close()
            if activated:
                return
            ctypes.windll.user32.MessageBoxW(
                None,
                "V2Path is already running, but its window could not be activated. "
                "Use the notification-area icon to reopen it.",
                "V2Path",
                0x30,
            )
            return

    app = V2PathGUI()
    single_instance.watch(
        lambda: app.after(0, app._show_window),
        lambda: app.after(0, app._exit_application),
    )
    try:
        app.mainloop()
    finally:
        single_instance.close()


if __name__ == "__main__":
    main()