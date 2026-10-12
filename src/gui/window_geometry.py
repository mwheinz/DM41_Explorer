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

And for the position (GitHub issue #63): one saved from the last session
is used only if the window's title bar would land on a screen, so the
window can't open somewhere it can't be seen or dragged -- after a monitor
is unplugged, say. Positions are in the window system's own units, which
CustomTkinter passes through unscaled (unlike sizes).
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
# The position part of a Tk geometry string: "+x+y", where either number
# can be negative ("+-1200+100" on a monitor left of the main one).
_POSITION = re.compile(r"\+(-?\d+)\+(-?\d+)$")

# How much of the window's top edge must be on one screen for a saved
# position to be used: a strip TITLE_STRIP high (the title bar), at least
# MIN_VISIBLE_WIDTH of it across.
TITLE_STRIP = 30
MIN_VISIBLE_WIDTH = 100


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


def parse_position(geometry: str):
    """(x, y) from a Tk geometry string such as "1080x768+10+20" or
    "1080x768+-1200+100", or None if it has no position."""
    match = _POSITION.search(geometry or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def geometry_string(size, position=None) -> str:
    """ "WxH", or "WxH+X+Y" with a position (negative numbers as Tk writes
    them, "+-1200")."""
    text = f"{size[0]}x{size[1]}"
    if position is not None:
        text += f"+{position[0]}+{position[1]}"
    return text


def restored_position(saved, size, screens):
    """`saved` (x, y), or None if it shouldn't be used: the window's title
    bar -- its top TITLE_STRIP, at least MIN_VISIBLE_WIDTH of it across
    (less for a narrower window) -- must be on one of `screens`, each an
    (x, y, width, height) rectangle, and not above that screen's top edge.
    Otherwise the window could open off screen or with nothing to drag."""
    if saved is None:
        return None
    x, y = saved
    width = size[0]
    needed_width = min(MIN_VISIBLE_WIDTH, width)
    for left, top, screen_width, screen_height in screens:
        across = min(x + width, left + screen_width) - max(x, left)
        if across >= needed_width and top <= y <= top + screen_height - TITLE_STRIP:
            return saved
    return None


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


def centered_position(size, parent, screens):
    """Where to put a window of `size` (width, height) so it is centred on
    `parent`, an (x, y, width, height) rectangle -- a dialog over the main
    window (GitHub issue #64) -- but wholly on the screen that holds the
    parent's centre (the first of `screens` if none does). A window larger
    than that screen goes to its top-left corner."""
    width, height = size
    parent_x, parent_y, parent_width, parent_height = parent
    x = parent_x + (parent_width - width) // 2
    y = parent_y + (parent_height - height) // 2
    middle_x = parent_x + parent_width // 2
    middle_y = parent_y + parent_height // 2
    screen = next(
        (
            s
            for s in screens
            if s[0] <= middle_x < s[0] + s[2] and s[1] <= middle_y < s[1] + s[3]
        ),
        screens[0],
    )
    left, top, screen_width, screen_height = screen
    x = max(left, min(x, left + screen_width - width))
    y = max(top, min(y, top + screen_height - height))
    return x, y
