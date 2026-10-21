"""Offline Qt Quick rendering and engine-compatibility checks."""
import json
from pathlib import Path
import time
import tkinter

from PySide6.QtCore import QObject, QTimer, QMetaObject


def schedule_checks(app, controller, window):
    import secondnet_gui as ui
    measurements = []
    frames = []
    failures = []
    idle_frames = []
    window.frameSwapped.connect(lambda: frames.append(time.perf_counter()))
    sizes = [(960, 660), (1160, 800), (1400, 900), (960, 660)]
    initial_rows = controller.profile_rows

    def check_size(index):
        try:
            if index:
                for name in ("connectionHeader", "connectButton", "internetCard", "profilesCard", "routingCard", "applicationsCard", "portsCard", "logsCard"):
                    item = window.findChild(QObject, name)
                    assert item is not None, name
                    assert item.property("width") > 30 and item.property("height") > 20, name
                measurements.append({"width": window.width(), "height": window.height(), "frames": len(frames)})
            if index < len(sizes):
                width, height = sizes[index]
                window.resize(width, height)
                QTimer.singleShot(400, lambda: check_size(index + 1))
            else:
                for name in ui.UNCHANGED_METHODS:
                    assert ui.Controller.__dict__[name] is ui.legacy.V2PathGUI.__dict__[name], name
                for mode in (ui.legacy.MODE_PROXY, ui.legacy.MODE_TUN, ui.legacy.MODE_DISCORD):
                    controller.setField("mode", mode)
                    assert controller._ports() == (10808, 10809)
                controller.setField("socksPort", "10809")
                try:
                    controller._ports()
                    raise AssertionError("Duplicate ports accepted")
                except ui.secondnet.V2PathError:
                    pass
                controller.setField("socksPort", "10808")
                for state in ("starting", "running", "stopping", "idle"):
                    controller._set_state(state)
                    assert controller.profile_rows is initial_rows
                controller._append_logs([f"Sample log line {i}" for i in range(200)])
                assert window.isVisible()
                assert window.rendererInterface().graphicsApi().name == "Direct3D11"
                assert len(frames) > 0, "No rendered frames"
                assert tkinter._default_root is None, "A Tk frontend was created"
                assert controller.view["modeLabel"] == "Per-App"
                assert window.findChild(QObject, "processEntry").property("placeholderText") == "Discord.exe"
                assert controller.process_entry.get() == "Discord.exe", "Updater is still in the default targets"
                assert not controller.icon.isNull(), "Application icon is missing"
                identity = ui.windows_taskbar.window_identity(int(window.winId()))
                assert identity["appId"] == ui.windows_taskbar.APP_ID
                assert identity["icon"].endswith("secondnet.ico,0")
                assert identity["name"] == "V2Path"
                assert "V2Path.exe" in identity["command"] or "secondnet_gui.py" in identity["command"]
                controller._set_state("running")
                window.resize(1160, 800)
                QTimer.singleShot(400, check_locked_selector)
        except Exception as exc:
            failures.append(str(exc))
            finish()

    def check_locked_selector():
        selector = window.findChild(QObject, "adapterChoice")
        if selector.property("opacity") != 1 or selector.property("enabled"):
            failures.append("Locked selected interface is faded or editable")
        output = ui.secondnet.RUNTIME_DIR
        output.mkdir(parents=True, exist_ok=True)
        window.grabWindow().save(str(output / "v3.2-connected-preview.png"))
        controller._set_state("idle")
        QTimer.singleShot(200, open_adapter_popup)

    def open_adapter_popup():
        selector = window.findChild(QObject, "adapterChoice")
        QMetaObject.invokeMethod(selector, "openPopupForCheck")
        QTimer.singleShot(200, lambda: check_adapter_popup(selector))

    def check_adapter_popup(selector):
        try:
            assert selector.property("selectedPopupTextColor").lower() == "#f3f6fc", "Selected popup row is not bright"
            output = ui.secondnet.RUNTIME_DIR
            window.grabWindow().save(str(output / "v3.2-dropdown-preview.png"))
            from unittest.mock import patch
            from PySide6.QtCore import Qt
            from PySide6.QtTest import QTest
            assert window.findChild(QObject, "clipboardImportButton") is not None
            assert controller.profile_rows.rows[0]["profileEndpoint"] == "vpn.example.com:443"
            assert controller.profile_rows.rows[0]["profileKind"] == "VLESS · REALITY · TCP"
            QMetaObject.invokeMethod(selector, "closePopupForCheck")
            window.requestActivate()
            app.processEvents()
            previous_clipboard = app.clipboard().text()
            try:
                controller.preview = False
                with patch.object(controller, "importClipboard") as importer:
                    app.clipboard().setText("vless://11111111-1111-1111-1111-111111111111@example.com:443")
                    QTest.keyClick(window, Qt.Key_V, Qt.ControlModifier)
                    assert importer.call_count == 1, "Ctrl+V did not trigger clipboard import"
                    app.clipboard().setText("https://example.com/subscription")
                    QTest.keyClick(window, Qt.Key_V, Qt.ControlModifier)
                    assert importer.call_count == 1, "Subscription paste was intercepted"
                    controller.app_state = "running"
                    app.clipboard().setText("trojan://secret@example.com:443")
                    QTest.keyClick(window, Qt.Key_V, Qt.ControlModifier)
                    assert importer.call_count == 1, "Connected mode allowed import"
            finally:
                controller.preview = True
                controller.app_state = "idle"
                app.clipboard().setText(previous_clipboard)
        except Exception as exc:
            failures.append(str(exc))
        QMetaObject.invokeMethod(selector, "closePopupForCheck")
        QTimer.singleShot(600, measure_idle)

    def measure_idle():
        idle_frames.append(len(frames))
        QTimer.singleShot(800, finish)

    def finish():
        idle_count = len(frames) - idle_frames[0] if idle_frames else None
        if idle_count is not None and idle_count > 4:
            failures.append(f"Idle UI rendered {idle_count} frames in 800ms")
        selected_adapter = None
        for i, line in enumerate(ui.QT_MESSAGES):
            if "using this adapter" in line and i:
                selected_adapter = ui.QT_MESSAGES[i - 1]
        hardware = selected_adapter is not None and "flags 0x0" in selected_adapter
        if not hardware:
            failures.append("Hardware graphics adapter was not confirmed")
        diagnostics = {
            "passed": not failures, "failures": failures, "measurements": measurements,
            "frameCount": len(frames), "graphicsApi": window.rendererInterface().graphicsApi().name,
            "qtMessages": ui.QT_MESSAGES,
            "idleFramesIn800ms": idle_count,
            "selectedAdapter": selected_adapter,
            "hardwareAccelerated": hardware,
        }
        errors = [m for m in ui.QT_MESSAGES if "ReferenceError" in m or "TypeError" in m or "Unable to assign" in m]
        if errors:
            diagnostics["passed"] = False
            diagnostics["failures"].extend(errors)
        output = ui.secondnet.RUNTIME_DIR
        output.mkdir(parents=True, exist_ok=True)
        (output / "gpu-smoke.json").write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
        window.grabWindow().save(str(output / "v3.2-preview.png"))
        controller.closing = True
        app.exit(0 if diagnostics["passed"] else 1)

    QTimer.singleShot(500, lambda: check_size(0))
