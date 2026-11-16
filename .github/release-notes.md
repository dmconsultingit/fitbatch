## Download

| System | File |
|---|---|
| Windows 10/11 (64-bit) | `FitBatch.exe` |
| macOS on Apple Silicon (M1 and newer) | `FitBatch-macOS-arm64.zip` |
| macOS on Intel | `FitBatch-macOS-intel.zip` |

Not sure which Mac you have? Apple menu → **About This Mac**: "Chip Apple M…" means Apple Silicon, "Processor … Intel" means Intel.

No installer: FitBatch runs straight from the downloaded file. The first start takes a little longer while it unpacks.

### Windows

Download `FitBatch.exe` and run it. The file is not signed yet, so Windows SmartScreen may show "Windows protected your PC". Click **More info → Run anyway**.

### macOS

1. Download the zip for your Mac and double-click it. Move `FitBatch.app` to **Applications**.
2. Open FitBatch. macOS will say it cannot verify the app (it is not signed with an Apple developer certificate yet). Click **Done**.
3. Open **System Settings → Privacy & Security**, scroll down and click **Open Anyway** next to FitBatch, then confirm.

You only need to do this once. On macOS 14 and older you can instead right-click the app and choose **Open**.

### Check the download (optional)

Each file has a matching `.sha256` checksum. Windows: `Get-FileHash FitBatch.exe`. macOS: `shasum -a 256 FitBatch-macOS-arm64.zip` (or `-intel.zip`).

## Your data

Your Garmin password is used only to log in and is never saved. The login session is stored encrypted: in Windows DPAPI on Windows and in the Keychain on macOS.

This is an early test version. Please report problems in [Issues](https://github.com/dmconsultingit/fitbatch/issues).
