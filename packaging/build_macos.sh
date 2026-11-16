#!/usr/bin/env bash
# Build the macOS app: dist/FitBatch.app, zipped as dist/FitBatch-macOS-<arm64|intel>.zip.
#
# Run on a Mac from the repository root:   bash packaging/build_macos.sh
# PyInstaller cannot cross-compile, so the Windows .exe and the macOS app are built on their own
# systems (see .github/workflows/build.yml). The app is built for the Mac's own architecture:
# arm64 (Apple Silicon) or x86_64 (Intel).
#
# The app is not signed with an Apple Developer ID, so on first start macOS asks the user to
# confirm it (System Settings > Privacy & Security > Open Anyway).

set -euo pipefail
cd "$(dirname "$0")/.."

FLET="flet"
if [ -x .venv/bin/flet ]; then FLET=".venv/bin/flet"; fi
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/fitbatch/__init__.py)
case "$(uname -m)" in
    arm64) ARCH="arm64" ;;
    *) ARCH="intel" ;;
esac

rm -rf build dist FitBatch.spec

"$FLET" pack packaging/launcher.py \
    -n FitBatch \
    -i brand/fitbatch.icns \
    --product-name FitBatch \
    --product-version "$VERSION" \
    --bundle-id app.fitbatch.desktop \
    --copyright "Copyright 2026 Dawid Medyński. GPL-3.0-or-later." \
    --add-data "src/fitbatch/ui/assets:fitbatch/ui/assets" \
    --add-data "src/fitbatch/data:fitbatch/data" \
    --hidden-import keyring.backends.macOS \
    --distpath dist \
    -y \
    -- --exclude-module PIL --exclude-module pygments --exclude-module setuptools

# ditto keeps the bundle's code signature and symlinks intact (plain zip can break them).
ZIP="FitBatch-macOS-$ARCH.zip"
ditto -c -k --sequesterRsrc --keepParent dist/FitBatch.app "dist/$ZIP"
(cd dist && shasum -a 256 "$ZIP" > "$ZIP.sha256")
echo "Built dist/$ZIP ($(du -h "dist/$ZIP" | cut -f1)), version $VERSION"
