#!/usr/bin/env python3
"""Regenerates the README's screenshots (resources/screenshots/*.png).

Run from the repository root, with the project's environment active:

    python3 tools/make_screenshots.py [OUTPUT_DIR] [--only NAME ...]

It starts the real app in this process, with a throwaway preferences file
(your own ~/.dm41l_explorer.json is not read or written) and no serial
port, loads a sample state from src/tests/data, selects each tab, and
photographs the window from the screen. Each window must really be on
screen while it is captured, so do not cover it while the script runs.

The pictures are of the actual window with whatever look your platform
gives it (on a Mac that includes the title bar). They are written at the
screen's own pixel density, so a Retina Mac produces 2x images.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PIL import ImageGrab  # noqa: E402

DATA = ROOT / "src" / "tests" / "data"
DEFAULT_OUT = ROOT / "resources" / "screenshots"

# Ports offered in the connection picture: made up, so the picture does not
# depend on what is plugged into the machine that takes it.
SAMPLE_PORTS = ["/dev/cu.usbmodem14101", "/dev/cu.Bluetooth-Incoming-Port"]

# name -> (state file or None, tab, sub-tab or None, options)
# options: "size" is the window size; "first_row" scrolls the Hex View so
# that register address `first_row` is at the top.
SHOTS = {
    "overview": ("dm41x_manyfiles.dm41", "Overview", None, {}),
    "flags_view": ("dm41x_manyfiles.dm41", "Flags", None, {}),
    "program_view": ("dm41x_manyfiles.dm41", "Programs", None, {}),
    "key_assigns_dm41l": (
        "keyassigns.dm41",
        "Key Assignments",
        "DM41L",
        {},
    ),
    "key_assigns_hp41": (
        "keyassigns.dm41",
        "Key Assignments",
        "DM41X",
        {},
    ),
    "registers_view": ("dm41x_manyfiles.dm41", "Data Registers", None, {}),
    "xm_files": ("dm41x_manyfiles.dm41", "XM Files", None, {}),
    "hex_view": ("dm41x_manyfiles.dm41", "Hex View", None, {"first_row": 0x178}),
    "connection": (None, "Overview", None, {}),  # empty buffer plus the port dialog
}


def settle(app, seconds=0.6):
    """Lets Tk finish drawing: several event-loop passes over `seconds`."""
    end = time.time() + seconds
    while time.time() < end:
        app.update_idletasks()
        app.update()
        time.sleep(0.02)


def window_box(app):
    """(left, top, right, bottom) of `app`'s window on the screen in Tk
    units, including the title bar and borders the window manager adds."""
    # X11: ask for the window manager's frame window directly.
    frame = app.wm_frame()
    if isinstance(frame, str) and frame.startswith("0x"):
        try:
            out = subprocess.run(
                ["xwininfo", "-id", frame], capture_output=True, text=True, check=True
            ).stdout
            values = {}
            for line in out.splitlines():
                key, _, value = line.partition(":")
                values[key.strip()] = value.strip()
            left = int(values["Absolute upper-left X"])
            top = int(values["Absolute upper-left Y"])
            return left, top, left + int(values["Width"]), top + int(values["Height"])
        except (OSError, subprocess.CalledProcessError, KeyError, ValueError):
            pass  # fall through to the generic answer
    # macOS and Windows: the frame sits above the content area by the
    # difference between the content's and the frame's y.
    left = app.winfo_rootx()
    top = app.winfo_rooty()
    title = max(0, app.winfo_rooty() - app.winfo_y())
    return left, top - title, left + app.winfo_width(), top + app.winfo_height()


def park_pointer(app):
    """Moves the mouse pointer off the window so nothing shows as hovered."""
    try:
        app.event_generate(
            "<Motion>",
            rootx=app.winfo_screenwidth() - 2,
            rooty=app.winfo_screenheight() - 2,
            warp=True,
        )
    except Exception:  # not every platform allows warping the pointer
        pass


def grab(app, path):
    """Saves a picture of the window to `path`."""
    park_pointer(app)
    settle(app, 0.4)
    left, top, right, bottom = window_box(app)
    full = ImageGrab.grab()
    # Tk reports points; the screen picture is in pixels (2x on a Retina Mac).
    scale = full.width / app.winfo_screenwidth()
    box = tuple(round(v * scale) for v in (left, top, right, bottom))
    full.crop(box).save(path)
    print("wrote", path)


def pick_sub_tab(app, tab_name, sub_name):
    tab = app._tabs[tab_name]
    names = tab._layout_tabs._tab_dict.keys()
    match = next(n for n in names if sub_name.lower() in n.lower())
    tab._layout_tabs.set(match)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("out", nargs="?", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--only", nargs="+", choices=sorted(SHOTS))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    from config import ProjectConfig
    from engine.serial_manager import SerialManager
    from gui import app as app_module

    # A short, neutral place for the sample states, because the window
    # shows the path of the file that is open.
    base = Path("/tmp") if Path("/tmp").is_dir() else Path(tempfile.gettempdir())
    scratch = base / "DM41_Explorer"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    for state in {shot[0] for shot in SHOTS.values() if shot[0]}:
        shutil.copy(DATA / state, scratch / state)
    ProjectConfig.PREFS_FILE = scratch / "prefs.json"
    ProjectConfig.PREFS_FILE.write_text(
        '{"log_directory": "%s", "serial_port": "%s"}'
        % (scratch / "logs", SAMPLE_PORTS[0])
    )
    SerialManager.get_available_ports = lambda self: SAMPLE_PORTS

    app = app_module.DM41ExplorerApp()
    app.geometry("1080x768+40+40")
    app.lift()
    settle(app, 1.0)
    try:
        for name in args.only or SHOTS:
            state, tab, sub, options = SHOTS[name]
            app.geometry(options.get("size", "1080x768") + "+40+40")
            if state is None:
                app.memory_source = None
                app._on_state_received(app_module.Memory().to_string())
                app._set_status("Not connected")
            else:
                app._load_state_into_buffer(str(scratch / state))
            app.tabview.set(tab)
            app._on_tab_changed()
            settle(app)
            if sub:
                pick_sub_tab(app, tab, sub)
                settle(app)
            if "first_row" in options:
                tree = app.hex_view_tab._tree
                rows = tree.get_children()
                tree.yview_moveto(options["first_row"] / len(rows))
                settle(app)
            if name == "connection":
                from gui.port_dialog import PortSelectionDialog

                dialog = PortSelectionDialog(
                    app, app.serial, default_port=SAMPLE_PORTS[0]
                )
                dialog.geometry(
                    "+%d+%d" % (app.winfo_rootx() + 330, app.winfo_rooty() + 220)
                )
                settle(app, 0.8)
                grab(app, args.out / f"{name}.png")
                dialog.destroy()
            else:
                grab(app, args.out / f"{name}.png")
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
