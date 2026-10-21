import queue
import unittest
from unittest.mock import Mock, patch

import secondnet_gui as ui


class DiscordRestartTests(unittest.TestCase):
    def owner(self, enabled=True, state="running"):
        owner = Mock(closing=False, preview=False, app_state=state)
        owner.settings = {"discord_auto_restart": enabled, "unrelated": "preserved"}
        owner.mode_combo.get.return_value = ui.legacy.MODE_DISCORD
        return owner

    def test_default_is_enabled_and_saved_false_is_respected(self):
        self.assertTrue(ui.discord_auto_restart_enabled({}))
        self.assertFalse(ui.discord_auto_restart_enabled({"discord_auto_restart": False}))

    def test_automatic_restart_when_enabled(self):
        owner = self.owner()
        ui.Controller._handle_discord_restart(owner)
        owner._restart_discord_direct.assert_called_once_with(ask=False)

    def test_disabled_does_not_touch_discord(self):
        owner = self.owner(False)
        ui.Controller._handle_discord_restart(owner)
        owner._restart_discord_direct.assert_not_called()
        self.assertIn("manually", owner._append_log.call_args.args[0])

    def test_stale_restart_ignored_when_not_running_closing_or_other_mode(self):
        for state in ("idle", "starting", "stopping"):
            owner = self.owner(state=state)
            ui.Controller._handle_discord_restart(owner)
            owner._restart_discord_direct.assert_not_called()
        owner = self.owner()
        owner.closing = True
        ui.Controller._handle_discord_restart(owner)
        owner._restart_discord_direct.assert_not_called()
        owner = self.owner()
        owner.mode_combo.get.return_value = ui.legacy.MODE_TUN
        ui.Controller._handle_discord_restart(owner)
        owner._restart_discord_direct.assert_not_called()

    def test_toggle_is_persisted_immediately_including_while_connected(self):
        for state in ("idle", "running"):
            owner = self.owner(state=state)
            with patch.object(ui.legacy, "_atomic_json") as save:
                ui.Controller.setDiscordAutoRestart(owner, False)
                self.assertFalse(owner.settings["discord_auto_restart"])
                self.assertEqual(owner.settings["unrelated"], "preserved")
                save.assert_called_once_with(ui.legacy.SETTINGS_PATH, owner.settings)
                owner.schedule_publish.assert_called_once()

    def test_save_failure_restores_previous_setting(self):
        owner = self.owner()
        with patch.object(ui.legacy, "_atomic_json", side_effect=OSError):
            ui.Controller.setDiscordAutoRestart(owner, False)
        self.assertTrue(owner.settings["discord_auto_restart"])
        owner._append_log.assert_called_once()

    def test_transition_does_not_change_preference(self):
        owner = self.owner(state="starting")
        with patch.object(ui.legacy, "_atomic_json") as save:
            ui.Controller.setDiscordAutoRestart(owner, False)
            save.assert_not_called()
        self.assertTrue(owner.settings["discord_auto_restart"])

    def test_manual_restart_stays_available_with_automatic_restart_off(self):
        owner = self.owner(False)
        ui.Controller.action(owner, "restartDiscord")
        owner._restart_discord_direct.assert_called_once_with()

    def test_restart_event_uses_the_preference_handler(self):
        owner = self.owner(False)
        owner.events = queue.Queue()
        owner.events.put(("restart_discord", None))
        ui.Controller._poll_events(owner)
        owner._handle_discord_restart.assert_called_once()
