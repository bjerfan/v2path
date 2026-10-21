import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import secondnet


class ConfigTests(unittest.TestCase):
    def test_jsonc_stripping_keeps_comment_markers_in_strings(self):
        text = r'''{
          // comment
          "url": "https://example.com/a//b", /* another */
          "items": [1, 2,],
        }'''
        self.assertEqual(
            json.loads(secondnet.strip_jsonc(text)),
            {"url": "https://example.com/a//b", "items": [1, 2]},
        )

    def test_build_config_binds_only_proxy_and_replaces_inbounds_and_routing(self):
        source = {
            "inbounds": [{"listen": "0.0.0.0", "port": 9999, "protocol": "socks"}],
            "outbounds": [
                {"tag": "proxy", "protocol": "vless", "sendThrough": "10.0.0.2", "settings": {}},
                {"tag": "direct", "protocol": "freedom", "settings": {}},
            ],
            "routing": {"domainStrategy": "IPIfNonMatch", "rules": [{"outboundTag": "direct"}]},
        }
        result, tag = secondnet.build_config(source, "Wi-Fi", 10808, 10809)
        self.assertEqual(tag, "proxy")
        self.assertEqual(result["outbounds"][0]["streamSettings"]["sockopt"]["interface"], "Wi-Fi")
        self.assertNotIn("sendThrough", result["outbounds"][0])
        self.assertNotIn("streamSettings", result["outbounds"][1])
        self.assertEqual({item["listen"] for item in result["inbounds"]}, {"127.0.0.1"})
        socks_rule, direct_socks_rule = result["routing"]["rules"]
        self.assertEqual(socks_rule["inboundTag"], ["secondnet-socks"])
        self.assertEqual(socks_rule["outboundTag"], "proxy")
        self.assertEqual(direct_socks_rule["inboundTag"], ["secondnet-direct-socks"])
        direct_inbound = next(item for item in result["inbounds"] if item["tag"] == "secondnet-direct-socks")
        self.assertEqual(direct_inbound["protocol"], "socks")
        self.assertTrue(direct_inbound["settings"]["udp"])
        direct = next(item for item in result["outbounds"] if item["tag"] == direct_socks_rule["outboundTag"])
        self.assertEqual(direct["protocol"], "freedom")
        self.assertEqual(direct["streamSettings"]["sockopt"]["interface"], "Wi-Fi")
        self.assertEqual(result["routing"]["domainStrategy"], "IPIfNonMatch")
        self.assertEqual(source["inbounds"][0]["port"], 9999)  # source was not mutated

    def test_build_config_can_leave_direct_socks_to_background_dispatcher(self):
        source = {
            "outbounds": [
                {"tag": "proxy", "protocol": "vless", "settings": {}},
            ],
        }
        result, _ = secondnet.build_config(source, "Wi-Fi", 10808, 0)
        self.assertEqual(
            [(item["tag"], item["protocol"], item["port"]) for item in result["inbounds"]],
            [("secondnet-socks", "socks", 10808)],
        )
        self.assertFalse(any(
            rule.get("inboundTag") == ["secondnet-direct-socks"]
            for rule in result["routing"]["rules"]
        ))
        self.assertFalse(any(
            item.get("tag", "").startswith("secondnet-local-direct")
            for item in result["outbounds"]
        ))

    def test_vless_reality_share_link(self):
        link = (
            "vless://11111111-1111-1111-1111-111111111111@example.com:443"
            "?type=tcp&security=reality&sni=example.org&fp=chrome&pbk=publickey&sid=abcd"
        )
        config = secondnet.load_source(link)
        outbound = config["outbounds"][0]
        self.assertEqual(outbound["protocol"], "vless")
        self.assertEqual(outbound["streamSettings"]["network"], "raw")
        self.assertEqual(outbound["streamSettings"]["realitySettings"]["publicKey"], "publickey")

    def test_vless_tcp_http_obfuscation_share_link(self):
        link = (
            "vless://11111111-1111-1111-1111-111111111111@example.com:443"
            "?security=&type=tcp&path=%2Fhidden&headerType=http"
            "&host=cdn.example&encryption=none"
        )
        config = secondnet.load_source(link)
        stream = config["outbounds"][0]["streamSettings"]
        self.assertEqual(stream["security"], "none")
        self.assertEqual(stream["network"], "raw")
        request = stream["rawSettings"]["header"]["request"]
        self.assertEqual(request["path"], ["/hidden"])
        self.assertEqual(request["headers"]["Host"], ["cdn.example"])

    def test_vmess_share_link(self):
        payload = {
            "v": "2",
            "add": "vpn.example",
            "port": "443",
            "id": "11111111-1111-1111-1111-111111111111",
            "aid": "0",
            "net": "ws",
            "host": "cdn.example",
            "path": "/socket",
            "tls": "tls",
            "sni": "cdn.example",
        }
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        config = secondnet.load_source("vmess://" + encoded)
        stream = config["outbounds"][0]["streamSettings"]
        self.assertEqual(stream["network"], "websocket")
        self.assertEqual(stream["wsSettings"]["host"], "cdn.example")


class AdapterTests(unittest.TestCase):
    def test_adapter_ipv4_prefers_routable_address_over_link_local(self):
        adapter = {"name": "Wi-Fi", "ipv4": ["169.254.4.5", "192.168.50.20"]}
        self.assertEqual(secondnet.adapter_ipv4_address(adapter), "192.168.50.20")

    def setUp(self):
        self.adapters = [
            {"name": "Main", "guid": "{11111111-1111-1111-1111-111111111111}", "mac": "10-20-30-40-50-60", "status": "Up", "ifIndex": 16, "ipv4": ["192.168.1.2"], "gateways": ["192.168.1.1"], "physical": True, "description": "Ethernet"},
            {"name": "Wi-Fi", "guid": "{22222222-2222-2222-2222-222222222222}", "mac": "AA-BB-CC-DD-EE-FF", "status": "Up", "ifIndex": 3, "ipv4": ["192.168.70.2"], "gateways": ["192.168.70.1"], "physical": True, "description": "Wireless Adapter"},
        ]

    def test_auto_selects_wifi(self):
        self.assertEqual(secondnet.select_adapter(self.adapters, None)["name"], "Wi-Fi")

    def test_selects_by_gateway_ip_or_index(self):
        for selector in ("192.168.70.1", "192.168.70.2", "3"):
            self.assertEqual(secondnet.select_adapter(self.adapters, selector)["name"], "Wi-Fi")

    def test_cached_adapter_uses_stable_guid_after_name_and_index_change(self):
        remembered = {
            "adapter": "Old Wi-Fi name",
            "adapter_guid": "22222222-2222-2222-2222-222222222222",
            "adapter_ifindex": 99,
        }
        self.assertIs(secondnet.find_cached_adapter(self.adapters, remembered), self.adapters[1])

    def test_cached_adapter_migrates_legacy_name(self):
        self.assertIs(
            secondnet.find_cached_adapter(self.adapters, {"adapter": "wi-fi"}),
            self.adapters[1],
        )


class PerAppTunnelTests(unittest.TestCase):
    def test_process_names_are_normalized_and_deduplicated(self):
        self.assertEqual(
            secondnet.parse_process_names("discord.exe, OBS64.exe; discord.exe\nC:\\Apps\\Game.exe"),
            ["discord.exe", "OBS64.exe", "C:\\Apps\\Game.exe"],
        )

    def test_process_names_reject_proxy_loop(self):
        with self.assertRaises(secondnet.V2PathError):
            secondnet.parse_process_names("xray.exe")

    def test_xray_tun_routes_selected_processes_and_keeps_others_direct(self):
        source = {
            "outbounds": [{"tag": "vpn", "protocol": "vless", "settings": {}}],
        }
        config, tag = secondnet.build_config(
            source, "Wi-Fi", 10808, 10809,
            force_processes=["discord.exe", "obs64.exe", r"C:\Apps\Game.exe"],
            direct_interface_name="Main",
        )
        self.assertEqual(tag, "vpn")
        tun = next(item for item in config["inbounds"] if item["protocol"] == "tun")
        self.assertEqual(tun["settings"]["name"], "V2Path TUN")
        self.assertEqual(tun["settings"]["mtu"], 1400)
        self.assertNotIn("autoSystemRoutingTable", tun["settings"])
        blocked = config["routing"]["rules"][2]
        self.assertIn("169.254.0.0/16", blocked["ip"])
        block_outbound = next(item for item in config["outbounds"] if item["tag"] == blocked["outboundTag"])
        self.assertEqual(block_outbound["protocol"], "blackhole")
        selected = config["routing"]["rules"][3]
        self.assertEqual(selected["process"], ["discord", "obs64", "C:/Apps/Game.exe"])
        self.assertEqual(selected["outboundTag"], "vpn")
        direct = config["routing"]["rules"][4]
        self.assertEqual(direct["inboundTag"], ["secondnet-tun"])
        direct_outbound = next(item for item in config["outbounds"] if item["tag"] == direct["outboundTag"])
        self.assertEqual(direct_outbound["protocol"], "freedom")
        self.assertEqual(direct_outbound["streamSettings"]["sockopt"]["interface"], "Main")

    def test_per_app_filter_preserves_local_socks_and_targets_only_requested_apps(self):
        config = secondnet.build_proxifyre_config(
            ["Discord.exe", r"C:\Users\User\AppData\Local\Discord\Update.exe"], 10808,
        )
        proxy = config["proxies"][0]
        self.assertEqual(proxy["socks5ProxyEndpoint"], "127.0.0.1:10808")
        self.assertEqual(proxy["supportedProtocols"], ["UDP"])
        self.assertEqual(proxy["supportedAddressFamilies"], ["IPv4"])
        self.assertEqual(proxy["appNames"][0], "Discord.exe")
        self.assertTrue(config["bypassLan"])
        self.assertEqual(config["logLevel"], "Info")

    def test_installed_filter_version_must_meet_minimum(self):
        from unittest import mock
        with mock.patch.object(secondnet, "packet_filter_version", return_value=(3, 6, 1, 1)):
            self.assertFalse(secondnet.packet_filter_installed())
        with mock.patch.object(secondnet, "packet_filter_version", return_value=(3, 6, 2, 1)):
            self.assertTrue(secondnet.packet_filter_installed())

    def test_old_proxifyre_is_rejected_before_runtime_copy(self):
        from unittest import mock
        with (
            mock.patch.object(secondnet, "PROXIFYRE_SOURCE_DIR", Path(__file__).parent),
            mock.patch.object(secondnet, "windows_file_version", return_value=(2, 2, 1, 0)),
        ):
            fake_executable = Path(__file__).parent / "ProxiFyre.exe"
            try:
                fake_executable.touch()
                with self.assertRaisesRegex(secondnet.V2PathError, "too old"):
                    secondnet.prepare_proxifyre(["Discord.exe"], 10808)
            finally:
                fake_executable.unlink(missing_ok=True)

    @unittest.skipUnless(os.name == "nt", "Windows firewall command test")
    def test_firewall_rule_is_program_scoped_and_all_profiles(self):
        from unittest import mock
        helper = Path(__file__).resolve()
        completed = subprocess.CompletedProcess([], 0, "Ok.", "")
        with mock.patch.object(secondnet.subprocess, "run", return_value=completed) as run:
            secondnet.ensure_proxifyre_firewall_rule(helper)
        add_command = run.call_args_list[1].args[0]
        self.assertIn(f"name={secondnet.PROXIFYRE_FIREWALL_RULE}", add_command)
        self.assertIn(f"program={helper}", add_command)
        self.assertIn("protocol=UDP", add_command)
        self.assertIn("profile=any", add_command)

@unittest.skipUnless(os.name == "nt", "Windows Job Object test")
class ProcessSafetyTests(unittest.TestCase):
    def test_closing_job_kills_assigned_child(self):
        process = subprocess.Popen(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "Start-Sleep -Seconds 30"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        job = secondnet.WindowsChildJob()
        try:
            job.assign(process)
            job.close()
            process.wait(timeout=5)
            self.assertIsNotNone(process.returncode)
        finally:
            job.close()
            if process.poll() is None:
                process.kill()
                process.wait()


if __name__ == "__main__":
    unittest.main()