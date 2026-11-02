import unittest

import secondnet_gui as ui


class DefaultTargetsTests(unittest.TestCase):
    def test_fresh_install_targets_discord_family(self):
        self.assertEqual(ui.initial_processes({}), ui.DEFAULT_PROCESSES)
        self.assertEqual(ui.legacy._default_discord_targets(), "Discord.exe")

    def test_migrates_previous_automatic_updater_pair(self):
        self.assertEqual(ui.initial_processes({"routing_processes": ui.PREVIOUS_DEFAULT_TARGETS}), ui.DEFAULT_PROCESSES)

    def test_migration_is_case_insensitive(self):
        self.assertEqual(ui.initial_processes({"routing_processes": ui.PREVIOUS_DEFAULT_TARGETS.upper()}), ui.DEFAULT_PROCESSES)

    def test_preserves_custom_application_list(self):
        targets = "Discord.exe, obs64.exe, Game.exe"
        self.assertEqual(ui.initial_processes({"routing_processes": targets}), targets)


if __name__ == "__main__":
    unittest.main()
