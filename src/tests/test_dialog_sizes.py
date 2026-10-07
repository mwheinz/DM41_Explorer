"""Tests for GitHub issue #42, dialog sizes: every dialog follows the
application font (Preferences > Font) -- its fixed widths and wrap lengths
grow with it, and the dialogs that set their own window size never clip
their content or exceed the screen.

The first half tests gui/window_geometry.dialog_size(); the second half
builds the real dialogs against a live (withdrawn) Tk root (Xvfb in
CI/sandboxes, like test_app.py).
"""
from unittest import mock

import pytest

pytest.importorskip("customtkinter")

import customtkinter as ctk

from gui.window_geometry import BASE_FONT_SIZE, dialog_size, parse_geometry

BIG_SCREEN = (3840, 2160)
SMALL_SCREEN = (1280, 800)
LARGE_FONT = 26


# -- The pure rule --------------------------------------------------------------


def test_dialog_size_at_the_default_font_is_the_base_size():
    assert dialog_size(BASE_FONT_SIZE, (460, 300), (100, 100), BIG_SCREEN) == (460, 300)


def test_dialog_size_grows_with_the_font():
    assert dialog_size(BASE_FONT_SIZE * 2, (460, 300), (0, 0), BIG_SCREEN) == (920, 600)


def test_dialog_size_is_never_smaller_than_the_content():
    assert dialog_size(BASE_FONT_SIZE, (460, 300), (700, 500), BIG_SCREEN) == (700, 500)


def test_dialog_size_with_no_base_is_the_content_size():
    assert dialog_size(BASE_FONT_SIZE, (0, 0), (333, 222), BIG_SCREEN) == (333, 222)


def test_dialog_size_never_exceeds_the_screen():
    width, height = dialog_size(48, (760, 560), (5000, 5000), SMALL_SCREEN)
    assert width <= SMALL_SCREEN[0] * 0.95
    assert height <= SMALL_SCREEN[1] * 0.90


def test_a_smaller_font_does_not_shrink_a_dialog():
    assert dialog_size(8, (460, 300), (0, 0), BIG_SCREEN) == (460, 300)


# -- Real dialogs ---------------------------------------------------------------


@pytest.fixture
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def font_size():
    """Sets the application font the way gui/app.py does at start-up (the
    theme's CTkFont size), for dialogs built during the test."""
    saved = ctk.ThemeManager.theme["CTkFont"]["size"]

    def use(size):
        ctk.ThemeManager.theme["CTkFont"]["size"] = size

    yield use
    ctk.ThemeManager.theme["CTkFont"]["size"] = saved


def _quiet(build):
    """Wraps a dialog builder so the dialog is withdrawn at once and never
    appears on the desktop while the tests run."""

    def quiet(root):
        dialog = build(root)
        dialog.withdraw()
        return dialog

    quiet.__name__ = build.__name__
    return quiet


@_quiet
def _alarm(root):
    from gui.alarm_edit_dialog import AlarmEditDialog

    return AlarmEditDialog(root, mock.Mock())


@_quiet
def _xm_file(root):
    from gui.xm_file_dialog import XMFileDialog

    return XMFileDialog(root, mock.Mock())


@_quiet
def _key_assignment(root):
    from gui.key_assignment_edit_dialog import KeyAssignmentEditDialog

    return KeyAssignmentEditDialog(
        root,
        key_number=1,
        shifted=False,
        assignment=None,
        program_assignment=None,
        program_names=["A", "B"],
        on_save=mock.Mock(),
        on_delete=mock.Mock(),
        flag_clear=False,
    )


@_quiet
def _register(root):
    from gui.register_edit_dialog import RegisterEditDialog
    from memory import Register

    return RegisterEditDialog(root, 5, Register(bytes(7)), mock.Mock())


@_quiet
def _register_range(root):
    from gui.register_range_dialog import RegisterRangeDialog

    return RegisterRangeDialog(root, count=35, on_confirm=mock.Mock())


@_quiet
def _register_import(root):
    from gui.register_range_dialog import RegisterImportLocationDialog

    return RegisterImportLocationDialog(
        root, count=35, import_count=10, on_confirm=mock.Mock()
    )


@_quiet
def _shortcuts(root):
    from gui.help_dialog import KeyboardShortcutsDialog

    return KeyboardShortcutsDialog(root)


@_quiet
def _port(root):
    from gui.port_dialog import PortSelectionDialog

    serial = mock.Mock()
    serial.get_available_ports.return_value = ["/dev/tty.usbmodem1234"]
    return PortSelectionDialog(root, serial)


@_quiet
def _mnemonics(root):
    from gui.mnemonics_reference_dialog import MnemonicsReferenceDialog

    return MnemonicsReferenceDialog(root)


DIALOGS = [
    _alarm,
    _xm_file,
    _key_assignment,
    _register,
    _register_range,
    _register_import,
    _shortcuts,
    _port,
    _mnemonics,
]
# Dialogs that set their own window size.
SIZED_DIALOGS = [_shortcuts, _port, _mnemonics]


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def _size(dialog):
    """The dialog's size: what was set on it, or else what its content asks
    for (a dialog nobody sized isn't mapped under the withdrawn test root,
    so its geometry is still 1x1)."""
    dialog.update()
    size = parse_geometry(dialog.geometry())
    if size == (1, 1):
        return dialog.winfo_reqwidth(), dialog.winfo_reqheight()
    return size


def _widths(dialog, kind):
    return [w.cget("width") for w in _walk(dialog) if isinstance(w, kind)]


@pytest.mark.parametrize("build", DIALOGS)
def test_a_larger_font_widens_the_dialog(root, font_size, build):
    font_size(BASE_FONT_SIZE)
    small = build(root)
    small_width = _size(small)[0]
    small.destroy()
    font_size(LARGE_FONT)
    large = build(root)
    assert _size(large)[0] > small_width
    large.destroy()


@pytest.mark.parametrize("build", [_alarm, _xm_file])
def test_entries_grow_with_the_font(root, font_size, build):
    font_size(BASE_FONT_SIZE)
    small = build(root)
    small_widths = sorted(_widths(small, ctk.CTkEntry))
    small.destroy()
    font_size(BASE_FONT_SIZE * 2)
    large = build(root)
    large_widths = sorted(_widths(large, ctk.CTkEntry))
    large.destroy()
    assert small_widths and len(small_widths) == len(large_widths)
    assert large_widths == [w * 2 for w in small_widths]


def test_the_xm_file_text_boxes_grow_with_the_font(root, font_size):
    font_size(BASE_FONT_SIZE)
    small = _xm_file(root)
    small_box = small._data_box.cget("width"), small._data_box.cget("height")
    small.destroy()
    font_size(BASE_FONT_SIZE * 2)
    large = _xm_file(root)
    large_box = large._data_box.cget("width"), large._data_box.cget("height")
    large.destroy()
    assert large_box == (small_box[0] * 2, small_box[1] * 2)


def test_the_button_row_grows_with_the_font(root, font_size):
    font_size(BASE_FONT_SIZE * 2)
    dialog = _alarm(root)
    widths = _widths(dialog, ctk.CTkButton)
    assert widths and all(w == 180 for w in widths)
    dialog.destroy()


@pytest.mark.parametrize("build", SIZED_DIALOGS)
@pytest.mark.parametrize("size", [BASE_FONT_SIZE, LARGE_FONT])
def test_a_fixed_size_dialog_never_clips_its_content(root, font_size, build, size):
    font_size(size)
    dialog = build(root)
    dialog.update()
    width, height = _size(dialog)
    screen = (dialog.winfo_screenwidth(), dialog.winfo_screenheight())
    if build is _mnemonics:
        # The tables scroll, so only the screen limits this one.
        assert width <= screen[0] and height <= screen[1]
    else:
        assert width >= dialog.winfo_reqwidth()
        assert height >= dialog.winfo_reqheight()
    dialog.destroy()


@pytest.mark.parametrize("build", SIZED_DIALOGS)
def test_a_fixed_size_dialog_cannot_be_resized_below_its_minimum(root, font_size, build):
    font_size(LARGE_FONT)
    dialog = build(root)
    dialog.update_idletasks()
    min_width, min_height = dialog._min_width, dialog._min_height
    width, height = _size(dialog)
    assert min_width <= width and min_height <= height
    assert min_width > 0 and min_height > 0
    dialog.destroy()


def test_the_connect_dialog_wraps_its_message_to_the_scaled_width(root, font_size):
    from gui.port_dialog import DIALOG_WIDTH, PortSelectionDialog

    widths = []
    for size in (BASE_FONT_SIZE, BASE_FONT_SIZE * 2):
        font_size(size)
        serial = mock.Mock()
        serial.get_available_ports.return_value = []
        dialog = PortSelectionDialog(root, serial, message="No calculator found.")
        labels = [w for w in _walk(dialog) if isinstance(w, ctk.CTkLabel)]
        widths.append(max(label.cget("wraplength") for label in labels))
        dialog.destroy()
    assert widths[0] == DIALOG_WIDTH - 40
    assert widths[1] == 2 * widths[0]


def test_the_mnemonics_notes_rewrap_when_the_window_is_resized(root, font_size):
    font_size(BASE_FONT_SIZE)
    dialog = _mnemonics(root)
    dialog.update()
    note = dialog._notes[0]
    before = note.cget("wraplength")
    dialog.geometry(f"{_size(dialog)[0] + 200}x{_size(dialog)[1]}")
    dialog.update()
    assert note.cget("wraplength") > before
    dialog.destroy()


def test_the_mnemonics_dialog_opens_larger_with_a_larger_font(root, font_size):
    font_size(BASE_FONT_SIZE)
    small = _mnemonics(root)
    small_size = _size(small)
    small.destroy()
    font_size(18)
    large = _mnemonics(root)
    large_size = _size(large)
    large.destroy()
    assert large_size[0] > small_size[0] and large_size[1] > small_size[1]


def test_the_mnemonics_dialog_can_be_made_smaller_than_its_tables(root, font_size):
    """Its tables scroll, so the content must not set the minimum size."""
    font_size(BASE_FONT_SIZE)
    dialog = _mnemonics(root)
    dialog.update_idletasks()
    assert (dialog._min_width, dialog._min_height) == (560, 360)
    assert dialog.winfo_reqwidth() > 560
    dialog.destroy()
