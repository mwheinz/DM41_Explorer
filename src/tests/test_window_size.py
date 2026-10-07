"""Tests for GitHub issue #42 items 2 and 3: the status bar and the window
follow the application font, the window has a minimum size, and its size is
saved in the preferences and used at the next launch.

The first half tests gui/window_geometry.py's pure rules; the second half
builds a real DM41ExplorerApp (Xvfb in CI/sandboxes, like test_app.py).
"""
import json

import pytest

pytest.importorskip("customtkinter")

import customtkinter as ctk
from tkinter import ttk

from config import ProjectConfig
from gui import window_geometry as wg
from gui.window_geometry import (
    BASE_DEFAULT_SIZE,
    BASE_FONT_SIZE,
    BASE_MINIMUM_SIZE,
    default_window_size,
    font_scale,
    initial_window_size,
    minimum_window_size,
    parse_geometry,
    scaled_row_height,
    scaled_width,
)

BIG_SCREEN = (3840, 2160)
SMALL_SCREEN = (1280, 800)
NEEDED = (600, 330)  # what a default-font window's tab bar and status bar ask for


# -- The pure rules -------------------------------------------------------------


def test_font_scale():
    assert font_scale(BASE_FONT_SIZE) == 1.0
    assert font_scale(BASE_FONT_SIZE * 2) == 2.0
    assert font_scale(8) == 1.0, "a smaller font must not shrink the window"
    assert font_scale(None) == 1.0
    assert font_scale("junk") == 1.0


def test_parse_geometry():
    assert parse_geometry("1080x768+10+20") == (1080, 768)
    assert parse_geometry("640x480") == (640, 480)
    assert parse_geometry("1x1+0+0") == (1, 1)
    assert parse_geometry("junk") is None
    assert parse_geometry("") is None
    assert parse_geometry(None) is None


def test_default_size_at_the_default_font_is_the_base_size():
    assert default_window_size(BASE_FONT_SIZE, BIG_SCREEN) == BASE_DEFAULT_SIZE


def test_default_and_minimum_sizes_grow_with_the_font():
    big_font = BASE_FONT_SIZE * 1.5
    default = default_window_size(big_font, BIG_SCREEN)
    assert default[0] == int(BASE_DEFAULT_SIZE[0] * 1.5)
    assert default[1] == int(BASE_DEFAULT_SIZE[1] * 1.5)
    minimum = minimum_window_size(big_font, NEEDED, BIG_SCREEN)
    assert minimum[0] == int(BASE_MINIMUM_SIZE[0] * 1.5)
    assert minimum[1] == int(BASE_MINIMUM_SIZE[1] * 1.5)


def test_minimum_size_is_at_least_what_the_widgets_need():
    wide = (2000, 900)
    assert minimum_window_size(BASE_FONT_SIZE, wide, BIG_SCREEN) == wide
    assert minimum_window_size(BASE_FONT_SIZE, NEEDED, BIG_SCREEN) == BASE_MINIMUM_SIZE


def test_sizes_never_exceed_the_screen():
    for font in (BASE_FONT_SIZE, 24, 48):
        w, h = default_window_size(font, SMALL_SCREEN)
        assert w <= SMALL_SCREEN[0] * 0.95 and h <= SMALL_SCREEN[1] * 0.90
        w, h = minimum_window_size(font, (5000, 5000), SMALL_SCREEN)
        assert w <= SMALL_SCREEN[0] * 0.95 and h <= SMALL_SCREEN[1] * 0.90


def test_a_saved_size_is_used():
    assert initial_window_size((1000, 700), BASE_FONT_SIZE, NEEDED, BIG_SCREEN) == (1000, 700)


def test_a_saved_size_below_the_minimum_is_raised_to_it():
    assert initial_window_size((300, 200), BASE_FONT_SIZE, NEEDED, BIG_SCREEN) == BASE_MINIMUM_SIZE
    # e.g. the font was enlarged since the size was saved.
    big = BASE_FONT_SIZE * 2
    assert initial_window_size((1000, 700), big, NEEDED, BIG_SCREEN) == minimum_window_size(
        big, NEEDED, BIG_SCREEN
    )


def test_a_saved_size_larger_than_the_screen_is_reduced():
    w, h = initial_window_size((9000, 9000), BASE_FONT_SIZE, NEEDED, SMALL_SCREEN)
    assert w <= SMALL_SCREEN[0] * 0.95 and h <= SMALL_SCREEN[1] * 0.90


@pytest.mark.parametrize("bad", [None, (), (0, 0), (-5, 700), ("a", "b"), (1000,), "junk", 7])
def test_an_unusable_saved_size_falls_back_to_the_default(bad):
    assert initial_window_size(bad, BASE_FONT_SIZE, NEEDED, BIG_SCREEN) == BASE_DEFAULT_SIZE


def test_scaled_row_height():
    assert scaled_row_height(BASE_FONT_SIZE) == 22
    assert scaled_row_height(BASE_FONT_SIZE * 2) == 44
    assert scaled_row_height(9) == 22


def test_scaled_width():
    assert scaled_width(100, BASE_FONT_SIZE) == 100
    assert scaled_width(100, BASE_FONT_SIZE * 2) == 200
    assert scaled_width(100, 9) == 100


# -- Preferences ----------------------------------------------------------------


@pytest.fixture
def prefs_file(tmp_path, monkeypatch):
    path = tmp_path / ".dm41_test_prefs.json"
    path.write_text(json.dumps({"log_directory": str(tmp_path / "logs")}))
    monkeypatch.setattr(ProjectConfig, "PREFS_FILE", path)
    return path


def _write_prefs(path, **values):
    data = json.loads(path.read_text())
    data.update(values)
    path.write_text(json.dumps(data))


def test_window_size_defaults_to_none(prefs_file):
    config = ProjectConfig()
    config.load()
    assert config.window_size is None


def test_window_size_round_trips_through_the_file(prefs_file):
    config = ProjectConfig()
    config.load()
    config.window_size = (1234, 777)
    config.save()

    again = ProjectConfig()
    again.load()
    assert again.window_size == (1234, 777)


@pytest.mark.parametrize(
    "width,height", [(0, 0), (-1, 500), (500, 0), ("wide", "tall"), (None, None)]
)
def test_unusable_saved_window_sizes_read_as_none(prefs_file, width, height):
    _write_prefs(prefs_file, window_width=width, window_height=height)
    config = ProjectConfig()
    config.load()
    assert config.window_size is None


# -- The running app --------------------------------------------------------------


def _make_app():
    from gui.app import DM41ExplorerApp

    return DM41ExplorerApp()


@pytest.fixture
def make_app(prefs_file):
    """Builds apps (with whatever the prefs file says at that moment) and
    destroys any still alive at the end."""
    apps = []

    def build(**prefs):
        _write_prefs(prefs_file, **prefs)
        app = _make_app()
        apps.append(app)
        return app

    yield build
    for app in apps:
        try:
            app.destroy()
        except Exception:  # already closed by the test
            pass


def _size(app):
    app.update_idletasks()
    return parse_geometry(app.geometry())


def test_status_bar_follows_the_application_font(make_app):
    app = make_app(font_size=20)
    for label in (
        app._status_label,
        app._modified_label,
        app._battery_label,
        app._calc_time_label,
        app._source_label,
    ):
        assert label.cget("font").cget("size") == 20


def test_status_bar_font_is_unchanged_at_the_default_font(make_app):
    app = make_app()
    assert app._status_label.cget("font").cget("size") == ctk.ThemeManager.theme["CTkFont"]["size"]


def test_treeview_rows_grow_with_the_font(make_app):
    make_app(font_size=BASE_FONT_SIZE * 2)
    assert int(ttk.Style().lookup("Treeview", "rowheight")) == 44


def test_treeview_columns_grow_with_the_font(make_app):
    small = make_app(font_size=BASE_FONT_SIZE)
    small_width = int(small.xm_files_tab._tree.column("name", "width"))
    large = make_app(font_size=BASE_FONT_SIZE * 2)
    large_width = int(large.xm_files_tab._tree.column("name", "width"))
    assert large_width == small_width * 2


def test_a_window_with_no_saved_size_opens_at_the_scaled_default(make_app):
    app = make_app(font_size=BASE_FONT_SIZE)
    app.winfo_screenwidth = lambda: 3840
    app.winfo_screenheight = lambda: 2160
    app._apply_window_size()
    assert _size(app) == BASE_DEFAULT_SIZE


def test_a_larger_font_gives_a_larger_window_and_minimum(make_app):
    small = make_app(font_size=BASE_FONT_SIZE)
    small.winfo_screenwidth = lambda: 3840
    small.winfo_screenheight = lambda: 2160
    small._apply_window_size()
    small_size, small_min = _size(small), (small._min_width, small._min_height)

    large = make_app(font_size=BASE_FONT_SIZE * 2)
    large.winfo_screenwidth = lambda: 3840
    large.winfo_screenheight = lambda: 2160
    large._apply_window_size()
    large_size, large_min = _size(large), (large._min_width, large._min_height)

    assert large_size[0] > small_size[0] and large_size[1] > small_size[1]
    assert large_min[0] > small_min[0] and large_min[1] > small_min[1]


def test_the_window_has_a_minimum_size(make_app):
    app = make_app()
    assert app._min_width >= BASE_MINIMUM_SIZE[0]
    assert app._min_height >= BASE_MINIMUM_SIZE[1] or app.winfo_screenheight() * 0.9 < BASE_MINIMUM_SIZE[1]
    app.geometry("200x150")
    assert _size(app)[0] >= app._min_width


def test_the_minimum_covers_the_tab_bar(make_app):
    app = make_app(font_size=BASE_FONT_SIZE * 2)
    tab_bar_width = app.tabview.winfo_reqwidth()
    screen_cap = int(app.winfo_screenwidth() * 0.95)
    assert app._min_width >= min(tab_bar_width, screen_cap)


def test_a_saved_size_is_used_at_launch(make_app):
    app = make_app(window_width=950, window_height=650)
    if app.winfo_screenwidth() * 0.95 < 950 or app.winfo_screenheight() * 0.9 < 650:
        pytest.skip("screen too small for this size")
    assert _size(app) == (950, 650)


def test_closing_saves_the_size_for_the_next_launch(make_app, prefs_file):
    app = make_app()
    app.geometry("930x640")
    app.update_idletasks()
    app.on_close()

    saved = json.loads(prefs_file.read_text())
    assert (saved["window_width"], saved["window_height"]) == (930, 640)

    config = ProjectConfig()
    config.load()
    assert config.window_size == (930, 640)


def test_closing_a_maximized_window_keeps_the_previous_size(make_app, prefs_file, monkeypatch):
    _write_prefs(prefs_file, window_width=900, window_height=600)
    app = make_app()
    monkeypatch.setattr(app, "_window_is_maximized", lambda: True)
    app.geometry("1200x800")
    app.update_idletasks()
    app.on_close()

    saved = json.loads(prefs_file.read_text())
    assert (saved["window_width"], saved["window_height"]) == (900, 600)


def test_a_failure_saving_the_size_does_not_stop_the_app_closing(make_app, monkeypatch):
    app = make_app()

    def broken_save(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(app.config_store, "save", broken_save)
    app.on_close()  # must not raise
    with pytest.raises(Exception):
        app.winfo_exists()  # a destroyed Tk app raises here


# -- Saving the size however the app ends --------------------------------------------


import time
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


def _pump(app, seconds=0.5, until=None):
    """Runs Tk's event loop for up to `seconds` (or until `until()`)."""
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until() if until is not None else True


def _start_tracking(app):
    """What the app itself does two seconds after launch, once the window
    manager has placed the window."""
    _pump(app, 0.3)
    app._start_tracking_size()


def _saved(prefs_file):
    data = json.loads(prefs_file.read_text())
    return data.get("window_width", 0), data.get("window_height", 0)


def test_the_size_is_saved_shortly_after_a_resize_without_any_quit(
    make_app, prefs_file, monkeypatch
):
    monkeypatch.setattr("gui.app.DM41ExplorerApp.RESIZE_SAVE_DELAY_MS", 50)
    app = make_app()
    _start_tracking(app)
    app.geometry("940x650")
    assert _pump(app, 3, until=lambda: _saved(prefs_file) == (940, 650)), _saved(prefs_file)


def test_a_burst_of_resizes_is_saved_once_at_the_final_size(make_app, prefs_file, monkeypatch):
    monkeypatch.setattr("gui.app.DM41ExplorerApp.RESIZE_SAVE_DELAY_MS", 150)
    app = make_app()
    saves = []
    original = app.config_store.save
    monkeypatch.setattr(app.config_store, "save", lambda *a, **k: (saves.append(1), original(*a, **k)))
    _start_tracking(app)
    for width in (900, 910, 920, 930):
        app.geometry(f"{width}x640")
        _pump(app, 0.03)
    assert _pump(app, 3, until=lambda: _saved(prefs_file) == (930, 640))
    _pump(app, 0.4)
    assert len(saves) == 1


def test_nothing_is_written_when_the_user_never_resized(make_app, prefs_file, monkeypatch):
    monkeypatch.setattr("gui.app.DM41ExplorerApp.RESIZE_SAVE_DELAY_MS", 50)
    app = make_app()
    _start_tracking(app)
    _pump(app, 0.5)
    app.on_close()
    assert _saved(prefs_file) == (0, 0)


def test_closing_twice_is_harmless(make_app):
    app = make_app()
    app.on_close()
    app.on_close()  # the second call must neither raise nor touch Tk


def test_the_macos_quit_command_goes_through_on_close(make_app, prefs_file, monkeypatch):
    """Cmd+Q on macOS calls the Tcl command ::tk::mac::Quit, whose default
    does not run the WM_DELETE_WINDOW handler. Simulated here by building
    the app as if on a Mac and invoking that Tcl command."""
    monkeypatch.setattr("gui.app.PLATFORM_SYSTEM", "Darwin")
    app = make_app()
    app.geometry("925x645")
    app.update_idletasks()
    app.tk.call("::tk::mac::Quit")

    assert _saved(prefs_file) == (925, 645)
    with pytest.raises(Exception):
        app.winfo_exists()


def test_the_macos_quit_command_still_asks_about_unsaved_changes(make_app, monkeypatch):
    monkeypatch.setattr("gui.app.PLATFORM_SYSTEM", "Darwin")
    app = make_app()
    app.memory.is_modified()
    asked = []
    monkeypatch.setattr(
        "gui.app.messagebox.askyesno", lambda *a, **k: (asked.append(a), False)[1]
    )
    app.tk.call("::tk::mac::Quit")
    assert asked, "Cmd+Q must show the unsaved-changes prompt"
    assert app.winfo_exists(), "answering No must keep the app open"


# -- Columns fit their text -----------------------------------------------------------

from tkinter import font as tkfont

from gui.tab_common import CELL_PADDING, treeview_fonts

FIT_CASES = [
    ("Alarms", "goodalarms.dm41"),
    ("Programs", "global-key-assignments.dm41"),
    # Seven labels: its Key ASNs text ("N/A, N/A, ...") is far wider than 220 px.
    ("Programs", "samplelabels.dm41"),
    ("XM Files", "dm41x_manyfiles.dm41"),
]


@pytest.mark.parametrize("font_size", [BASE_FONT_SIZE, 26])
@pytest.mark.parametrize("tab_name,state", FIT_CASES)
def test_no_cell_text_is_wider_than_its_column(make_app, tab_name, state, font_size):
    app = make_app(font_size=font_size)
    app.winfo_screenwidth = lambda: 3840
    app.winfo_screenheight = lambda: 2160
    app._apply_window_size()
    app.tabview.set(tab_name)
    app._load_state_into_buffer(str(DATA_DIR / state))
    app._on_tab_changed()
    app.update()

    tab = app._tabs[tab_name]
    tree = tab._tree
    stretching = _stretch_ids(tab_name)
    cell_font = tkfont.Font(font=treeview_fonts()[0])
    rows = tree.get_children()
    assert rows, "the sample state must put rows in the tree"
    for column in tree["columns"]:
        if column in stretching:
            continue
        width = int(tree.column(column, "width"))
        for iid in rows:
            text = str(tree.set(iid, column))
            assert cell_font.measure(text) + CELL_PADDING <= width, (
                tab_name, column, text, width
            )


def _stretch_ids(tab_name):
    from gui import alarms_tab, program_tab, xm_files_tab

    columns = {
        "Alarms": alarms_tab._TREE_COLUMNS,
        "Programs": program_tab._TREE_COLUMNS,
        "XM Files": xm_files_tab._TREE_COLUMNS,
    }[tab_name]
    return {col[0] for col in columns if col[3]}


def test_a_heading_is_never_truncated(make_app):
    app = make_app(font_size=26)
    app.tabview.set("Alarms")
    app._load_state_into_buffer(str(DATA_DIR / "goodalarms.dm41"))
    app._on_tab_changed()
    app.update()
    tree = app._tabs["Alarms"]._tree
    heading_font = tkfont.Font(font=treeview_fonts()[1])
    for column in tree["columns"]:
        if column in _stretch_ids("Alarms"):
            continue
        assert int(tree.column(column, "width")) >= heading_font.measure(tree.heading(column, "text"))
