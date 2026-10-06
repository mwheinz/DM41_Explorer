#!/bin/zsh
# Builds MyIcon.icns (macOS), MyIcon.ico (Windows), and MyIcon.png (Linux)
# from icon.png. Run from this directory.
#
# icon.png should be a large (e.g. 1024x1024) square PNG.
#
# dm41explorer.spec picks the right one of these automatically based on
# platform.system() -- see ICON_BY_OS there. MyIcon.png isn't wired into
# the spec (PyInstaller doesn't embed an icon into Linux binaries the way
# it does .ico/.exe on Windows or .icns/.app on macOS); it's generated
# here so it's ready to use as a .desktop file's Icon= entry whenever
# this project ships one.
#
# .icns generation uses macOS's own sips/iconutil, so that part only runs
# when this script is run on macOS. .ico and .png generation use Pillow
# instead (see requirements-dev.txt), so those two run on any platform.

set -e
cd "$(dirname "$0")"

if [ ! -f icon.png ]; then
    echo "icon.png not found in $(pwd) -- nothing to build." >&2
    exit 1
fi

# --- macOS: MyIcon.icns ---
if [ "$(uname)" = "Darwin" ]; then
    mkdir -p MyIcon.iconset
    for i in 64 128 256 512; do
        sips -z $i $i icon.png --out MyIcon.iconset/icon_${i}x${i}.png
    done
    iconutil -c icns MyIcon.iconset
    rm -rf MyIcon.iconset
    echo "Built MyIcon.icns"
else
    echo "Skipping MyIcon.icns (macOS only -- sips/iconutil aren't available here)."
fi

# --- Windows: MyIcon.ico, Linux: MyIcon.png (Pillow, any platform) ---
python3 - <<'PY'
from PIL import Image

src = Image.open("icon.png").convert("RGBA")

# Multi-resolution .ico -- Windows picks whichever size fits (taskbar,
# title bar, Explorer thumbnails, etc.) out of this one file.
ico_sizes = [16, 24, 32, 48, 64, 128, 256]
src.save("MyIcon.ico", sizes=[(s, s) for s in ico_sizes])
print("Built MyIcon.ico")

# Single 256x256 PNG -- the conventional size for a Linux .desktop
# Icon= entry / freedesktop hicolor icon theme.
src.resize((256, 256), Image.LANCZOS).save("MyIcon.png")
print("Built MyIcon.png")
PY
