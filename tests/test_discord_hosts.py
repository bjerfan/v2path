import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import discord_hosts


class DiscordHostsTests(unittest.TestCase):
    def test_apply_and_undo_preserve_other_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            hosts = Path(directory) / "hosts"
            original = b"# user line\r\n127.0.0.1 example.test\r\n"
            hosts.write_bytes(original)
            with patch.object(discord_hosts, "resolve_via_proxy", return_value="1.1.1.1") as resolver, patch.object(discord_hosts, "_flush_dns"):
                discord_hosts.apply(10807, hosts)
                self.assertEqual(resolver.call_count, len(discord_hosts.DOMAINS))
                self.assertTrue(discord_hosts.installed(hosts))
                self.assertEqual(hosts.with_name("hosts.v2path.backup").read_bytes(), original)
                discord_hosts.undo(hosts)
            self.assertEqual(hosts.read_bytes(), original)

    def test_conflicting_entry_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            hosts = Path(directory) / "hosts"
            original = b"10.10.34.36 updates.discord.com\n"
            hosts.write_bytes(original)
            with patch.object(discord_hosts, "resolve_via_proxy") as resolver:
                with self.assertRaisesRegex(RuntimeError, "conflict"):
                    discord_hosts.apply(10807, hosts)
                resolver.assert_not_called()
            self.assertEqual(hosts.read_bytes(), original)

    def test_reject_private_dns_result(self):
        response = '{"Status":0,"Answer":[{"name":"updates.discord.com.","type":1,"data":"10.10.34.36"}]}'
        with patch.object(discord_hosts.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = response
            with self.assertRaisesRegex(RuntimeError, "no public IPv4"):
                discord_hosts.resolve_via_proxy("updates.discord.com", 10807)
            self.assertIn("socks5h://127.0.0.1:10807", run.call_args.args[0])

    def test_cname_chain_can_resolve_public_address(self):
        response = '{"Status":0,"Answer":[{"name":"updates.discord.com.","type":5,"data":"cdn.example.net."},{"name":"cdn.example.net.","type":1,"data":"1.1.1.1"}]}'
        with patch.object(discord_hosts.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = response
            self.assertEqual(discord_hosts.resolve_via_proxy("updates.discord.com", 10807), "1.1.1.1")

    def test_manual_backup_is_distinct_and_restore_is_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            hosts = Path(directory) / "hosts"
            hosts.write_bytes(b"first\r\n")
            backup = discord_hosts.make_manual_backup(hosts)
            self.assertIn("hosts.v2path.manual.", backup.name)
            self.assertNotEqual(backup.name, "hosts.v2path.backup")
            hosts.write_bytes(b"changed\r\n")
            with patch.object(discord_hosts, "_flush_dns"):
                restored = discord_hosts.restore_manual_backup(hosts)
            self.assertEqual(restored, backup)
            self.assertEqual(hosts.read_bytes(), b"first\r\n")
            self.assertEqual(len(list(hosts.parent.glob("hosts.v2path.before-restore.*.bak"))), 1)

    def test_restore_without_manual_backup_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            hosts = Path(directory) / "hosts"
            hosts.write_bytes(b"unchanged")
            with self.assertRaisesRegex(RuntimeError, "No manual hosts backup"):
                discord_hosts.restore_manual_backup(hosts)
            self.assertEqual(hosts.read_bytes(), b"unchanged")
