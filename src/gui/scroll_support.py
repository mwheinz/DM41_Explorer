"""
macOS mouse-wheel / trackpad scroll support.

Two unrelated macOS scroll problems live in this one module, because both
are about the same thing -- what Tk does with a scroll gesture before the
widget underneath ever hears about it -- and they share the
<TouchpadScroll> delta decoding:

  * CTkScrollableFrame (Overview, Flags, Key Assignments) never sees a
    trackpad gesture at all. See bind_touchpad_scroll() below.
  * ttk.Treeview (Hex View, Data Registers, Programs, XM Files, Alarms)
    does see it, and scrolls in sudden multi-row jumps instead of
    following the finger -- GitHub issue #29. See SmoothTreeview below.

<TouchpadScroll> is a macOS-only event type -- Tk's X11 build (Linux)
doesn't define it, and binding an unknown event type raises TclError
immediately -- so every bind() of it here is guarded. Non-macOS platforms
keep whatever normal <MouseWheel>/<Button-4>/<Button-5> handling the
widget already has, which is why none of this is active off macOS unless
explicitly asked for (see TreeScrollTuning.everywhere).
"""

import dataclasses
import enum
import logging
import os
import platform
import tkinter
from tkinter import ttk

logger = logging.getLogger(__name__)

PLATFORM_SYSTEM = platform.system()


def decode_touchpad_delta(raw_delta: int):
    """(delta_x, delta_y), in pixels, from a <TouchpadScroll> event's
    `%D` field, which packs both axes into one integer: delta_x in the
    high 16 bits, delta_y in the low 16, each a signed 16-bit value.

    The same unpacking Tk itself does in `::tk::PreciseScrollDeltas`
    (library/tk.tcl), done here because the Python side never sees the
    Tcl helper's result -- a bound Python callback gets the raw packed
    `%D` in `event.delta`.

    Positive delta_y means the content should move down the screen (the
    gesture pushed it down), matching Tk's own sign convention for the
    event, hence the negation at both call sites below.
    """
    raw = raw_delta & 0xFFFFFFFF
    delta_y = raw & 0xFFFF
    delta_x = (raw >> 16) & 0xFFFF
    if delta_y >= 0x8000:
        delta_y -= 0x10000
    if delta_x >= 0x8000:
        delta_x -= 0x10000
    return delta_x, delta_y


def bind_touchpad_scroll(scrollable_frame):
    """Enables trackpad scrolling on a CTkScrollableFrame. Call once per
    frame, after it's been created. No-op on non-macOS platforms.

    CustomTkinter's CTkScrollableFrame wraps a plain Canvas that, on
    macOS, doesn't respond to a trackpad scroll gesture the way native
    widgets do -- only Tk's <TouchpadScroll> event carries that gesture,
    and nothing wires it to the canvas by default. Without this,
    scrolling one of these frames only works with an external mouse
    wheel (or a Magic Mouse), not the trackpad.

    Native ttk widgets (e.g. ttk.Treeview) don't need this -- they
    already receive <TouchpadScroll> on their own. What they do with it
    is a different problem; see SmoothTreeview.
    """
    if PLATFORM_SYSTEM != "Darwin":
        return

    def _trackpad_scroll(event):
        # <TouchpadScroll> is delivered via bind_all (see below), so every
        # frame using this helper gets a callback on every trackpad
        # gesture anywhere in the window, not just ones over its own
        # area -- winfo_ismapped() is what limits the actual scrolling to
        # whichever tab is actually visible right now.
        if not scrollable_frame.winfo_ismapped():
            return None
        _, delta_y = decode_touchpad_delta(event.delta)
        # yview() == (0.0, 1.0) means the whole scrollregion is already
        # visible -- i.e. this tab's content fits inside the window with
        # room to spare. Tk's Canvas doesn't clamp yview_scroll() to a
        # no-op in that case the way it does once content overflows: when
        # the scrollregion is smaller than the canvas, yview_scroll happily
        # slides the (already fully visible) content around inside the
        # extra space instead of refusing the move, which is exactly what
        # let a two-finger trackpad gesture drag Overview's cards up/down
        # even though nothing was actually clipped. Mirrors the same guard
        # CTkScrollableFrame's own built-in `_mouse_wheel_all()` already
        # uses for regular <MouseWheel>/<Button-4>/<Button-5> scrolling
        # (see customtkinter/windows/widgets/ctk_scrollable_frame.py) --
        # <TouchpadScroll> just never had it, since this handler is this
        # project's own addition, not part of CTkScrollableFrame itself.
        if delta_y and scrollable_frame._parent_canvas.yview() != (0.0, 1.0):
            scrollable_frame._parent_canvas.yview_scroll(-delta_y, "units")
        return "break"

    # bind_all (not bind): the event's target widget is whatever's under
    # the cursor -- often a child label/checkbox, not the scrollable frame
    # itself -- so a plain instance-level bind() would frequently miss it.
    try:
        scrollable_frame.bind_all("<TouchpadScroll>", _trackpad_scroll, add="+")
    except Exception as e:
        # Defensive: some Tk/Aqua build without this virtual event
        # shouldn't take the whole app down over a scroll nicety.
        logger.debug("Could not bind <TouchpadScroll>: %s", e)


# --------------------------------------------------------------------------
# ttk.Treeview scrolling (GitHub issue #29)
# --------------------------------------------------------------------------

# Fallback pixels-per-row, used only when the Treeview's ttk style doesn't
# report a rowheight (a bare ttk.Treeview that never went through
# gui/tab_common.py's style_treeview(), e.g. in tools/scroll_lab.py).
# Same base as gui/window_geometry.py's scaled_row_height().
DEFAULT_ROW_HEIGHT = 22

# Set this to tune scrolling without editing code -- see
# TreeScrollTuning.from_environment() for the syntax. Reading it once at
# import time (into TREE_SCROLL_TUNING below) is deliberate: it's a
# development knob for finding good defaults, not a user preference.
TUNING_ENVIRONMENT_VARIABLE = "DM41_TREE_SCROLL"


class ScrollAxis(enum.Enum):
    """Which axis a scroll event moves. The value is the prefix of the
    Tk view command for that axis ("yview_scroll"/"xview_scroll")."""

    VERTICAL = "y"
    HORIZONTAL = "x"


@dataclasses.dataclass
class TreeScrollTuning:
    """How SmoothTreeview turns scroll events into rows.

    Defaults are the starting point for the issue #29 experiment, not
    measured optima -- override them from the environment (see
    from_environment()) or interactively with tools/scroll_lab.py, then
    change these numbers once a set of values feels right on real
    hardware. Every field is read at event time, so a live tweak takes
    effect on the next gesture without rebuilding the widget.

    touchpad_gain
        Pixels of content per pixel of finger travel, for
        <TouchpadScroll>. 1.0 is one-to-one: the rows track the gesture
        the way a native macOS list does. Raise it for faster scrolling.
    touchpad_sample
        Handle every Nth <TouchpadScroll> event. 1 handles them all. Tk's
        own binding uses 5 (`if {%# %% 5 == 0}`), which is half of why
        issue #29 feels clunky -- it throws away four events out of five
        and then applies the fifth one's whole delta at once.
    wheel_divisor
        `event.delta` units per row for <MouseWheel>, i.e. a real wheel
        or a Magic Mouse. 40.0 is the factor Tk's own TtkScrollable
        binding uses (`tk::MouseWheel %W y %D -40.0`), so the default
        keeps the stock wheel speed and changes only how the leftovers
        are treated (see RowAccumulator).
    max_rows
        Most rows one event may scroll, so a fast flick can't jump the
        whole table. 0 disables the cap.
    row_height
        Pixels per row, for converting touchpad pixels into rows. 0 (the
        default) reads it from the widget's own ttk style, which is what
        style_treeview() sets from the application font size.
    trace
        Log every scroll event at DEBUG -- what came in, what it became.
        Off by default because a trackpad produces these at 60-120 Hz.
    everywhere
        Handle scroll events on every platform, not just macOS. Only for
        testing and for tools/scroll_lab.py: Linux and Windows don't have
        this problem, and their stock bindings are already better suited
        to their own wheel conventions.
    """

    touchpad_gain: float = 1.0
    touchpad_sample: int = 1
    wheel_divisor: float = 40.0
    max_rows: int = 24
    row_height: int = 0
    trace: bool = False
    everywhere: bool = False

    def applies(self) -> bool:
        """Whether SmoothTreeview should take over scroll handling at
        all. macOS only, unless `everywhere` says otherwise."""
        return self.everywhere or PLATFORM_SYSTEM == "Darwin"

    @classmethod
    def from_environment(cls, value: str = None) -> "TreeScrollTuning":
        """The tuning described by `value` (or by $DM41_TREE_SCROLL):
        comma-separated `key=value` pairs, e.g.

            DM41_TREE_SCROLL=gain=1.5,sample=1,wheel=30,maxrows=12

        Keys are the short names in _TUNING_KEYS below. Anything
        unrecognized or unparseable is logged and ignored, so a typo in a
        shell profile can't stop the app from starting."""
        tuning = cls()
        if value is None:
            value = os.environ.get(TUNING_ENVIRONMENT_VARIABLE, "")
        for setting in value.split(","):
            setting = setting.strip()
            if not setting:
                continue
            key, _, raw = setting.partition("=")
            entry = _TUNING_KEYS.get(key.strip().lower())
            if entry is None:
                logger.warning(
                    "Ignoring unknown %s setting %r (known: %s)",
                    TUNING_ENVIRONMENT_VARIABLE,
                    setting,
                    ", ".join(sorted(_TUNING_KEYS)),
                )
                continue
            field, convert = entry
            try:
                setattr(tuning, field, convert(raw.strip()))
            except (TypeError, ValueError):
                logger.warning(
                    "Ignoring malformed %s setting %r",
                    TUNING_ENVIRONMENT_VARIABLE,
                    setting,
                )
        if tuning.touchpad_sample < 1:
            logger.warning(
                "%s sample must be >= 1; using 1", TUNING_ENVIRONMENT_VARIABLE
            )
            tuning.touchpad_sample = 1
        if not tuning.wheel_divisor:
            logger.warning(
                "%s wheel must not be 0; using %s",
                TUNING_ENVIRONMENT_VARIABLE,
                cls.wheel_divisor,
            )
            tuning.wheel_divisor = cls.wheel_divisor
        return tuning


def _boolean(raw: str) -> bool:
    """A flag from the environment: "1"/"true"/"yes"/"on" are true,
    "0"/"false"/"no"/"off" are false, anything else is a ValueError (so
    from_environment() reports it rather than silently reading "maybe" as
    true)."""
    lowered = raw.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    raise ValueError(raw)


# Environment key -> (TreeScrollTuning field, parser). Short keys because
# these get typed by hand on a command line.
_TUNING_KEYS = {
    "gain": ("touchpad_gain", float),
    "sample": ("touchpad_sample", int),
    "wheel": ("wheel_divisor", float),
    "maxrows": ("max_rows", int),
    "rowheight": ("row_height", int),
    "trace": ("trace", _boolean),
    "everywhere": ("everywhere", _boolean),
}

# The tuning every SmoothTreeview uses unless handed its own. A module
# singleton so a running app (or tools/scroll_lab.py) can retune every
# table at once by assigning to its fields.
TREE_SCROLL_TUNING = TreeScrollTuning.from_environment()


class RowAccumulator:
    """Turns a stream of fractional row deltas into whole-row steps.

    A Treeview can only scroll in whole rows, so the fraction left over
    from each event has to be kept rather than rounded away: a gesture
    that asks for 0.4 rows per event should move a row every third event,
    not either nothing at all (truncation) or a row every time
    (rounding). This is the piece that makes a one-to-one trackpad gain
    possible at all -- a 10-pixel gesture against a 22-pixel row is
    "0.45 rows", which is only meaningful as an accumulated quantity.

    Reversing direction drops whatever was pending instead of having to
    pay it back first, so a flick up right after a flick down responds
    immediately.
    """

    def __init__(self):
        self._pending = 0.0

    def steps(self, rows: float, limit: int = 0) -> int:
        """Whole rows to scroll now for a request of `rows`, keeping the
        remainder for next time. `limit`, if nonzero, caps the magnitude
        of a single step (the excess is discarded, not banked -- the
        point of the cap is that one violent flick doesn't move the table
        a screenful, let alone keep moving it afterwards)."""
        if rows > 0 > self._pending or rows < 0 < self._pending:
            self._pending = 0.0
        self._pending += rows
        whole = int(self._pending)  # truncates toward zero, either sign
        self._pending -= whole
        if limit:
            whole = max(-limit, min(limit, whole))
        return whole

    def reset(self) -> None:
        """Forgets the pending fraction."""
        self._pending = 0.0


class SmoothTreeview(ttk.Treeview):
    """A ttk.Treeview that scrolls smoothly on macOS -- GitHub issue #29.

    ttk.Treeview has no `_mouse_wheel_all()` to override the way
    CustomTkinter's CTkScrollableFrame does: its wheel handling lives in
    Tcl, as *class* bindings shared by every ttk scrollable widget
    (library/ttk/utils.tcl copies them onto Treeview with
    `ttk::copyBindings TtkScrollable Treeview`). So the equivalent of
    overriding that method is to bind the same events on the instance --
    which Tk runs before the class bindings -- and return "break" so the
    stock handler doesn't also fire. That's what this class does.

    Why the stock handling feels clunky, read out of the Tcl that ships
    with this project's own macOS build (Tcl/Tk 9.1, bundled by
    PyInstaller under `_tk_data/`):

        bind TtkScrollable <TouchpadScroll> {
            if {%# %% 5 == 0} {
                lassign [tk::PreciseScrollDeltas %D] ... deltaY
                ... %W yview scroll [expr {-$deltaY}] units
            }
        }

    Two problems, and they compound:

      * `%# %% 5 == 0` handles one <TouchpadScroll> event in five and
        drops the rest. A trackpad delivers these continuously during a
        gesture, so four fifths of the finger movement is simply thrown
        away.
      * `yview scroll $deltaY units` feeds a *pixel* delta to a command
        whose unit is a *row*. A modest 8-pixel flick of the finger
        scrolls 8 rows, so the one event in five that does get through
        jumps ~10 rows at a time. Hence big discrete steps rather than
        content tracking the finger -- worst on the tall tables (Hex
        View's 768 registers, Data Registers) where you scroll the most.

    The wheel path (`tk::MouseWheel %W y %D -40.0`) is less broken but
    still lossy: `yview scroll` takes whole rows, so everything under one
    row's worth of a slow wheel notch rounds to nothing.

    This class instead converts both event kinds into fractional rows --
    touchpad pixels divided by the real row height, wheel delta divided
    by wheel_divisor -- and runs them through a RowAccumulator, so no
    part of a gesture is dropped and nothing jumps. See TreeScrollTuning
    for the knobs and tools/scroll_lab.py for trying values side by side
    against a stock Treeview.

    Off macOS this is a plain ttk.Treeview: no bindings are added at all
    (Linux/Windows wheel handling is already fine, and their stock
    bindings suit their own conventions), unless the tuning says
    `everywhere`.
    """

    def __init__(
        self, master=None, *, scroll_tuning: TreeScrollTuning = None, **kwargs
    ):
        super().__init__(master, **kwargs)
        self.scroll_tuning = scroll_tuning or TREE_SCROLL_TUNING
        self._row_steps = {
            ScrollAxis.VERTICAL: RowAccumulator(),
            ScrollAxis.HORIZONTAL: RowAccumulator(),
        }
        self._touchpad_events = 0
        if self.scroll_tuning.applies():
            self._bind_scroll_events()

    # -- event wiring ------------------------------------------------

    def _bind_scroll_events(self) -> None:
        """Binds the scroll events on this widget, ahead of the class
        bindings described in the class docstring. <MouseWheel> also
        catches <Option-MouseWheel> (Tk falls back to the less specific
        pattern when a bindtag has no exact match), which costs the stock
        Option-is-faster behavior -- a deliberate trade for one
        consistent speed."""
        self.bind("<MouseWheel>", self._on_mouse_wheel)
        self.bind("<Shift-MouseWheel>", self._on_shift_mouse_wheel)
        try:
            self.bind("<TouchpadScroll>", self._on_touchpad_scroll)
        except tkinter.TclError as e:
            # Expected on Tk builds without the event (X11); the wheel
            # bindings above still apply.
            logger.debug("Could not bind <TouchpadScroll>: %s", e)

    def _on_mouse_wheel(self, event, axis: ScrollAxis = ScrollAxis.VERTICAL):
        """<MouseWheel>: a wheel notch or a Magic Mouse swipe. `delta` is
        in the same units Tk's own binding divides by 40."""
        tuning = self.scroll_tuning
        rows = -event.delta / tuning.wheel_divisor
        scrolled = self._scroll(axis, rows)
        if tuning.trace:
            logger.debug(
                "wheel %s delta=%s -> %.3f rows -> scrolled %s",
                axis.value,
                event.delta,
                rows,
                scrolled,
            )
        return "break"

    def _on_shift_mouse_wheel(self, event):
        """<Shift-MouseWheel>: the same, sideways."""
        return self._on_mouse_wheel(event, ScrollAxis.HORIZONTAL)

    def _on_touchpad_scroll(self, event):
        """<TouchpadScroll>: a two-finger trackpad gesture, delivered
        continuously with pixel deltas for both axes packed into
        `delta`."""
        tuning = self.scroll_tuning
        self._touchpad_events += 1
        if (
            tuning.touchpad_sample > 1
            and self._touchpad_events % tuning.touchpad_sample
        ):
            return "break"
        delta_x, delta_y = decode_touchpad_delta(event.delta)
        row_height = self.scroll_row_height()
        scrolled = {}
        for axis, delta in (
            (ScrollAxis.VERTICAL, delta_y),
            (ScrollAxis.HORIZONTAL, delta_x),
        ):
            if delta:
                rows = -delta * tuning.touchpad_gain / row_height
                scrolled[axis.value] = self._scroll(axis, rows)
        if tuning.trace:
            logger.debug(
                "touchpad dx=%s dy=%s rowheight=%s -> scrolled %s",
                delta_x,
                delta_y,
                row_height,
                scrolled or "nothing",
            )
        return "break"

    # -- scrolling ---------------------------------------------------

    def _scroll(self, axis: ScrollAxis, rows: float) -> int:
        """Scrolls `axis` by as much of `rows` as is whole, banking the
        rest (RowAccumulator). Returns the rows actually scrolled."""
        steps = self._row_steps[axis].steps(rows, self.scroll_tuning.max_rows)
        if not steps:
            return 0
        view = self.yview() if axis is ScrollAxis.VERTICAL else self.xview()
        # Nothing is clipped on this axis, so there is nothing to scroll
        # to -- the same guard CTkScrollableFrame's own wheel handler
        # uses, and the one gui/overview_tab.py needed added for
        # <TouchpadScroll> (a Tk view that already spans 0.0-1.0 will
        # still happily shift when told to).
        if view == (0.0, 1.0):
            return 0
        if axis is ScrollAxis.VERTICAL:
            self.yview_scroll(steps, "units")
        else:
            self.xview_scroll(steps, "units")
        return steps

    def scroll_row_height(self) -> int:
        """Pixels per row, for turning trackpad pixels into rows: the
        tuning's own `row_height` if set, else the `rowheight` this
        widget's ttk style reports (what gui/tab_common.py's
        style_treeview() computed from the application font size), else
        DEFAULT_ROW_HEIGHT."""
        if self.scroll_tuning.row_height > 0:
            return self.scroll_tuning.row_height
        try:
            style_name = str(self.cget("style")) or "Treeview"
            height = int(ttk.Style(self).lookup(style_name, "rowheight"))
        except (TypeError, ValueError, tkinter.TclError):
            height = 0
        return height if height > 0 else DEFAULT_ROW_HEIGHT
