"""Tests for how the main window saves its size for the next launch
(GitHub issue #42): on close, shortly after the user stops resizing, and
never for a maximized window or a size the user didn't change.

These build a real DM41ExplorerApp but never depend on what the window
system reports: the window's size is supplied by the test (`_report_size`)
and the resize debounce is driven directly rather than with real timing,
so they behave the same on every platform. The size rules themselves are
in test_window_geometry.py.
"""
import json
import types

import pytest

pytest.importorskip("customtkinter")

from config import ProjectConfig


@pytest.fixture
def prefs_file(tmp_path, monkeypatch):
    path = tmp_path / ".dm41_test_prefs.json"
    path.write_text(json.dumps({"log_directory": str(tmp_path / "logs")}))
    monkeypatch.setattr(ProjectConfig, "PREFS_FILE", path)
    return path


@pytest.fixture
def app(prefs_file):
    from gui.app import DM41ExplorerApp

    app = DM41ExplorerApp()
    yield app
    try:
        app.destroy()
    except Exception:  # already closed by the test
        pass


def _report_size(app, monkeypatch, width, height):
    """Makes the window report `width` x `height`, as if the user had
    resized it to that."""
    monkeypatch.setattr(app, "geometry", lambda *args: f"{width}x{height}+10+10")


def _saved(prefs_file):
    data = json.loads(prefs_file.read_text())
    return data.get("window_width", 0), data.get("window_height", 0)


def test_closing_saves_the_size_for_the_next_launch(app, prefs_file, monkeypatch):
    _report_size(app, monkeypatch, 930, 640)
    app.on_close()

    assert _saved(prefs_file) == (930, 640)
    config = ProjectConfig()
    config.load()
    assert config.window_size == (930, 640)


def test_closing_a_maximized_window_keeps_the_previous_size(app, prefs_file, monkeypatch):
    _report_size(app, monkeypatch, 1200, 800)
    monkeypatch.setattr(app, "_window_is_maximized", lambda: True)
    app.on_close()
    assert _saved(prefs_file) == (0, 0)


def test_nothing_is_written_when_the_user_never_resized(app, prefs_file, monkeypatch):
    _report_size(app, monkeypatch, *app._applied_size)
    app.on_close()
    assert _saved(prefs_file) == (0, 0)


def test_a_failure_saving_the_size_does_not_stop_the_app_closing(app, monkeypatch):
    _report_size(app, monkeypatch, 930, 640)

    def broken_save(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(app.config_store, "save", broken_save)
    app.on_close()  # must not raise
    with pytest.raises(Exception):
        app.winfo_exists()  # a destroyed Tk app raises here


def test_a_burst_of_resizes_is_saved_once_after_it_stops(app, prefs_file, monkeypatch):
    """Each resize event restarts the save timer, so only the last one's
    timer fires."""
    pending = {}
    ids = iter(range(100))

    def after(_ms, fn):
        after_id = next(ids)
        pending[after_id] = fn
        return after_id

    monkeypatch.setattr(app, "after", after)
    monkeypatch.setattr(app, "after_cancel", lambda after_id: pending.pop(after_id))
    app._tracking_size = True
    resize = types.SimpleNamespace(widget=app)
    for _ in range(4):
        app._on_configure(resize)
    assert len(pending) == 1, "every earlier timer was cancelled"

    _report_size(app, monkeypatch, 930, 640)
    (save,) = pending.values()
    save()
    assert _saved(prefs_file) == (930, 640)


def test_resizes_are_ignored_until_tracking_starts(app, monkeypatch):
    """The window manager's own placement after launch isn't the user's."""
    scheduled = []
    monkeypatch.setattr(app, "after", lambda _ms, fn: scheduled.append(fn))
    app._tracking_size = False
    app._on_configure(types.SimpleNamespace(widget=app))
    assert scheduled == []


def test_closing_twice_is_harmless(app):
    app.on_close()
    app.on_close()  # the second call must neither raise nor touch Tk


@pytest.fixture
def mac_app(prefs_file, monkeypatch):
    """An app built as if on macOS, where Cmd+Q calls ::tk::mac::Quit."""
    monkeypatch.setattr("gui.app.PLATFORM_SYSTEM", "Darwin")
    from gui.app import DM41ExplorerApp

    app = DM41ExplorerApp()
    yield app
    try:
        app.destroy()
    except Exception:
        pass


def test_the_macos_quit_command_goes_through_on_close(mac_app, prefs_file, monkeypatch):
    """::tk::mac::Quit's default skips the WM_DELETE_WINDOW handler, so
    the app registers its own; it must save the size and close."""
    _report_size(mac_app, monkeypatch, 925, 645)
    mac_app.tk.call("::tk::mac::Quit")
    assert _saved(prefs_file) == (925, 645)
    with pytest.raises(Exception):
        mac_app.winfo_exists()


def test_the_macos_quit_command_still_asks_about_unsaved_changes(mac_app, monkeypatch):
    mac_app.memory.mark_modified()
    asked = []
    monkeypatch.setattr(
        "gui.app.messagebox.askyesno", lambda *a, **k: (asked.append(a), False)[1]
    )
    mac_app.tk.call("::tk::mac::Quit")
    assert asked, "Cmd+Q must show the unsaved-changes prompt"
    assert mac_app.winfo_exists(), "answering No must keep the app open"
