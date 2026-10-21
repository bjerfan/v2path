import base64
import json
import unittest
from unittest.mock import patch, Mock

from profile_import import clipboard_links, profile_details
import secondnet_gui as ui


VLESS = "vless://11111111-1111-1111-1111-111111111111@vpn.example.com:443?security=reality&type=tcp#Test"
TROJAN = "trojan://secret@backup.example.com:8443?security=tls&type=ws#Backup"


class ClipboardTests(unittest.TestCase):
    def test_subscription_json_file_and_arbitrary_text_are_ignored(self):
        for text in ("https://example.com/sub?token=secret", "http://example.com/sub", '{"outbounds": []}', "C:/config.json", "hello", ""):
            self.assertEqual(clipboard_links(text), [])

    def test_raw_links_deduplicate_and_ignore_subscription_lines(self):
        self.assertEqual(clipboard_links(f"\ufeff{VLESS}\nhttps://example.com/sub\n{TROJAN}\n{VLESS}"), [VLESS, TROJAN])

    def test_controller_ignores_subscription_without_loading_or_fetching_it(self):
        owner = Mock(app_state="idle", preview=False)
        owner.application.clipboard.return_value.text.return_value = "https://example.com/sub"
        with patch("secondnet.load_source") as loader:
            self.assertFalse(ui.Controller.importClipboard(owner))
            loader.assert_not_called()
            owner.library.add_link.assert_not_called()

    def test_controller_import_select_and_duplicate(self):
        owner = Mock(app_state="idle", preview=False)
        owner.application.clipboard.return_value.text.return_value = VLESS
        owner.library.records = []
        record = {"id": "one", "type": "link", "value": VLESS, "name": "Test"}
        owner.library.add_link.return_value = record
        self.assertTrue(ui.Controller.importClipboard(owner))
        owner.library.add_link.assert_called_once_with(VLESS)
        owner._refresh_config_choices.assert_called_once_with("one")
        owner.library.records = [record]
        self.assertTrue(ui.Controller.importClipboard(owner))
        owner.library.add_link.assert_called_once()

    def test_import_disabled_while_connected(self):
        owner = Mock(app_state="running", preview=False)
        self.assertFalse(ui.Controller.importClipboard(owner))
        owner.application.clipboard.assert_not_called()

    def test_malformed_raw_link_does_not_crash_or_log_credentials(self):
        owner = Mock(app_state="idle", preview=False)
        owner.application.clipboard.return_value.text.return_value = "vless://secret"
        owner.library.records = []
        owner.library.add_link.side_effect = ValueError("private credential")
        self.assertFalse(ui.Controller.importClipboard(owner))
        self.assertNotIn("private credential", owner._append_log.call_args.args[0])


class ProfileDetailsTests(unittest.TestCase):
    def test_vless_reality_tcp(self):
        self.assertEqual(profile_details(VLESS), ("vpn.example.com:443", "VLESS · REALITY · TCP"))

    def test_trojan_tls_ws_hides_password(self):
        self.assertEqual(profile_details(TROJAN), ("backup.example.com:8443", "TROJAN · TLS · WS"))

    def test_vmess_tls_ws(self):
        data = {"add": "vm.example.com", "port": "443", "id": "11111111-1111-1111-1111-111111111111", "net": "ws", "tls": "tls"}
        link = "vmess://" + base64.b64encode(json.dumps(data).encode()).decode()
        self.assertEqual(profile_details(link), ("vm.example.com:443", "VMESS · TLS · WS"))

    def test_ipv6_endpoint(self):
        self.assertEqual(profile_details("trojan://secret@[2001:db8::1]:443?security=tls")[0], "[2001:db8::1]:443")

    def test_invalid_saved_profile_is_safe(self):
        self.assertEqual(profile_details("vless://broken"), ("Server details unavailable", "Invalid or unavailable profile"))
