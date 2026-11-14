"""
The main window's menu bar and keyboard shortcuts (GitHub issue #77).

Every basic function lives in the menus rather than in toolbar buttons.
`MenuBar` builds the menus, binds the shortcuts they show, keeps
File > Open Recent in step with the recent-files preference, and greys
out the Connect menu in a mode with no serial console. The actions
themselves belong to the main window and its helpers; this module only
wires them up.
"""

import logging
from tkinter import Menu

from gui.tools_actions import pack_memory

logger = logging.getLogger(__name__)

# File > Open Recent: long paths are shortened by keeping the tail
# (the part that actually distinguishes files living in different
# folders) rather than the head.
_MAX_RECENT_LABEL_LEN = 60


def _format_recent_label(path_str: str) -> str:
    if len(path_str) <= _MAX_RECENT_LABEL_LEN:
        return path_str
    return "…" + path_str[-(_MAX_RECENT_LABEL_LEN - 1) :]


class MenuBar:
    """The menu bar of the main window `app`. `is_mac` puts About and
    Preferences in the macOS application menu and uses Command for the
    shortcuts; elsewhere they go in the File and Help menus and use
    Control."""

    def __init__(self, app, *, is_mac: bool):
        self.app = app
        self.is_mac = is_mac
        self._acc = "Command" if is_mac else "Control"
        self.menubar = self._build()
        self.rebuild_recent_files()

    # -- Building ---------------------------------------------------------

    def _build(self):
        app = self.app
        acc = self._acc
        menubar = Menu(app)

        if self.is_mac:
            # About and Preferences live in the application menu (under the
            # app's name), where Mac users look for them. Tk shows that menu's
            # Preferences item, with its standard Cmd+, shortcut, only once
            # this command exists; a Preferences item elsewhere doesn't get
            # to show Cmd+, at all (GitHub issue #62).
            app.createcommand("tkAboutDialog", app.show_about)
            app.createcommand("::tk::mac::ShowPreferences", app.show_preferences)

        # File Menu
        file_menu = Menu(menubar, tearoff=0)
        file_menu.add_command(
            label="New Memory Buffer",
            command=app.new_memory_buffer,
            accelerator=f"{acc}+N",
            underline=0,
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Open State...",
            command=app.load_state_from_file,
            accelerator=f"{acc}+O",
            underline=0,
        )
        self.recent_menu = Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label="Open Recent", menu=self.recent_menu, underline=5)
        file_menu.add_command(
            label="Save State",
            command=app.save_state_to_file,
            accelerator=f"{acc}+S",
            underline=0,
        )
        file_menu.add_command(
            label="Save State As...",
            command=app.save_state_as,
            accelerator=f"{acc}+Shift+S",
            underline=13,
        )
        if not self.is_mac:
            # On macOS these are in the application menu instead (above).
            file_menu.add_separator()
            file_menu.add_command(
                label="About Box", command=app.show_about, underline=0
            )
            file_menu.add_command(
                label="Preferences",
                command=app.show_preferences,
                accelerator=f"{acc}+,",
                underline=0,
            )
            file_menu.add_separator()
            file_menu.add_command(
                label="Quit", command=app.on_close, accelerator=f"{acc}+Q", underline=0
            )
        menubar.add_cascade(label="File", menu=file_menu)

        # Connect Menu
        connection = app.connection
        connect_menu = Menu(menubar, tearoff=0)
        connect_menu.add_command(
            label="Connect / Reconnect...",
            command=connection.show_connect_dialog,
            accelerator=f"{acc}+K",
            underline=0,
        )
        connect_menu.add_command(
            label="Disconnect",
            command=connection.disconnect,
            accelerator=f"{acc}+D",
            underline=0,
        )
        connect_menu.add_separator()
        connect_menu.add_command(
            label="Set Calculator Time",
            command=connection.set_calculator_time,
            accelerator=f"{acc}+T",
            underline=4,
        )
        connect_menu.add_separator()
        connect_menu.add_command(
            label="Get State from DM41L",
            command=connection.get_state_from_calculator,
            accelerator=f"{acc}+G",
            underline=0,
        )
        connect_menu.add_command(
            label="Send State to DM41L",
            command=connection.send_state_to_calculator,
            accelerator=f"{acc}+U",
            underline=0,
        )
        # Every entry in this menu needs a serial console, so the whole
        # menu is gated by the mode (see set_serial_enabled). The indices
        # are collected from the entries rather than hard-coded, so
        # inserting an entry later cannot silently gate the wrong one.
        self.connect_menu = connect_menu
        self.serial_menu_indices = [
            index
            for index in range(connect_menu.index("end") + 1)
            if connect_menu.type(index) == "command"
        ]
        menubar.add_cascade(label="Connect", menu=connect_menu)

        # View Menu
        view_menu = Menu(menubar, tearoff=0)
        view_menu.add_command(
            label="Refresh Tabs",
            command=app.render_tabs,
            accelerator="F5",
            underline=0,
        )
        menubar.add_cascade(label="View", menu=view_menu)

        # Tools Menu
        tools_menu = Menu(menubar, tearoff=0)
        tools_menu.add_command(
            label="Pack Memory...",
            command=lambda: pack_memory(app),
            underline=0,
        )
        tools_menu.add_command(
            label="XROM Libraries...",
            command=app.xrom_libraries.show_manager,
            underline=0,
        )
        menubar.add_cascade(label="Tools", menu=tools_menu)

        # Help Menu
        help_menu = Menu(menubar, tearoff=0)
        if not self.is_mac:
            help_menu.add_command(label="About DM41_Explorer", command=app.show_about)
            help_menu.add_separator()
        help_menu.add_command(
            label="Keyboard Shortcuts",
            command=app.show_keyboard_shortcuts,
            underline=0,
        )
        help_menu.add_command(
            label="FOCAL Mnemonics Reference",
            command=app.show_mnemonics_reference,
            underline=0,
        )
        menubar.add_cascade(label="Help", menu=help_menu)

        app.config(menu=menubar)
        return menubar

    def bind_shortcuts(self):
        """Binds the keyboard shortcuts. A menu's `accelerator=` text is only
        a label in Tk -- every shortcut shown in the menus needs its own
        bind() here. Each binding looks its action up when the key is
        pressed, not when it is bound."""
        app = self.app
        acc = self._acc

        app.bind(f"<{acc}-n>", lambda e: app.new_memory_buffer())
        app.bind(f"<{acc}-o>", lambda e: app.load_state_from_file())
        app.bind(f"<{acc}-s>", lambda e: app.save_state_to_file())
        # Shift turns the key into "S" on some platforms but not others, so
        # both are bound. Either is more specific than <{acc}-s> above,
        # which therefore doesn't also fire.
        for key in ("S", "s"):
            app.bind(f"<{acc}-Shift-{key}>", lambda e: app.save_state_as())
        app.bind(f"<{acc}-q>", lambda e: app.on_close())
        app.bind(f"<{acc}-comma>", lambda e: app.show_preferences())
        app.bind("<F5>", lambda e: app.render_tabs())

        # Export.../Import... are tab-scoped header buttons (Data
        # Registers, XM Files, Programs; Cmd/Ctrl+E is Save PDF... on Key
        # Assignments), not menu items, so their shortcuts are bound
        # globally here and forward to whichever tab is active -- see
        # DM41ExplorerApp.dispatch_tab_action().
        app.bind(f"<{acc}-e>", lambda e: app.dispatch_tab_action("export_method"))
        app.bind(f"<{acc}-i>", lambda e: app.dispatch_tab_action("import_method"))

        # Connect menu (GitHub issue #9) -- each of these already no-ops
        # with a "Not Connected" warning (or, for disconnect(), silently)
        # when the serial port isn't open, exactly like clicking the menu
        # item itself would, so binding them globally doesn't open up any
        # new not-connected crash path.
        app.bind(f"<{acc}-k>", lambda e: app.connection.show_connect_dialog())
        app.bind(f"<{acc}-d>", lambda e: app.connection.disconnect())
        app.bind(f"<{acc}-t>", lambda e: app.connection.set_calculator_time())
        app.bind(f"<{acc}-g>", lambda e: app.connection.get_state_from_calculator())
        app.bind(f"<{acc}-u>", lambda e: app.connection.send_state_to_calculator())

    # -- The mode ---------------------------------------------------------

    def set_serial_enabled(self, enabled: bool):
        """Greys out the serial actions in a mode whose calculator has no
        serial console -- disabled rather than hidden, so the user can see
        the app has them and why they are unavailable.

        The keyboard shortcuts bypass menu state entirely, so every one of
        these actions also guards itself with
        Connection.require_serial_mode()."""
        state = "normal" if enabled else "disabled"
        for index in self.serial_menu_indices:
            self.connect_menu.entryconfigure(index, state=state)

    # -- File > Open Recent -----------------------------------------------

    def rebuild_recent_files(self):
        """Repopulates File > Open Recent from the recent-files preference
        -- once when the menus are built, and again every time the list
        changes (a state opened or saved, a stale entry pruned, or Clear
        Recent Files). Tk menu items are static once added, so the submenu
        has to be torn down and rebuilt rather than updated in place."""
        self.recent_menu.delete(0, "end")
        recent = self.app.config_store.recent_files
        if not recent:
            self.recent_menu.add_command(label="(No Recent Files)", state="disabled")
            return
        for path_str in recent:
            self.recent_menu.add_command(
                label=_format_recent_label(path_str),
                command=lambda p=path_str: self.app.open_state_file(p),
            )
        self.recent_menu.add_separator()
        self.recent_menu.add_command(
            label="Clear Recent Files", command=self._clear_recent_files
        )

    def remember_recent_file(self, path):
        """Puts `path` at the top of File > Open Recent."""
        self.app.config_store.add_recent_file(path)
        self._recent_files_changed()

    def forget_recent_file(self, path):
        """Takes `path` out of File > Open Recent, e.g. once it is gone."""
        self.app.config_store.remove_recent_file(path)
        self._recent_files_changed()

    def _clear_recent_files(self):
        self.app.config_store.clear_recent_files()
        self._recent_files_changed()

    def _recent_files_changed(self):
        """Saves the recent-files list and rebuilds its menu to match."""
        self.app.persist_config("recent files")
        self.rebuild_recent_files()
