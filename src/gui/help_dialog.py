"""
Modal, read-only dialog listing the application's keyboard shortcuts --
Help > Keyboard Shortcuts.

The shortcuts below are plain copies of the accelerator strings
`gui/app.py`'s `_build_menus()`/`_bind_keys()` already define; there's no
single shared source of truth for them (menus, key bindings, and this
dialog are three separate places), so if a shortcut is ever added,
changed, or removed in `app.py`, update _SECTIONS below to match.
"""

import platform
import customtkinter as ctk

from gui.tab_common import MONOSPACE_FONT_FAMILY

PLATFORM_SYSTEM = platform.system()

_ACC = "Command" if PLATFORM_SYSTEM == "Darwin" else "Control"

# (section title, [(action label, shortcut text), ...])
_SECTIONS = [
    (
        "File",
        [
            ("New Memory Buffer", f"{_ACC}+N"),
            ("Open State...", f"{_ACC}+O"),
            ("Save State", f"{_ACC}+S"),
            ("Preferences", f"{_ACC}+,"),
            ("Quit", f"{_ACC}+Q"),
        ],
    ),
    (
        "Connect",
        [
            ("Connect / Reconnect...", f"{_ACC}+K"),
            ("Disconnect", f"{_ACC}+D"),
            ("Set Calculator Time", f"{_ACC}+T"),
            ("Get State from DM41L", f"{_ACC}+G"),
            ("Send State to DM41L", f"{_ACC}+U"),
        ],
    ),
    (
        "View",
        [
            ("Refresh Tabs", "F5"),
        ],
    ),
    (
        # Export/Import aren't menu items at all -- they're header buttons
        # on the Data Registers/XM Files/Programs tabs -- but the shortcuts
        # are real and global (see _bind_keys()'s GitHub issue #7 comment
        # in app.py), so they belong here too.
        "Data Registers / XM Files / Programs tabs",
        [
            ("Export...", f"{_ACC}+E"),
            ("Import...", f"{_ACC}+I"),
        ],
    ),
]

DIALOG_WIDTH = 460


class KeyboardShortcutsDialog(ctk.CTkToplevel):
    """Blocking modal dialog that just lists every keyboard shortcut the
    app currently binds. Purely informational -- unlike the app's other
    dialogs (see gui/dialog_common.py) there's no Cancel/primary pair,
    just a single Close button."""

    def __init__(self, master):
        super().__init__(master)
        self.title("Keyboard Shortcuts")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        if PLATFORM_SYSTEM == "Darwin" and hasattr(master, "_menubar"):
            self.config(menu=master._menubar)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(padx=16, pady=(16, 8), fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0)

        row = 0
        for section_title, shortcuts in _SECTIONS:
            ctk.CTkLabel(
                body,
                text=section_title,
                font=ctk.CTkFont(weight="bold"),
                anchor="w",
            ).grid(
                row=row,
                column=0,
                columnspan=2,
                sticky="w",
                pady=(10 if row else 0, 4),
            )
            row += 1
            for label, shortcut in shortcuts:
                ctk.CTkLabel(body, text=label, anchor="w").grid(
                    row=row, column=0, sticky="w", padx=(12, 24), pady=2
                )
                ctk.CTkLabel(
                    body,
                    text=shortcut,
                    anchor="e",
                    font=ctk.CTkFont(family=MONOSPACE_FONT_FAMILY),
                ).grid(row=row, column=1, sticky="e", pady=2)
                row += 1

        ctk.CTkButton(self, text="Close", width=90, command=self.destroy).pack(
            padx=16, pady=(0, 16), anchor="e"
        )

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Return>", lambda e: self.destroy())
        self.bind("<Escape>", lambda e: self.destroy())

        self.update_idletasks()
        self.geometry(f"{DIALOG_WIDTH}x{self.winfo_reqheight()}")
        self.minsize(DIALOG_WIDTH, self.winfo_reqheight())
