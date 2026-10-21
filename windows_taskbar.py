"""Explicit Windows shell identity and icon metadata for taskbar pinning."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys
import uuid

APP_ID = "ZzeK.V2Path.Desktop"


class GUID(ctypes.Structure):
    _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
                ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str) -> "GUID":
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", ctypes.c_uint32)]


class BLOB(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.c_void_p)]


class VALUE(ctypes.Union):
    _fields_ = [("pointer", ctypes.c_void_p), ("integer", ctypes.c_uint64), ("blob", BLOB)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_uint16), ("reserved1", ctypes.c_uint16),
                ("reserved2", ctypes.c_uint16), ("reserved3", ctypes.c_uint16), ("value", VALUE)]


def _check(result: int) -> None:
    if result < 0:
        raise OSError(f"Windows shell metadata failed (HRESULT 0x{result & 0xffffffff:08X})")


def set_process_identity() -> None:
    if os.name == "nt":
        function = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        function.argtypes = [ctypes.c_wchar_p]
        function.restype = ctypes.c_long
        _check(function(APP_ID))


def _property_store(hwnd: int) -> ctypes.c_void_p:
    interface = GUID.parse("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")
    pointer = ctypes.c_void_p()
    function = ctypes.windll.shell32.SHGetPropertyStoreForWindow
    function.argtypes = [ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    function.restype = ctypes.c_long
    _check(function(hwnd, ctypes.byref(interface), ctypes.byref(pointer)))
    return pointer


def _method(pointer: ctypes.c_void_p, index: int, result: object, *arguments: object) -> object:
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *arguments)(table[index])


def _key(pid: int) -> PROPERTYKEY:
    return PROPERTYKEY(GUID.parse("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid)


def set_window_identity(hwnd: int, icon: Path, script: Path) -> None:
    if os.name != "nt":
        return
    initialized = ctypes.windll.ole32.CoInitializeEx(None, 2)
    pointer = None
    try:
        pointer = _property_store(hwnd)
        executable = Path(sys.executable).resolve()
        command = subprocess.list2cmdline([str(executable)] if getattr(sys, "frozen", False) else [str(executable), str(script.resolve())])
        # Set relaunch properties first, then the window's explicit AppUserModelID.
        for pid, text in ((2, command), (3, f"{icon.resolve()},0"), (4, "V2Path"), (5, APP_ID)):
            buffer = ctypes.create_unicode_buffer(text)
            variant = PROPVARIANT()
            variant.vt = 31  # VT_LPWSTR; Python owns the buffer for this call.
            variant.value.pointer = ctypes.cast(buffer, ctypes.c_void_p).value
            key = _key(pid)
            setter = _method(pointer, 6, ctypes.c_long, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))
            _check(setter(pointer, ctypes.byref(key), ctypes.byref(variant)))
        _check(_method(pointer, 7, ctypes.c_long)(pointer))
    finally:
        if pointer:
            _method(pointer, 2, ctypes.c_ulong)(pointer)
        if initialized in (0, 1):
            ctypes.windll.ole32.CoUninitialize()


def window_identity(hwnd: int) -> dict[str, str]:
    initialized = ctypes.windll.ole32.CoInitializeEx(None, 2)
    pointer = None
    values = {}
    try:
        pointer = _property_store(hwnd)
        for pid, name in ((2, "command"), (3, "icon"), (4, "name"), (5, "appId")):
            key = _key(pid)
            variant = PROPVARIANT()
            getter = _method(pointer, 5, ctypes.c_long, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))
            _check(getter(pointer, ctypes.byref(key), ctypes.byref(variant)))
            try:
                values[name] = ctypes.wstring_at(variant.value.pointer) if variant.vt == 31 and variant.value.pointer else ""
            finally:
                ctypes.windll.ole32.PropVariantClear(ctypes.byref(variant))
        return values
    finally:
        if pointer:
            _method(pointer, 2, ctypes.c_ulong)(pointer)
        if initialized in (0, 1):
            ctypes.windll.ole32.CoUninitialize()