"""Tests for gui/scroll_support.py -- the GitHub issue #29 Treeview
scrolling fix and its tuning.

The arithmetic (RowAccumulator, the environment parsing, the delta
decoding) is plain Python and tested as such. The widget tests build a
real SmoothTreeview but only check how it is wired up (its bindings and
row height), never where it scrolled to, which depends on the platform's
window system. How scrolling *feels* can't be tested at all, which is why
the tuning is adjustable -- see tools/scroll_lab.py.
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
    return tree


def _wheel(tree, delta):
    return tree._on_mouse_wheel(types.SimpleNamespace(delta=delta))


def test_a_smooth_treeview_is_a_treeview(root):
    """Everything else in the GUI (apply_row_tags(), the tabs' own
    isinstance checks, ttk styling) must keep working unchanged."""
    assert isinstance(_tree(root), ttk.Treeview)


def test_the_stock_binding_is_suppressed(root):
    assert _wheel(_tree(root), -120) == "break"


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
