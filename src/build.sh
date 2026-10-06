#!/bin/bash
# Builds DM41_Explorer.app via PyInstaller. Run from src/ (or just
# ./build.sh from anywhere, it cd's there itself), with the project's venv
# active and its build requirements installed:
#
#   python3 -m venv dm41-venv
#   source dm41-venv/bin/activate
#   pip install -r requirements-dev.txt
#   (On Ubuntu) sudo apt install python3-tk
#   cd src
#   ./build.sh
#
# Output lands in src/dist/DM41_Explorer.app (macOS) or
# src/dist/dm41explorer/ (Linux/Windows).

set -e
cd "$(dirname "$0")"

rm -rf build dist

# Generate a version id for the about box.
echo "APP_VERSION = '$(git describe --tags --always)'" >dm41version.py

# Make the icons
bash ../resources/makeicon.sh

# Generate the binary
pyinstaller dm41explorer.spec

# macOS requires at least an ad-hoc signature for the app to launch at all
# on Apple Silicon -- this is separate from (and needed even without) a
# real Apple Developer ID; see docs/release_checklist.md. No-op elsewhere.
if [ "$(uname)" = "Darwin" ]; then
    codesign --force --deep --sign - "dist/DM41_Explorer.app"
    cp -r ../README.md "dist/README.md"
    # The GPL asks that every binary distribution carry the license text.
    cp ../LICENSE "dist/LICENSE"
else
    # Drop a short README into that output directory so anyone
    # who unzips a release and sees an unfamiliar _internal folder next
    # to the exe knows it's required, not clutter.
    cp "../resources/dist_readme.txt" "dist/dm41explorer/README.txt"

    # Add a "sample" icon to the Linux bundle.
    if [ "$(uname)" = "Linux" ]; then
        cp "../resources/MyIcon.png" "dist/dm41explorer/MyIcon.png"
    fi

    cp -r ../README.md "dist/dm41explorer/README.md"
    cp ../LICENSE "dist/dm41explorer/LICENSE"
fi
