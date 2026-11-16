# Build the portable Windows executable: dist\FitBatch.exe (single file, no installer).
#
# Run from the repository root:   powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1
#
# Notes
# - Flet 1.1.2 has a packaging bug in flet_cli\__pyinstaller\win_utils.py; run
#   packaging\patch_flet.py once after installing flet-cli (until a fixed Flet release is out).
# - Excluded modules are pulled in by the analysis but never used by FitBatch (saves ~11 MB):
#   Pillow (only Flet's RawImage uses it, lazily), pygments, setuptools; keyring is the
#   macOS/Linux secret store (Windows uses DPAPI).
# - The Flet desktop client (~45 MB, already compressed) is the bulk of the file; Flet has a
#   "light" client only for Linux.

$ErrorActionPreference = "Stop"
$flet = if (Test-Path .venv\Scripts\flet.exe) { ".venv\Scripts\flet.exe" } else { "flet" }  # CI: no venv

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
Remove-Item -Force FitBatch.spec -ErrorAction SilentlyContinue

& $flet pack packaging\launcher.py `
    -n FitBatch `
    -i src\fitbatch\ui\assets\brand\fitbatch.ico `
    --add-data "src\fitbatch\ui\assets;fitbatch\ui\assets" `
    --add-data "src\fitbatch\data;fitbatch\data" `
    --distpath dist `
    -y `
    -- --exclude-module PIL --exclude-module pygments --exclude-module setuptools `
       --exclude-module keyring

$exe = Get-Item dist\FitBatch.exe
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash
"{0}  {1}" -f $hash.ToLower(), $exe.Name | Out-File -Encoding ascii dist\FitBatch.exe.sha256
"Built {0} ({1:N1} MB), SHA-256 {2}" -f $exe.FullName, ($exe.Length / 1MB), $hash
