"""Tests for gui/scroll_support.py -- the GitHub issue #29 Treeview
scrolling fix and its tuning.

The arithmetic (RowAccumulator, the environment parsing, the delta
decoding) is plain Python and tested as such. The widget half builds a
real SmoothTreeview and feeds it scroll events, with the tuning's
`everywhere` flag set so the macOS-only handler runs on whatever platform
the suite is running on; that needs a display (Xvfb in CI/sandboxes, like
test_app.py).

<TouchpadScroll> is a macOS-only event type that Tk's X11 build cannot
generate, so the trackpad tests call the bound handler with a synthetic
event rather than going through event_generate(). What all this cannot
test is how the result *feels*, which is why the tuning is adjustable at
all -- see tools/scroll_lab.py.
"""

import types

import pytest

pytest.importorskip("tkinter")

import tkinter
from tkinter import ttk

from gui.scroll_support import (
    DEFAULT_ROW_HEIGHT,
    RowAccumulator,
    ScrollAxis,
    SmoothTreeview,
    TreeScrollTuning,
    decode_touchpad_delta,
)
from gui_helpers import pump

ROWS = 200  # far more than the tree is tall, so there's always room to scroll


# --- the arithmetic ----------------------------------------------------


def test_whole_rows_pass_straight_through():
    accumulator = RowAccumulator()
    assert accumulator.steps(3.0) == 3
    assert accumulator.steps(-3.0) == -3


def test_fractions_accumulate_instead_of_rounding_away():
    """The point of the accumulator: a one-to-one trackpad gain asks for
    well under a row per event, which truncation would drop entirely."""
    accumulator = RowAccumulator()
    assert [accumulator.steps(0.4) for _ in range(3)] == [0, 0, 1]
    assert accumulator.steps(0.4) == 0  # 0.2 left over from the last row


def test_a_big_delta_is_capped_and_the_excess_discarded():
    accumulator = RowAccumulator()
    assert accumulator.steps(50.0, limit=10) == 10
    assert accumulator.steps(0.0, limit=10) == 0  # not 40 more rows next time


def test_zero_limit_means_uncapped():
    assert RowAccumulator().steps(50.0, limit=0) == 50


def test_reversing_direction_drops_the_pending_fraction():
    accumulator = RowAccumulator()
    assert accumulator.steps(0.75) == 0
    assert accumulator.steps(-0.75) == 0  # starts afresh, not 0.0 again
    assert accumulator.steps(-0.75) == -1


def test_reset_forgets_the_fraction():
    accumulator = RowAccumulator()
    accumulator.steps(0.9)
    accumulator.reset()
    assert accumulator.steps(0.9) == 0


@pytest.mark.parametrize(
    "raw, expected",
    [
        (0x00000000, (0, 0)),
        (0x00000005, (0, 5)),  # down 5 pixels
        (0x0000FFFB, (0, -5)),  # up 5 pixels
        (0x00030005, (3, 5)),  # both axes at once
        (0xFFFD0005, (-3, 5)),
    ],
)
def test_touchpad_deltas_unpack_both_axes(raw, expected):
    assert decode_touchpad_delta(raw) == expected


# --- the tuning --------------------------------------------------------


def test_default_tuning_keeps_tks_own_wheel_speed():
    """40.0 is the factor in Tk's own `tk::MouseWheel %W y %D -40.0`
    binding, so the wheel default changes nothing but the leftovers."""
    assert TreeScrollTuning().wheel_divisor == 40.0
    assert TreeScrollTuning().touchpad_gain == 1.0
    assert TreeScrollTuning().touchpad_sample == 1


def test_environment_sets_every_knob():
    assert TreeScrollTuning.from_environment(
        "gain=1.5,sample=2,wheel=30,maxrows=12,rowheight=20,trace=on,everywhere=yes"
    ) == TreeScrollTuning(
        touchpad_gain=1.5,
        touchpad_sample=2,
        wheel_divisor=30.0,
        max_rows=12,
        row_height=20,
        trace=True,
        everywhere=True,
    )


def test_an_empty_environment_is_the_defaults():
    assert TreeScrollTuning.from_environment("") == TreeScrollTuning()


@pytest.mark.parametrize(
    "setting", ["gain=fast", "nonsense=1", "sample=", "trace=maybe"]
)
def test_a_bad_setting_is_ignored_rather_than_fatal(setting):
    assert TreeScrollTuning.from_environment(f"{setting},gain=2.0").touchpad_gain == 2.0


def test_nonsensical_numbers_fall_back_to_something_usable():
    tuning = TreeScrollTuning.from_environment("sample=0,wheel=0")
    assert tuning.touchpad_sample == 1  # not a modulo-by-zero
    assert (
        tuning.wheel_divisor == TreeScrollTuning.wheel_divisor
    )  # not a divide-by-zero


def test_the_handler_is_macos_only_unless_told_otherwise(monkeypatch):
    monkeypatch.setattr("gui.scroll_support.PLATFORM_SYSTEM", "Linux")
    assert not TreeScrollTuning().applies()
    assert TreeScrollTuning(everywhere=True).applies()
    monkeypatch.setattr("gui.scroll_support.PLATFORM_SYSTEM", "Darwin")
    assert TreeScrollTuning().applies()


# --- the widget --------------------------------------------------------


@pytest.fixture
def root():
    r = tkinter.Tk()
    r.withdraw()
    yield r
    r.destroy()


def _tree(root, tuning=None, rows=ROWS, parent=None, **kwargs):
    tree = SmoothTreeview(
        parent if parent is not None else root,
        columns=("value",),
        show="headings",
        height=5,
        scroll_tuning=(
            tuning if tuning is not None else TreeScrollTuning(everywhere=True)
        ),
        **kwargs,
    )
    tree.heading("value", text="value")
    for index in range(rows):
        tree.insert("", "end", values=(index,))
    tree.pack(fill="both", expand=True)
    root.deiconify()
    pump(root, 0.3)
    return tree


def _first_row(tree) -> int:
    """Which row is at the top of the view, in rows rather than
    fractions."""
    return round(tree.yview()[0] * len(tree.get_children()))


def _touchpad(tree, delta_y=0, delta_x=0):
    packed = ((delta_x & 0xFFFF) << 16) | (delta_y & 0xFFFF)
    return tree._on_touchpad_scroll(types.SimpleNamespace(delta=packed))


def _wheel(tree, delta):
    return tree._on_mouse_wheel(types.SimpleNamespace(delta=delta))


def test_a_smooth_treeview_is_a_treeview(root):
    """Everything else in the GUI (apply_row_tags(), the tabs' own
    isinstance checks, ttk styling) must keep working unchanged."""
    assert isinstance(_tree(root), ttk.Treeview)


def test_a_real_wheel_event_reaches_the_handler(root):
    """Not just a direct call: the instance binding has to be in place
    and ahead of Tk's own class binding."""
    tree = _tree(root)
    tree.event_generate("<MouseWheel>", delta=-120, x=5, y=5)
    pump(tree, 0.1)
    assert _first_row(tree) == 3  # 120/40, Tk's own factor


def test_the_stock_binding_is_suppressed(root):
    assert _wheel(_tree(root), -120) == "break"


def test_a_slow_wheel_still_moves_eventually(root):
    """A notch worth less than a row scrolls nothing on its own -- Tk
    drops that remainder, this keeps it."""
    tree = _tree(root, TreeScrollTuning(everywhere=True, wheel_divisor=40.0))
    for _ in range(3):
        _wheel(tree, -10)  # 0.25 rows each
    assert _first_row(tree) == 0
    _wheel(tree, -10)
    assert _first_row(tree) == 1


def test_the_wheel_divisor_sets_the_speed(root):
    tree = _tree(root, TreeScrollTuning(everywhere=True, wheel_divisor=20.0))
    _wheel(tree, -120)
    assert _first_row(tree) == 6


def test_wheeling_back_up_returns_to_the_top(root):
    tree = _tree(root)
    _wheel(tree, -400)
    assert _first_row(tree) > 0
    _wheel(tree, 400)
    assert _first_row(tree) == 0


def test_a_trackpad_gesture_scrolls_pixels_not_rows(root):
    """The issue #29 bug itself: Tk's own binding would read a 10-pixel
    delta as 10 rows. At a one-to-one gain, 10 pixels is under half a
    22-pixel row, so nothing moves until the gesture has travelled a
    whole row's worth. (A negative delta is a gesture that scrolls the
    view down the table, matching Tk's own sign convention.)"""
    tree = _tree(root, TreeScrollTuning(everywhere=True, row_height=22))
    _touchpad(tree, delta_y=-10)
    assert _first_row(tree) == 0  # 0.45 rows
    _touchpad(tree, delta_y=-10)
    assert _first_row(tree) == 0  # 0.91 rows: not a whole row yet
    _touchpad(tree, delta_y=-10)
    assert _first_row(tree) == 1  # 1.36 rows


def test_the_gain_multiplies_the_gesture(root):
    tree = _tree(
        root, TreeScrollTuning(everywhere=True, row_height=10, touchpad_gain=2.0)
    )
    _touchpad(tree, delta_y=-15)
    assert _first_row(tree) == 3


def test_a_trackpad_gesture_back_up_returns_to_the_top(root):
    tree = _tree(root, TreeScrollTuning(everywhere=True, row_height=10))
    _touchpad(tree, delta_y=-100)
    assert _first_row(tree) == 10
    _touchpad(tree, delta_y=100)
    assert _first_row(tree) == 0


def test_one_gesture_cannot_jump_further_than_max_rows(root):
    tree = _tree(root, TreeScrollTuning(everywhere=True, row_height=1, max_rows=5))
    _touchpad(tree, delta_y=-100)
    assert _first_row(tree) == 5


def test_sampling_drops_events_the_way_tks_own_binding_does(root):
    """Tk handles one touchpad event in five; this is the same knob, so
    the two can be compared on real hardware."""
    tree = _tree(
        root, TreeScrollTuning(everywhere=True, row_height=10, touchpad_sample=5)
    )
    for _ in range(4):
        _touchpad(tree, delta_y=-100)
    assert _first_row(tree) == 0
    _touchpad(tree, delta_y=-100)
    assert _first_row(tree) == 10


def test_a_sideways_gesture_scrolls_sideways(root):
    """In a holder narrower than the column, so there is something to
    scroll to sideways in the first place."""
    holder = tkinter.Frame(root, width=120, height=120)
    holder.pack_propagate(False)
    holder.pack()
    tree = _tree(root, TreeScrollTuning(everywhere=True, row_height=10), parent=holder)
    tree.column("value", width=600, stretch=False)
    pump(root, 0.2)
    _touchpad(tree, delta_x=-100)
    assert tree.xview()[0] > 0
    assert tree.yview()[0] == 0


def test_a_table_that_fits_does_not_move(root):
    """Nothing is clipped, so a gesture must not shift the view -- the
    same overscroll guard gui/overview_tab.py needed."""
    tree = _tree(root, rows=2)
    assert tree.yview() == (0.0, 1.0)
    assert _touchpad(tree, delta_y=500) == "break"
    assert _wheel(tree, -500) == "break"
    assert tree.yview() == (0.0, 1.0)


def test_the_row_height_comes_from_the_ttk_style(root):
    """What gui/tab_common.py's style_treeview() sets from the
    application font size, so a bigger font scrolls proportionally."""
    ttk.Style(root).configure("Tall.Treeview", rowheight=44)
    tree = _tree(root, style="Tall.Treeview")
    assert tree.scroll_row_height() == 44


def test_an_unstyled_tree_falls_back_to_a_sane_row_height(root):
    ttk.Style(root).configure("Treeview", rowheight="")
    assert _tree(root).scroll_row_height() == DEFAULT_ROW_HEIGHT


def test_the_tuning_can_override_the_style(root):
    tree = _tree(root, TreeScrollTuning(everywhere=True, row_height=33))
    assert tree.scroll_row_height() == 33


def test_nothing_is_bound_off_macos(root, monkeypatch):
    """Linux and Windows keep their stock bindings -- this is a macOS
    fix, and their wheel conventions differ."""
    monkeypatch.setattr("gui.scroll_support.PLATFORM_SYSTEM", "Linux")
    tree = _tree(root, TreeScrollTuning())
    assert not tree.bind()


def test_both_axes_keep_their_own_pending_fraction(root):
    """A diagonal gesture must not have one axis eat the other's
    leftovers."""
    tree = _tree(root, TreeScrollTuning(everywhere=True, row_height=10))
    accumulators = tree._row_steps
    assert accumulators[ScrollAxis.VERTICAL] is not accumulators[ScrollAxis.HORIZONTAL]
    _touchpad(tree, delta_y=-5, delta_x=-5)
    assert _first_row(tree) == 0
    _touchpad(tree, delta_y=-5)
    assert _first_row(tree) == 1
