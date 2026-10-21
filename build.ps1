param([switch]$Preview)
$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$outputRoot = if ($Preview) { Join-Path $project 'local-preview' } else { $project }
$archiveName = if ($Preview) { 'V2Path-clipboard-preview.zip' } else { 'V2Path-V3.2.3.zip' }
$python = (Get-Command python).Source
$pythonDirectory = Split-Path $python
$previousPath = $env:PATH
try {
    # Avoid collecting unrelated ICU/API-shim DLLs from PDF/image tool folders.
    $env:PATH = "$pythonDirectory;$pythonDirectory\Scripts;$env:SystemRoot\System32;$env:SystemRoot"
    & $python -m PyInstaller --noconfirm --clean --windowed --name V2Path `
    --distpath "$outputRoot\dist" --workpath "$project\build\work" --specpath "$project\build" `
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
New-Item -ItemType Directory -Path "$outputRoot\dist\V2Path\configs" -Force | Out-Null
New-Item -ItemType Directory -Path "$outputRoot\release" -Force | Out-Null
Copy-Item -LiteralPath "$project\README.md" -Destination "$outputRoot\dist\V2Path\README.md"
Compress-Archive -Path "$outputRoot\dist\V2Path" `
    -DestinationPath "$outputRoot\release\$archiveName" -CompressionLevel Optimal -Force
Get-FileHash "$outputRoot\release\$archiveName" -Algorithm SHA256
