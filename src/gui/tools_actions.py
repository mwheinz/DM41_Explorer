"""
The Tools menu's actions (GitHub issue #77): Pack Memory, and the XROM
libraries -- applying the arrangement saved for the current mode, and the
XROM Libraries manager (GitHub issue #53).
"""

import logging
from tkinter import messagebox

from memory.mnemonics import set_active_libraries
from memory.xrom_import import user_library_paths
from memory.xrom_library import WARNING as XROM_WARNING
from memory.xrom_library import active_set, find_conflicts, load_libraries
from gui.xrom_libraries_dialog import XromLibrariesDialog

logger = logging.getLogger(__name__)


def pack_memory(app):
    """Tools > Pack Memory: repacks Key Assignments/Alarms and program
    memory the way a real PACK does (see Memory.pack()), reporting the
    registers reclaimed and any programs whose global labels became
    visible. Useful before an Import, to make the most free space
    available. It lives in the Tools menu rather than on the Programs
    tab because it also changes Key Assignments and Alarms."""
    memory = app.memory
    before_count = len(memory.programs.list_programs())
    try:
        freed = memory.pack()
    except Exception as e:
        logger.warning("Could not pack memory: %s", e)
        messagebox.showerror("Could Not Pack Memory", str(e))
        return
    recovered = len(memory.programs.list_programs()) - before_count
    logger.info(
        "Packed memory: %d register(s) reclaimed, %d program(s) recovered",
        freed,
        recovered,
    )
    app.on_memory_changed()
    app.render_tabs()
    if freed > 0 or recovered > 0:
        summary = f"{freed} register(s) reclaimed"
        if recovered > 0:
            summary += (
                f", {recovered} program(s) recovered -- their global "
                "labels are now visible and can be assigned to a key"
            )
        app.set_status(f"Packed memory -- {summary}.")
        messagebox.showinfo("Pack Memory", f"Packed memory: {summary}.")
    else:
        app.set_status("Packed memory -- already fully packed.")
        messagebox.showinfo(
            "Pack Memory",
            "Memory is already fully packed -- nothing to reclaim.",
        )


class XromLibraries:
    """The XROM libraries in use by the main window `app`. `active` is the
    active set for the app's mode, once apply() has run."""

    def __init__(self, app):
        self.app = app
        self.active = None

    def apply(self):
        """Makes the XROM libraries arranged for the current mode active:
        reads the shipped and the user's library files, works out the
        active set (the mode's built-in library, then the enabled ones in
        order) and rebuilds the instruction-name registry with it. Returns
        the files that couldn't be loaded, which are logged; the manager
        lists them."""
        config = self.app.config_store
        mode = self.app.mode
        paths = user_library_paths(config.xrom_directory)
        libraries, errors = load_libraries(user_paths=paths)
        for error in errors:
            logger.warning("XROM library not loaded: %s", error)
        self.active = active_set(
            libraries, mode.value, config.enabled_xrom_library_ids(mode)
        )
        if self.active.missing:
            logger.warning(
                "Enabled XROM libraries not found: %s",
                ", ".join(self.active.missing),
            )
        set_active_libraries(self.active.libraries)
        self.app.refresh_mnemonics_reference()
        return errors

    def show_manager(self):
        """Tools > XROM Libraries...: opens the manager. Returns the
        dialog."""
        app = self.app
        return XromLibrariesDialog(
            app, app.config_store, app.mode, on_saved=self._on_saved
        )

    def _on_saved(self, entries):
        """Saves the manager's arrangement for the current mode, applies it
        and redraws the tabs, so names follow at once. If the active set
        changed and its libraries clash on an XROM number, says so once."""
        app = self.app
        before = [library.id for library in self.active.libraries]
        app.config_store.set_xrom_library_entries(app.mode, entries)
        app.persist_config("the XROM libraries")
        self.apply()
        app.render_tabs()
        active = self.active.libraries
        plugins = [library for library in active if not library.builtin_on]
        app.set_status(
            f"XROM libraries: {len(plugins)} plug-in "
            f"{'library' if len(plugins) == 1 else 'libraries'} active."
        )
        if [library.id for library in active] == before:
            return
        warnings = [f for f in find_conflicts(active) if f.level == XROM_WARNING]
        if warnings:
            messagebox.showwarning(
                "XROM Libraries",
                "\n\n".join(str(warning) for warning in warnings),
                parent=app,
            )
