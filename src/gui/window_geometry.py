"""
Window sizing rules for the main window (GitHub issue #42, item 3).

Pure functions, no Tk, so the rules are unit-testable headless. gui/app.py
feeds them the facts only a live window knows (what its widgets need, how
big the screen is) and applies the answer.

The three rules:

  * The default size and the minimum size grow with the application font
    (Preferences > Font), because everything inside the window does.
  * The minimum size is never smaller than what the tab bar and status bar
    ask for, so a large font can't clip the tabs.
  * A size saved from the last session wins over the default, but is still
    held between the minimum and the screen.
"""

import re

# CustomTkinter's default UI font size; the sizes below are right for it.
BASE_FONT_SIZE = 13
BASE_DEFAULT_SIZE = (1080, 768)
BASE_MINIMUM_SIZE = (800, 560)

# A window never asks for more than this fraction of the screen, so it
# can't open larger than the display (height leaves room for the menu
# bar, title bar and Dock/taskbar).
MAX_SCREEN_FRACTION = (0.95, 0.90)

_GEOMETRY = re.compile(r"^(\d+)x(\d+)")


def font_scale(font_size) -> float:
    """How much bigger than the default the UI is: 1.0 at the default
    font size or below (a smaller font doesn't shrink the window), and
    proportionally more above it."""
    try:
        size = float(font_size)
    except (TypeError, ValueError):
        return 1.0
    return max(1.0, size / BASE_FONT_SIZE)


def parse_geometry(geometry: str):
    """(width, height) from a Tk geometry string such as "1080x768+10+20",
    or None if it isn't one."""
    match = _GEOMETRY.match(geometry or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _cap(screen):
    return (
        max(1, int(screen[0] * MAX_SCREEN_FRACTION[0])),
        max(1, int(screen[1] * MAX_SCREEN_FRACTION[1])),
    )


def minimum_window_size(font_size, needed, screen):
    """The smallest size the window may be resized to.

    `needed` is (width, height) the widgets ask for (tab bar, status bar);
    `screen` is the display's (width, height). The result is capped to the
    screen so a huge font can't make the window unreachable."""
    scale = font_scale(font_size)
    cap = _cap(screen)
    return tuple(
        min(max(int(base * scale), int(need)), limit)
        for base, need, limit in zip(BASE_MINIMUM_SIZE, needed, cap)
    )


def default_window_size(font_size, screen):
    """The size used when no size was saved: the base size scaled with
    the font, capped to the screen."""
    scale = font_scale(font_size)
    cap = _cap(screen)
    return tuple(
        min(int(base * scale), limit) for base, limit in zip(BASE_DEFAULT_SIZE, cap)
    )


def initial_window_size(saved, font_size, needed, screen):
    """The size to open with. `saved` is the (width, height) remembered
    from the last session, or None; anything unusable counts as None."""
    minimum = minimum_window_size(font_size, needed, screen)
    cap = _cap(screen)
    try:
        width, height = (int(saved[0]), int(saved[1]))
        if width <= 0 or height <= 0:
            raise ValueError
    except (TypeError, ValueError, IndexError):
        width, height = default_window_size(font_size, screen)
    return (
        min(max(width, minimum[0]), max(cap[0], minimum[0])),
        min(max(height, minimum[1]), max(cap[1], minimum[1])),
    )


def scaled_row_height(font_size, base_height=22) -> int:
    """Treeview row height for `font_size`: `base_height` pixels at the
    default font size, growing in proportion above it so rows aren't
    clipped by a larger font."""
    return max(base_height, int(round(base_height * font_scale(font_size))))


def scaled_width(width, font_size) -> int:
    """A pixel width (a Treeview column, say) for `font_size`: `width` at
    the default font size, growing in proportion above it so text isn't
    truncated by a larger font."""
    return int(round(width * font_scale(font_size)))


def dialog_size(font_size, base, needed, screen):
    """The size to give a dialog (GitHub issue #42, dialog sizes).

    `base` is the (width, height) the dialog wants at the default font size
    (0 for "whatever the content needs"); it grows with the font like
    everything else in the dialog. `needed` is the (width, height) the
    widgets ask for, which a dialog that can't scroll must always get --
    otherwise a large font clips it. `screen` is the display's size; no
    dialog is larger than the screen allows.
    """
    scale = font_scale(font_size)
    cap = _cap(screen)
    return tuple(
        min(max(int(wanted * scale), int(need)), limit)
        for wanted, need, limit in zip(base, needed, cap)
    )
