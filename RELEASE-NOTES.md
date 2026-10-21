# V2Path v3.2.1

Route selected applications' UDP traffic through a VPN using the internet interface you choose. Useful for voice calls, screen sharing, and streaming while keeping other applications on their usual connection.

Download `V2Path-V3.2.1.zip`, extract it completely, and run `V2Path.exe`.
Keep `_internal` beside the executable. Exit older V2Path versions from their
tray menu before launching this release.

This version includes the GPU-rendered interface, the supplied app icon, corrected
selected-interface contrast, and the Windows taskbar pinning fix. New installations
default to `Discord.exe` without automatically adding Discord's updater.

The per-app mode handles UDP; applications that also need TCP through the VPN can
use the local VPN SOCKS5 endpoint. The direct SOCKS5 endpoint uses the selected
interface without the VPN profile.