# V2Path v3.2.9

- The live log now retains only the latest 300 lines and redraws at most four times per second. This reduces UI stutter during bursts of traffic.
- Routine Xray info-level traffic messages are disabled by default; warnings and errors remain visible.

## v3.2.8

- Direct SOCKS5 now uses DNS server 8.8.8.8 when the selected network adapter reports no IPv4 DNS servers. Queries remain bound to that adapter; configured adapter DNS servers still take precedence.

- The packaged V2Path.exe now requests administrator rights at launch through its Windows manifest. Windows shows its normal UAC consent prompt; this is required for per-app packet routing and hosts-file controls.

- Added **Check for Update** beside Connect. It reads GitHub's latest public release and shows a release link only when newer; otherwise it says "This is the latest version available".
- Added **Make Backup** and **Restore Backup** for Windows hosts. Manual backups have a timestamped filename distinct from the automatic DNS-fix backup. Restore uses the newest manual backup after confirmation and saves a pre-restore recovery copy.
- The DNS fix now requires a manual backup first, and the UI shows the sequence: backup, connect VPN, fix, restart Discord. Undo remains available for an already-applied fix.

- New Per-App default list: Discord.exe, Update.exe, discordptb.exe, VencordInstaller.exe. The previous single-Discord default migrates; custom lists remain unchanged.

- Added a red **Fix Discord not opening** button between Applications and Ports.
- While connected, it resolves four Discord domains through the VPN using Cloudflare DNS-over-HTTPS, then adds public IPv4 answers to Windows hosts after confirmation.
- The button becomes **Undo DNS fix** after use; it removes only V2Path's marked entries. An original-byte backup is saved as `hosts.v2path.backup`.
- Hosts entries affect all applications on the PC, remain after V2Path exits, and may become stale. Undo or refresh them if Discord stops connecting later.
- Existing conflicting hosts entries are not changed. Discord must be restarted after applying or undoing the fix.

- Per-App is now the only GUI routing mode. Saved TUN/proxy-only selections migrate to Per-App.
- Selected applications use ProxiFyre for both TCP and UDP through the VPN.
- VPN SOCKS5 defaults to 127.0.0.1:10807. The previous default 10808 migrates to 10807; custom ports are preserved.
- Direct SOCKS5 remains on 10809 by default, using the selected interface without the VPN.
- The Discord auto-restart toggle and manual restart button remain available.

Important: configure Proxifier to bypass applications routed by V2Path, including matching catch-all rules. V2Path does not modify Proxifier profiles. Do not let both tools intercept the same application's TCP traffic.

Extract V2Path-V3.2.9.zip completely and run V2Path.exe. Keep _internal beside it. Exit older versions from the tray before starting this version. Update clients using the old VPN SOCKS5 port to 10807.

Made with ❤️ by ZzeK.
