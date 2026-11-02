import unittest

import secondnet_gui as ui


class PerAppDefaultsTests(unittest.TestCase):
    def test_default_processes_and_migration(self):
        expected = 'Discord.exe, Update.exe, discordptb.exe, VencordInstaller.exe'
        self.assertEqual(ui.initial_processes({}), expected)
        self.assertEqual(ui.initial_processes({'routing_processes': 'Discord.exe'}), expected)
        self.assertEqual(ui.initial_processes({'routing_processes': 'discord.exe, update.exe'}), 'discord.exe, update.exe')

    def test_new_port_default(self):
        self.assertEqual(ui.initial_vpn_port({}), '10807')

    def test_old_default_migrates(self):
        for port in (10808, '10808'):
            self.assertEqual(ui.initial_vpn_port({'socks_port': port}), '10807')

    def test_custom_port_preserved(self):
        self.assertEqual(ui.initial_vpn_port({'socks_port': 12345}), '12345')
