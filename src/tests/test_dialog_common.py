"""
Tests for gui/dialog_common.py's build_dialog_button_row() -- GitHub issue
#13 (consistent Cancel-left/primary-right/extras-centered button placement,
plus Return triggering the "default" primary action).

Requires a real Tk display (Xvfb in CI/sandboxes) -- same requirement as
test_app.py.
"""

import time
from unittest import mock

import pytest

pytest.importorskip("tkinter")
pytest.importorskip("customtkinter")

import customtkinter as ctk

from gui import dialog_common
from gui.dialog_common import (
    CONFIRM_DIALOG_WIDTH,
    ConfirmDialog,
    _is_multiline_text_widget,
    ask_yes_no,
    build_dialog_button_row,
    center_on_master,
    fit_dialog,
    scaled,
)
from gui.window_geometry import centered_position


def _buttons_by_text(widget) -> dict:
    """Walks `widget`'s children (recursively, since extra_buttons live
    inside their own centering CTkFrame) and returns {button_text:
    CTkButton}. Restricted to actual CTkButton instances -- other widgets
    in the tree (CTkFrame, and CTkButton's own internal CTkCanvas) either
    don't support cget("text") at all or raise for it."""
    found = {}
    for child in widget.winfo_children():
        if isinstance(child, ctk.CTkButton):
            found[child.cget("text")] = child
        else:
            found.update(_buttons_by_text(child))
    return found


def _show(window, timeout: float = 5.0) -> bool:
    """Maps `window` and pumps the event loop until it is really on screen.

    Needed because of a Windows-only detour inside customtkinter: every new
    CTkToplevel runs _windows_set_titlebar_color(), which calls withdraw()
    to hide the window while it recolors the titlebar and only re-shows it
    later from an after(5, _revert_withdraw_after_windows_set_titlebar_color)
    callback. A test that builds a dialog and immediately calls
    update_idletasks() never lets that timer fire, so it is measuring an
    *unmapped* window: every child's winfo_x() is 0 (so placement asserts
    read `0 < 0`) and focus_force() has nothing to focus, which makes
    <Return> land on the toplevel instead of the CTkTextbox. Linux and
    macOS never take that code path -- hence Windows-only CI failures.
    """
    window.deiconify()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        window.update()
        if window.winfo_viewable():
            return True
        time.sleep(0.01)
    return False


def _focus(widget, timeout: float = 5.0) -> bool:
    """Gives `widget` keyboard focus and pumps the event loop until Tk
    really reports it there.

    A single focus_force() + update() is not enough on Windows: the focus
    change arrives later as a WM_SETFOCUS message, and customtkinter's own
    deferred titlebar callbacks (see _show()) can re-focus the toplevel in
    the meantime. event_generate("<Return>") delivers key events to
    whatever has focus *now*, so until this settles the key lands on the
    toplevel instead of `widget`.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        widget.focus_force()
        widget.update()
        if widget.focus_get() is widget:
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def dialog(root):
    d = ctk.CTkToplevel(root)
    if not _show(d):
        d.destroy()
        pytest.skip(
            "dialog never became viewable -- this display cannot map windows, "
            "so button geometry and keyboard focus are not measurable here"
        )
    yield d
    if d.winfo_exists():
        d.destroy()


# ---- placement: Cancel left, primary right, extras centered ----


def test_cancel_is_left_of_primary(dialog):
    row, primary = build_dialog_button_row(
        dialog,
        primary_text="Save",
        on_primary=lambda: None,
    )
    dialog.update_idletasks()
    cancel = _buttons_by_text(row)["Cancel"]
    assert primary.cget("text") == "Save"
    assert cancel.winfo_x() < primary.winfo_x()


def test_extra_buttons_sit_between_cancel_and_primary(dialog):
    row, primary = build_dialog_button_row(
        dialog,
        primary_text="Connect",
        on_primary=lambda: None,
        extra_buttons=[("Rescan", lambda: None)],
    )
    dialog.update_idletasks()
    buttons = _buttons_by_text(row)
    cancel, rescan = buttons["Cancel"], buttons["Rescan"]
    assert cancel.winfo_x() < rescan.winfo_x() < primary.winfo_x()


def test_custom_cancel_text_and_multiple_extra_buttons(dialog):
    row, primary = build_dialog_button_row(
        dialog,
        primary_text="OK",
        on_primary=lambda: None,
        cancel_text="Close",
        extra_buttons=[("Reset", lambda: None), ("Apply", lambda: None)],
    )
    dialog.update_idletasks()
    buttons = _buttons_by_text(row)
    assert set(buttons) == {"Close", "Reset", "Apply", "OK"}
    ordered = sorted(buttons.items(), key=lambda kv: kv[1].winfo_x())
    assert [text for text, _ in ordered] == ["Close", "Reset", "Apply", "OK"]


# ---- commands: primary/cancel wiring ----


def test_primary_button_invokes_on_primary(dialog):
    on_primary = mock.Mock()
    row, primary = build_dialog_button_row(
        dialog, primary_text="Save", on_primary=on_primary
    )
    primary.invoke()
    on_primary.assert_called_once()


def test_cancel_defaults_to_dialog_destroy(dialog):
    row, primary = build_dialog_button_row(
        dialog, primary_text="Save", on_primary=lambda: None
    )
    cancel = row.winfo_children()[0]
    assert dialog.winfo_exists()
    cancel.invoke()
    assert not dialog.winfo_exists()


def test_cancel_uses_explicit_on_cancel_instead_of_destroy(dialog):
    on_cancel = mock.Mock()
    row, primary = build_dialog_button_row(
        dialog,
        primary_text="Connect",
        on_primary=lambda: None,
        on_cancel=on_cancel,
    )
    cancel = row.winfo_children()[0]
    cancel.invoke()
    on_cancel.assert_called_once()
    # Explicit on_cancel replaces the default destroy -- the dialog is
    # still open unless on_cancel itself closes it (matches
    # PortSelectionDialog's _on_cancel, which does its own grab_release()
    # + destroy()).
    assert dialog.winfo_exists()


# ---- Return / "default action" ----


def test_is_multiline_text_widget_true_for_ctktextbox_internal_text(root):
    box = ctk.CTkTextbox(root)
    assert _is_multiline_text_widget(box._textbox) is True


def test_is_multiline_text_widget_false_for_entry(root):
    entry = ctk.CTkEntry(root)
    assert _is_multiline_text_widget(entry) is False


def test_return_on_dialog_triggers_primary_action(dialog):
    on_primary = mock.Mock()
    build_dialog_button_row(dialog, primary_text="Save", on_primary=on_primary)
    dialog.focus_force()
    dialog.update()
    dialog.event_generate("<Return>")
    dialog.update()
    on_primary.assert_called_once()


def test_return_inside_textbox_does_not_trigger_primary_action(dialog):
    """The scenario the guard exists for: a multi-line CTkTextbox (like
    XMFileDialog's Data/ASCII editors) needs Return to insert a newline,
    not submit the dialog."""
    on_primary = mock.Mock()
    box = ctk.CTkTextbox(dialog)
    box.pack()
    build_dialog_button_row(dialog, primary_text="Save", on_primary=on_primary)

    if not _focus(box._textbox):
        pytest.skip("the textbox never received keyboard focus on this display")
    box._textbox.event_generate("<Return>")
    dialog.update()

    on_primary.assert_not_called()


# -- ConfirmDialog / ask_yes_no (GitHub issue #55) ----------------------------

_FINDINGS = (
    "These will not work on a DM41L:\n\n"
    'Program "TWR":\n'
    "- step 416: TRNG is not built into a DM41L\n"
    "Key 14 (unshifted):\n"
    "- XROM 25,00 is not built into a DM41L\n\n"
    "Send it anyway?"
)


@pytest.fixture
def confirm(root):
    dialog = ConfirmDialog(root, "Send State to Calculator", _FINDINGS)
    yield dialog
    if dialog.winfo_exists():
        dialog.destroy()


def test_confirm_dialog_shows_the_whole_message_and_title(confirm):
    assert confirm.title() == "Send State to Calculator"
    assert confirm._message.cget("text") == _FINDINGS


def test_confirm_dialog_wraps_only_at_its_wide_width(confirm):
    """The point of the dialog: wider than a native alert, so a finding's
    line does not wrap."""
    assert confirm._message.cget("wraplength") == scaled(CONFIRM_DIALOG_WIDTH)
    confirm.update_idletasks()
    width = confirm._reverse_window_scaling(confirm.winfo_reqwidth())
    assert width >= CONFIRM_DIALOG_WIDTH


def test_confirm_dialog_yes_answers_true(confirm):
    _buttons_by_text(confirm)["Yes"].invoke()
    assert confirm.result is True
    assert not confirm.winfo_exists()


def test_confirm_dialog_no_answers_false(confirm):
    _buttons_by_text(confirm)["No"].invoke()
    assert confirm.result is False
    assert not confirm.winfo_exists()


def test_confirm_dialog_escape_answers_false(confirm):
    # Same as the Return test above: a key event needs a mapped, focused
    # window to land on.
    if not _show(confirm):
        pytest.skip("dialog never became viewable, so it cannot take a key")
    confirm.focus_force()
    confirm.update()
    confirm.event_generate("<Escape>")
    confirm.update()
    assert confirm.result is False
    assert not confirm.winfo_exists()


def test_ask_yes_no_returns_the_dialog_answer(root):
    def answer_yes(dialog):
        _buttons_by_text(dialog)["Yes"].invoke()

    with mock.patch.object(root, "wait_window", side_effect=answer_yes):
        assert ask_yes_no(root, "Question", "Really?") is True

    with mock.patch.object(root, "wait_window", side_effect=lambda dialog: None):
        assert ask_yes_no(root, "Question", "Really?") is False


# -- center_on_master (GitHub issue #64) -----------------------------------------


@pytest.fixture
def shown_parent():
    parent = ctk.CTk()
    parent.geometry("900x700+120+90")
    if not _show(parent):
        parent.destroy()
        pytest.skip("the window never became viewable on this display")
    yield parent
    parent.destroy()


def _requested_position(dialog, monkeypatch):
    """Records the "+x+y" center_on_master() asks for, rather than reading
    back where the window manager put the dialog, which it may adjust."""
    asked = []
    monkeypatch.setattr(dialog, "geometry", lambda text=None: asked.append(text))
    return asked


def test_a_dialog_is_centred_on_its_window(shown_parent, monkeypatch):
    monkeypatch.setattr(
        dialog_common,
        "screen_rectangles",
        lambda _window: [(-5000, -5000, 20000, 20000)],
    )
    dialog = ctk.CTkToplevel(shown_parent)
    dialog.wm_geometry("300x200")
    asked = _requested_position(dialog, monkeypatch)

    center_on_master(dialog)

    parent_box = (
        shown_parent.winfo_rootx(),
        shown_parent.winfo_rooty(),
        shown_parent.winfo_width(),
        shown_parent.winfo_height(),
    )
    # The size as center_on_master() reads it: Tk's own, which the window
    # manager may have adjusted from 300x200.
    reported = dialog_common._RAW_SIZE.match(dialog.wm_geometry())
    width = max(int(reported.group(1)), dialog.winfo_reqwidth())
    height = max(int(reported.group(2)), dialog.winfo_reqheight())
    x, y = centered_position(
        (width, height), parent_box, [(-5000, -5000, 20000, 20000)]
    )
    assert asked == [f"+{x}+{y}"]
    dialog.destroy()


def test_nothing_moves_while_the_parent_is_hidden(root, monkeypatch):
    dialog = ctk.CTkToplevel(root)  # `root` is withdrawn
    asked = _requested_position(dialog, monkeypatch)
    center_on_master(dialog)
    assert asked == []
    dialog.destroy()


def test_fit_dialog_centres_the_dialog(root, monkeypatch):
    centred = []
    monkeypatch.setattr(dialog_common, "center_on_master", centred.append)
    dialog = ctk.CTkToplevel(root)
    fit_dialog(dialog, 300, 200)
    assert centred == [dialog]
    dialog.destroy()


def test_a_failure_while_centring_is_harmless(shown_parent, monkeypatch):
    def broken(_window):
        raise RuntimeError("no screens")

    monkeypatch.setattr(dialog_common, "screen_rectangles", broken)
    dialog = ctk.CTkToplevel(shown_parent)
    center_on_master(dialog)  # must not raise
    dialog.destroy()
