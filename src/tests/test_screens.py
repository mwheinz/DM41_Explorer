"""Tests for gui/screens.py, which lists the monitors so a saved window
position can be checked (GitHub issue #63). What the platform reports
can't be pinned down, so these check its shape and the fallback."""

import pytest

pytest.importorskip("tkinter")

import tkinter as tk

from gui import screens


@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    r.withdraw()
    yield r
    r.destroy()


def test_there_is_always_at_least_one_screen(root):
    rectangles = screens.screen_rectangles(root)
    assert rectangles
    for rectangle in rectangles:
        assert len(rectangle) == 4 and all(isinstance(v, int) for v in rectangle)
        assert rectangle[2] > 0 and rectangle[3] > 0


def test_the_main_screen_is_among_them(root):
    """The main screen's top-left is (0, 0) on every platform."""
    width, height = root.winfo_screenwidth(), root.winfo_screenheight()
    assert any(
        x <= 0 < x + w and y <= 0 < y + h
        for x, y, w, h in screens.screen_rectangles(root)
    ), (width, height)


@pytest.mark.parametrize(
    "system, query", [("Darwin", "_macos_displays"), ("Windows", "_windows_monitors")]
)
def test_a_failing_platform_query_falls_back_to_the_main_screen(
    root, monkeypatch, system, query
):
    def broken():
        raise OSError("no such library")

    monkeypatch.setattr(screens.platform, "system", lambda: system)
    monkeypatch.setattr(screens, query, broken)
    assert screens.screen_rectangles(root) == [
        (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
    ]


def test_other_platforms_use_tks_screen(root, monkeypatch):
    monkeypatch.setattr(screens.platform, "system", lambda: "Linux")
    assert screens.screen_rectangles(root) == [
        (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
    ]
