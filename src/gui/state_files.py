"""
Opening and saving memory state files from the main window (GitHub issue
#77): File > New, Open, Open Recent, Save and Save As, a file passed at
launch, and macOS's "Open Document" for a double-clicked state file.

A DM41L `.dm41` state and a DM41X `.d41` state file are the same text
format, so a file cannot say which model wrote it: a state is always
opened with the app's current mode, and one too large for that mode is
offered the DM41X mode instead (gui/device_mode.py). A PX-41CX `.px41`
dump is a variant of the same format (#82).
"""

import logging
from pathlib import Path
from tkinter import filedialog, messagebox

from memory import DeviceMode, Memory, evaluate_mode_switch

logger = logging.getLogger(__name__)

STATE_EXTENSIONS = (".dm41", ".d41", ".px41")

# What Save As offers for a buffer that has no file of its own yet: the
# extension the user's own calculator writes, so a new state lands with the
# name they expect.
DEFAULT_EXTENSION_BY_MODE = {
    DeviceMode.DM41L: ".dm41",
    DeviceMode.DM41X: ".d41",
    DeviceMode.PX41CX: ".px41",
}
# The layout a file is written in, by its extension (#82): naming a file
# .px41 saves a PX-41CX dump, whatever the mode or where the state came
# from, and .dm41 or .d41 a DM41 state. A file with any other extension is
# written in the current mode's own layout (DeviceProfile.state_header).
HEADER_BY_EXTENSION = {
    ".dm41": "DM41",
    ".d41": "DM41",
    ".px41": "PX41",
}
STATE_FILETYPES = [
    ("Memory state", ("*.dm41", "*.d41", "*.px41")),
    ("DM41 memory state", ("*.dm41", "*.d41")),
    ("PX-41CX memory dump", "*.px41"),
    ("All files", "*.*"),
]


class StateFiles:
    """The File menu's state-file actions for the main window `app`."""

    def __init__(self, app):
        self.app = app

    def new_buffer(self):
        """File > New Memory Buffer."""
        app = self.app
        if not app.confirm_discard("start a new buffer"):
            return
        app.replace_memory(Memory(profile=app.profile))
        app.set_status("Started a new, empty memory buffer.")

    # -- Saving -----------------------------------------------------------

    def save(self):
        """File > Save State: overwrites the buffer's own file, after
        asking, or asks for one if it has none yet."""
        source = self.app.memory_source
        if source is None:
            self.save_as()
            return
        if not messagebox.askyesno(
            "Overwrite File", f"Overwrite {source} with your changes?"
        ):
            return
        self._write(source)

    def save_as(self):
        """File > Save State As..."""
        app = self.app
        source = app.memory_source
        # A file keeps its own extension. A buffer with no file of its own
        # gets the extension the current mode's calculator writes (.dm41
        # for a DM41L, .d41 for a DM41X, .px41 for a PX-41CX). Either way,
        # the extension the file ends up with decides its layout (_write).
        extension = (
            source.suffix
            if source and source.suffix in STATE_EXTENSIONS
            else DEFAULT_EXTENSION_BY_MODE[app.mode]
        )
        path = filedialog.asksaveasfilename(
            defaultextension=extension,
            filetypes=STATE_FILETYPES,
        )
        if not path:
            return
        self._write(path)

    def header_for(self, path) -> str:
        """The header, and so the layout, a state saved to `path` gets: the
        one its extension calls for, or else the current mode's."""
        suffix = Path(path).suffix.lower()
        return HEADER_BY_EXTENSION.get(suffix, self.app.profile.state_header)

    def _write(self, path):
        """Writes the buffer to `path`, which then becomes its file, in the
        layout its extension calls for (header_for()). Shared by Save and
        Save As; any confirmation is the caller's."""
        app = self.app
        memory = app.memory
        previous = memory.header
        memory.header = self.header_for(path)
        try:
            memory.to_file(path)
        except Exception as e:
            # Not saved, so the buffer keeps the layout it had.
            memory.header = previous
            logger.exception("Could not save state to %s", path)
            messagebox.showerror("Error", f"Could not save state: {e}")
            return
        if memory.header != previous:
            logger.info(
                "Saving in the %s layout instead of %s", memory.header, previous
            )
        logger.info("State saved to %s", path)
        app.show_saved_as(path)
        app.menus.remember_recent_file(path)
        messagebox.showinfo("Saved", f"Memory state written to {path}")

    # -- Opening ----------------------------------------------------------

    def open_dialog(self):
        """File > Open State..."""
        if not self.app.confirm_discard("load a different state"):
            return
        path = filedialog.askopenfilename(filetypes=STATE_FILETYPES)
        if not path:
            return
        self.load(path)

    def open(self, path):
        """Opens `path` as the app's current state, prompting to discard
        unsaved changes first if needed (same prompt File > Open... uses).

        The entry point for anything that hands the app a file path
        directly, bypassing the File > Open... dialog: File > Open Recent,
        a startup file argument (double-clicking a .dm41 file on
        Windows/Linux, or launching fresh via double-click on macOS -- see
        gui/app.py's `_handle_startup_file_arg`/main()) and the macOS "Open
        Document" AppleEvent for double-clicking a .dm41 file while the
        app is already running (see `open_documents` below)."""
        if not Path(path).exists():
            logger.warning("Could not find file: %s", path)
            messagebox.showerror("Error", f"Could not find file: {path}")
            self.app.menus.forget_recent_file(path)
            return
        if not self.app.confirm_discard("load a different state"):
            return
        self.load(path)

    def open_documents(self, *paths):
        """Handles macOS's "Open Document" AppleEvent -- what Finder sends
        for double-clicking a .dm41 file, whether that launches the app
        fresh or the app is already running. Tk surfaces this as a
        registered command (wired in DM41ExplorerApp.__init__) rather than
        a normal widget-level event or sys.argv, per
        https://www.tcl.tk/man/tcl/TkCmd/tk_mac.html."""
        if not paths:
            return
        # Finder can in principle hand over more than one path at once
        # (e.g. multi-selecting files and choosing Open); this app only
        # has one buffer, so just open the last one -- "last one wins" is
        # the least surprising choice for a single-document app.
        self.open(paths[-1])

    def load(self, path):
        """Reads `path` into the app's buffer and refreshes the UI to match.
        This is the actual load step shared by every way of opening a state
        -- callers are responsible for checking whether the buffer is
        modified and confirming with the user first, since the right prompt
        (or whether to prompt at all) differs by caller.

        A state is opened with the current mode's profile. If it does not
        fit that model, the user is offered DM41X mode instead
        (ModeSwitcher.offer_switch_for_load); declining leaves the open state
        alone, so nothing is assigned here until that is settled."""
        app = self.app
        # Parsed into a local first: the state only replaces the open
        # buffer once it is known to fit the mode (or the user has agreed
        # to switch), so a declined offer is a true no-op.
        loaded = self._read(path)
        if loaded is None:
            return

        findings = evaluate_mode_switch(loaded, app.profile)
        if findings and evaluate_mode_switch(loaded, DeviceMode.DM41X.profile):
            # It does not fit DM41X mode either -- an XM directory that
            # cannot be read in any mode -- so offering that mode as the
            # cure would be wrong (GitHub issue #68). It opens as it is,
            # as it would in DM41X mode, and the XM Files tab says why
            # the directory cannot be listed.
            logger.warning(
                "%s has problems no mode fixes: %s",
                path,
                "; ".join(str(finding) for finding in findings),
            )
        elif findings:
            if not app.mode_switcher.offer_switch_for_load(path, findings):
                return
            # The mode moved, so the state has to be re-read under the new
            # profile: the one just parsed still carries the old memory map.
            loaded = self._read(path)
            if loaded is None:
                return

        app.replace_memory(loaded, source=path)
        app.set_status(f"Loaded state from {Path(path).name}")
        logger.info("State loaded from %s (%s mode)", path, app.mode.value)
        app.menus.remember_recent_file(path)

    def _read(self, path):
        """`path` read with the app's current profile, or None after telling
        the user why it could not be read."""
        try:
            return Memory.from_file(path, profile=self.app.profile)
        except Exception as e:
            logger.exception("Could not load state from %s", path)
            messagebox.showerror("Error", f"Could not load state: {e}")
            return None
