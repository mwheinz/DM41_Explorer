"""
Shared modal-dialog button-row layout, so every CTkToplevel dialog in the
app (RegisterEditDialog, XMFileDialog, PortSelectionDialog, ...) places its
buttons the same way instead of each hand-rolling a slightly different
pack() order that drifts apart over time -- GitHub issue #13.
"""

import tkinter as tk
import customtkinter as ctk

from gui.window_geometry import dialog_size, scaled_width

# Every dialog's button row used this width for every button before this
# was centralized -- kept as the shared default so build_dialog_button_row
# callers don't each have to repeat it.
BUTTON_WIDTH = 90


def _font_size():
    return ctk.ThemeManager.theme["CTkFont"]["size"]


def scaled(width) -> int:
    """A fixed dialog dimension (an entry's width, a label's wraplength) for
    the application font: `width` at the default font size, growing in
    proportion above it so a larger font doesn't squeeze the dialog
    (GitHub issue #42)."""
    return scaled_width(width, _font_size())


def fit_dialog(dialog, base_width=0, base_height=0, *, minimum=None, content=True):
    """Sizes and sets the minimum size of `dialog` once its widgets exist.

    `base_width`/`base_height` are the size wanted at the default font size
    (0 = whatever the content needs); they grow with the application font.
    With `content` true (dialogs that can't scroll) the result is never
    smaller than the widgets ask for; pass False for dialogs whose content
    scrolls. `minimum` is the smallest size, at the default font size, the
    user may resize to (default: the same as the size). Nothing exceeds the
    screen. Sizes passed to Tk are unscaled, like the main window's.
    """
    dialog.update_idletasks()
    undo = dialog._reverse_window_scaling
    screen = (undo(dialog.winfo_screenwidth()), undo(dialog.winfo_screenheight()))
    needed = (
        (undo(dialog.winfo_reqwidth()), undo(dialog.winfo_reqheight()))
        if content
        else (0, 0)
    )
    size = dialog_size(_font_size(), (base_width, base_height), needed, screen)
    smallest = dialog_size(_font_size(), minimum or (base_width, base_height), needed, screen)
    dialog.geometry(f"{size[0]}x{size[1]}")
    dialog.minsize(*smallest)


def wrap_labels_to_width(dialog, labels, margin):
    """Keeps each of `labels` wrapped to the dialog's width minus `margin`
    (unscaled pixels), so text re-flows when the dialog is resized or opens
    larger because of the font."""

    def _rewrap(event):
        if event.widget is not dialog:
            return
        width = max(100, int(dialog._reverse_window_scaling(event.width)) - margin)
        for label in labels:
            label.configure(wraplength=width)

    dialog.bind("<Configure>", _rewrap, add="+")


def _is_multiline_text_widget(widget) -> bool:
    """True if `widget` is a real tkinter.Text -- CTkTextbox's actual
    keyboard-receiving widget is a plain `tkinter.Text` instance held as
    its `._textbox` attribute (see customtkinter's CTkTextbox.__init__),
    not the CTkTextbox wrapper itself, so that's what `event.widget`
    resolves to while the user is typing in one. Return means "insert a
    newline" there, the same way it always has -- never "submit the
    dialog", which is the only reason this check exists."""
    return isinstance(widget, tk.Text)


def build_dialog_button_row(
    dialog,
    master=None,
    *,
    primary_text: str,
    on_primary,
    on_cancel=None,
    cancel_text: str = "Cancel",
    extra_buttons: list = None,
    pack_kwargs: dict = None,
):
    """
    Builds a dialog's bottom button row with consistent placement -- GitHub
    issue #13: Cancel on the left, the primary (default) action on the
    right, and any other buttons centered between them. Also binds Return
    on the whole dialog to the primary action (the issue's follow-up
    request for a "default action" triggered by the keyboard), except
    while a multi-line CTkTextbox has focus -- there, Return means "insert
    a newline" the same way it always has, not "submit the dialog".

    `master` defaults to `dialog` itself; pass a specific frame for
    dialogs that pack the button row into something other than the
    Toplevel directly.

    `on_cancel` defaults to `dialog.destroy` (plain Cancel, no side
    effects) -- pass an explicit callback for dialogs that need to do more
    than just close (e.g. release a grab first).

    `extra_buttons`, if given, is a list of (text, command) pairs placed
    in the center of the row, left to right, between Cancel and the
    primary action.

    `pack_kwargs` controls how the row frame itself is packed (padding,
    fill, side, ...) -- each dialog passes its own existing values so this
    change is purely about button order/binding, not spacing.

    Returns (row_frame, primary_button) -- callers that want to give the
    primary button initial keyboard focus (matching a native "default
    button" look) can use the latter.
    """
    row = ctk.CTkFrame(master or dialog, fg_color="transparent")
    row.pack(**(pack_kwargs or {"padx": 16, "pady": (12, 16), "fill": "x"}))

    ctk.CTkButton(
        row,
        text=cancel_text,
        width=scaled(BUTTON_WIDTH),
        command=on_cancel or dialog.destroy,
    ).pack(side="left")

    primary_button = ctk.CTkButton(
        row,
        text=primary_text,
        width=scaled(BUTTON_WIDTH),
        command=on_primary,
    )
    primary_button.pack(side="right")

    # Packed last so it claims exactly the cavity left between Cancel and
    # the primary button -- pack() carves space in packing order, so
    # Cancel/primary have to be placed first for this center slice to end
    # up in the middle rather than off to one side.
    if extra_buttons:
        center = ctk.CTkFrame(row, fg_color="transparent")
        center.pack(side="left", expand=True)
        for text, command in extra_buttons:
            ctk.CTkButton(center, text=text, width=scaled(BUTTON_WIDTH), command=command).pack(
                side="left", padx=4
            )

    def _on_return(event):
        if _is_multiline_text_widget(event.widget):
            return
        on_primary()

    dialog.bind("<Return>", _on_return)

    return row, primary_button
