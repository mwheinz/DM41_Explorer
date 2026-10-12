"""
Where the monitors are (GitHub issue #63), so a saved window position can be
checked before it is used (gui/window_geometry.restored_position()).

Tk itself only knows the main screen's size on macOS and Windows, so a
window left on a second monitor would always look off screen. The operating
system is asked instead, through ctypes, which needs no extra package:

- macOS: CoreGraphics' active display list. Bounds are in points, with the
  origin at the main display's top-left and y increasing downward -- the
  same coordinates Tk uses for window positions there.
- Windows: EnumDisplayMonitors. Bounds are in physical pixels, which is what
  Tk uses once CustomTkinter has made the process DPI-aware.
- Anything else (X11): the root window already spans every monitor, so Tk's
  own screen size covers them all.

Every call falls back to the main screen as Tk reports it if the platform
query fails, so a broken query can only cost a remembered position, never
the window.
"""

import ctypes
import ctypes.util
import logging
import platform
from typing import List, Tuple

logger = logging.getLogger(__name__)

Rectangle = Tuple[int, int, int, int]  # x, y, width, height

_MAX_DISPLAYS = 32


def screen_rectangles(window) -> List[Rectangle]:
    """Every monitor's (x, y, width, height) in Tk's window coordinates,
    or just the main screen if they can't be listed. Never empty."""
    system = platform.system()
    try:
        if system == "Darwin":
            rectangles = _macos_displays()
        elif system == "Windows":
            rectangles = _windows_monitors()
        else:
            rectangles = []
    except Exception as e:  # a missing library or symbol, a bad call
        logger.info("Could not list the monitors (%s); using the main screen.", e)
        rectangles = []
    return rectangles or [
        (0, 0, window.winfo_screenwidth(), window.winfo_screenheight())
    ]


def _macos_displays() -> List[Rectangle]:
    class CGPoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

    class CGSize(ctypes.Structure):
        _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]

    class CGRect(ctypes.Structure):
        _fields_ = [("origin", CGPoint), ("size", CGSize)]

    path = ctypes.util.find_library("CoreGraphics") or (
        "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    )
    graphics = ctypes.cdll.LoadLibrary(path)
    graphics.CGGetActiveDisplayList.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    graphics.CGGetActiveDisplayList.restype = ctypes.c_int32
    graphics.CGDisplayBounds.argtypes = [ctypes.c_uint32]
    graphics.CGDisplayBounds.restype = CGRect

    displays = (ctypes.c_uint32 * _MAX_DISPLAYS)()
    count = ctypes.c_uint32(0)
    if graphics.CGGetActiveDisplayList(_MAX_DISPLAYS, displays, ctypes.byref(count)):
        return []
    rectangles = []
    for display in displays[: count.value]:
        bounds = graphics.CGDisplayBounds(display)
        rectangles.append(
            (
                round(bounds.origin.x),
                round(bounds.origin.y),
                round(bounds.size.width),
                round(bounds.size.height),
            )
        )
    return rectangles


def _windows_monitors() -> List[Rectangle]:
    from ctypes import wintypes

    rectangles = []
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    def found(_monitor, _dc, rect, _data):
        r = rect.contents
        rectangles.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return 1  # keep going

    if not ctypes.windll.user32.EnumDisplayMonitors(
        None, None, callback_type(found), 0
    ):
        return []
    return rectangles
