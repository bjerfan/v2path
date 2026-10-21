# Building V2Path

Windows x64 and Python 3.14 are required for the bundled build workflow.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-gui.txt
```

The repository does not store third-party runtime binaries. Download and fully
extract `V2Path-V3.2.1.zip` from the
[v3.2.1 release](https://github.com/bjerfan/v2path/releases/tag/v3.2.1), verify its
SHA-256 against `SHA256SUMS.txt`, and copy the contents of
`V2Path/_internal/tools/` into this checkout's `tools/` directory. Keep the
bundled licenses and ProxiFyre source archive with those components.

```powershell
python -m unittest discover -s tests -v
powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1
```

The output is `dist/V2Path/V2Path.exe`, its adjacent `_internal` directory,
and a portable ZIP under `release/`. Extract the ZIP completely before running.
Administrator access is needed for transparent per-app/TUN routing. VPN profiles
are not bundled; import your own profile and select your internet interface.

`secondnet.py` and related module filenames retain their original internal names
for compatibility. The application and Windows shell identity are V2Path.

To run the offline rendering checks, start `dist/V2Path/V2Path.exe --smoke-test`
on an interactive Windows desktop. It validates resizing, selected-interface
contrast, taskbar identity, and GPU rendering without opening a VPN connection.