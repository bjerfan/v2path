# V2Path v3.2.3

Route selected applications' UDP traffic through a VPN using the internet interface you choose. Useful for voice calls, screen sharing, and streaming while keeping other applications on their usual connection.

Download `V2Path-V3.2.3.zip`, extract it completely, and run `V2Path.exe`.
Keep `_internal` beside the executable. Exit older V2Path versions from their
tray menu before launching this release.

- Added a saved **Auto-restart Discord** toggle in the Applications section.
- Turn it off to leave Discord running after VPN connection; restart Discord or reconnect voice manually if needed.
- The **Restart Discord** button stays available while connected, regardless of the toggle.
- Auto-restart defaults to on to preserve existing behavior. The preference can be changed while disconnected or connected and is remembered between launches.
- Clipboard imports, detailed profile rows, and VPN/direct SOCKS5 behavior are unchanged.

The per-app mode handles UDP; applications that also need TCP through the VPN can
use the local VPN SOCKS5 endpoint. The direct SOCKS5 endpoint uses the selected
interface without the VPN profile.

Verify the download with `SHA256SUMS.txt`.

Made with ❤️ by ZzeK.
