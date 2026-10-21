# V2Path v3.2.2

Route selected applications' UDP traffic through a VPN using the internet interface you choose. Useful for voice calls, screen sharing, and streaming while keeping other applications on their usual connection.

Download `V2Path-V3.2.2.zip`, extract it completely, and run `V2Path.exe`.
Keep `_internal` beside the executable. Exit older V2Path versions from their
tray menu before launching this release.

- Replaced JSON import and the share-link dialog with **Import from clipboard**.
- Press **Ctrl+V** in the idle application to import raw VLESS, VMess, or Trojan links.
- Subscription URLs and other clipboard content are ignored without fetching them.
- Config rows show server address and protocol/security/transport, such as `VLESS · REALITY · TCP`.
- Duplicate links select the existing profile; malformed links are handled safely.
- Existing saved profiles and networking behavior are preserved.

The per-app mode handles UDP; applications that also need TCP through the VPN can
use the local VPN SOCKS5 endpoint. The direct SOCKS5 endpoint uses the selected
interface without the VPN profile.

Verify the download with `SHA256SUMS.txt`.

Made with ❤️ by ZzeK.
