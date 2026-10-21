$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$python = (Get-Command python).Source
$pythonDirectory = Split-Path $python
$previousPath = $env:PATH
try {
    # Avoid collecting unrelated ICU/API-shim DLLs from PDF/image tool folders.
    $env:PATH = "$pythonDirectory;$pythonDirectory\Scripts;$env:SystemRoot\System32;$env:SystemRoot"
    & $python -m PyInstaller --noconfirm --clean --windowed --name V2Path `
    --distpath "$project\dist" --workpath "$project\build\work" --specpath "$project\build" `
    --version-file "$project\windows-version-info.txt" `
    --icon "$project\assets\secondnet.ico" `
    --add-data "$project\assets;assets" `
    --add-data "$project\qml;qml" `
    --add-data "$project\tools;tools" `
    --add-data "$project\licenses;licenses" `
    --add-data "$project\THIRD-PARTY-NOTICES.txt;." `
    --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets `
    "$project\secondnet_gui.py"
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
} finally {
    $env:PATH = $previousPath
}
New-Item -ItemType Directory -Path "$project\dist\V2Path\configs" -Force | Out-Null
New-Item -ItemType Directory -Path "$project\release" -Force | Out-Null
Copy-Item -LiteralPath "$project\README.md" -Destination "$project\dist\V2Path\README.md"
Compress-Archive -Path "$project\dist\V2Path" `
    -DestinationPath "$project\release\V2Path-V3.2.1.zip" -CompressionLevel Optimal -Force
Get-FileHash "$project\release\V2Path-V3.2.1.zip" -Algorithm SHA256