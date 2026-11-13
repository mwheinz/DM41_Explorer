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

# The README and license go next to the app, with what the README links
# to: its screenshots, and the XROM library guide (which links to the
# trigraph reference). Their relative links are kept as they are in the
# repository, so they work from the build output too.
copy_docs() {
    local dest="$1"
    cp ../README.md ../LICENSE "$dest/"
    mkdir -p "$dest/resources/screenshots" "$dest/docs"
    cp ../resources/screenshots/*.png "$dest/resources/screenshots/"
    cp ../docs/xrom_libraries.md ../docs/trigraphs.md "$dest/docs/"
}

# macOS requires at least an ad-hoc signature for the app to launch at all
# on Apple Silicon -- this is separate from (and needed even without) a
# real Apple Developer ID, which a signed and notarized release would also
# need. No-op elsewhere.
if [ "$(uname)" = "Darwin" ]; then
    codesign --force --deep --sign - "dist/DM41_Explorer.app"
    # The GPL asks that every binary distribution carry the license text.
    copy_docs dist
else
    # Drop a short README into that output directory so anyone
    # who unzips a release and sees an unfamiliar _internal folder next
    # to the exe knows it's required, not clutter. (A macOS .app has no
    # _internal folder beside it, so it doesn't get this file.)
    cp "../resources/dist_readme.txt" "dist/dm41explorer/README.txt"

    # Add a "sample" icon to the Linux bundle.
    if [ "$(uname)" = "Linux" ]; then
        cp "../resources/MyIcon.png" "dist/dm41explorer/MyIcon.png"
    fi

    # The GPL asks that every binary distribution carry the license text.
    copy_docs dist/dm41explorer
fi
