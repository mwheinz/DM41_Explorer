"""
Switching the main window between the DM41L, DM41X and PX-41CX modes
(GitHub issues #43, #77 and #82).

The mode itself is `DM41ExplorerApp.mode`, the single source of truth for
the memory map in use. `ModeSwitcher` holds the ways it changes: from
Preferences (saved), and as an offer when a state too large for the
current mode, or saved by another mode's calculator (a PX-41CX dump, #82),
is opened (this session only). Changing mode erases the open state, so
every change goes through set_mode()'s confirmation.
"""

import logging
from pathlib import Path

from memory import Memory
from gui.dialog_common import ask_yes_no

logger = logging.getLogger(__name__)


class ModeSwitcher:
    """Changes the mode of the main window `app`."""

    def __init__(self, app):
        self.app = app

    def set_mode(self, mode, *, persist: bool, reason: str = None) -> bool:
        """Changes the effective mode, erasing the open state.

        Changing mode starts a new, empty state on the new mode's profile:
        a Memory fixes its profile when it is built, and
        rebuilding one through to_string()/from_string() to keep the
        contents would be a silent partial conversion in the narrowing
        direction. Moving a state between models is done by exporting the
        programs and data wanted and re-importing them.

        The sequence, which `reason` only adds a leading sentence to:
          1. the usual discard guard, if the buffer is modified;
          2. a confirmation naming the consequence, which also names the
             disconnect when a serial connection is open;
          3. the disconnect, then the switch and the empty buffer.

        `persist` writes the new mode to the preferences file. An
        auto-switch on load passes False, so it lasts for this session
        only. Returns whether the mode was changed; False means the user
        cancelled at some step, and neither the mode nor the open state
        was touched."""
        app = self.app
        if mode is app.mode:
            return True

        if not app.confirm_discard(f"switch to {mode.value} mode"):
            return False

        lines = []
        if reason:
            lines.append(reason)
        lines.append(
            f"Switching to {mode.value} mode starts a new, empty memory "
            "state. Anything still in the current one is discarded."
        )
        connected = app.connection.serial.is_connected
        if connected:
            lines.append("The serial connection will be closed.")
        lines.append(
            "To move programs or data between models, export them before "
            "switching and import them afterwards."
        )
        lines.append(f"Switch to {mode.value} mode?")
        # Wider than messagebox.askyesno(), for the list of findings an
        # auto-switch's `reason` carries (GitHub issue #55).
        if not ask_yes_no(app, "Switch Mode", "\n\n".join(lines)):
            return False

        if connected:
            app.connection.disconnect()

        previous = app.mode
        app.mode = mode
        # Each mode has its own built-in library and its own arrangement.
        app.xrom_libraries.apply()
        if persist:
            app.config_store.mode = mode
            app.persist_config("the DM41L/DM41X mode")
        logger.info(
            "Mode changed from %s to %s (%s)",
            previous.value,
            mode.value,
            "saved" if persist else "this session only",
        )

        app.menus.set_serial_enabled(mode.supports_serial)
        app.refresh_mode_indicator()
        app.replace_memory(Memory(profile=app.profile))
        app.set_status(
            f"Switched to {mode.value} mode; started a new, empty memory state."
        )
        return True

    def offer_switch_for_load(
        self, path, target, findings=(), *, written_by_target=False
    ) -> bool:
        """Offers `target` mode for the state at `path`, and returns whether
        the caller should go on to load it (in whichever mode is then in
        force).

        Offered for two reasons, which `findings` and `written_by_target`
        say: the state will not fit the current mode (`findings`, what
        does not fit), or its header says `target`'s calculator wrote it
        (#82). A state that does not fit is not loaded if the user
        declines; one that only came from the other calculator is then
        loaded in the current mode.

        The offer is deliberately session-only: someone who owns one
        calculator will rarely want the other mode permanently, so
        accepting does not write the preference. A declined offer leaves
        the mode alone."""
        app = self.app
        name = Path(path).name
        current = app.mode
        if findings:
            logger.info(
                "%s does not fit %s mode: %s",
                path,
                current.value,
                "; ".join(str(finding) for finding in findings),
            )
        # The findings are for the log, above: to decide whether to switch,
        # the user only needs to know the state is too big (issue #60).
        lines = []
        if written_by_target:
            lines.append(f"{name} was saved by a {target.value}.")
        if findings:
            lines.append(f"{name} needs more memory than a {current.value} has.")
        lines.append(f"{target.value} mode can open it.")
        if not findings:
            lines.append(f"If you don't switch, it opens in {current.value} mode.")
        if self.set_mode(target, persist=False, reason="\n\n".join(lines)):
            return True
        if findings:
            app.set_status(f"Did not load {name}: needs {target.value} mode.")
            return False
        logger.info("Opening %s in %s mode, as asked", path, current.value)
        return True

    def apply_preference(self, requested_mode):
        """Acts on the mode chosen in Preferences, which the dialog
        deliberately does NOT write to the config itself: changing mode
        erases the open state, so it has to go through set_mode()'s
        confirmation, and a cancelled switch must leave the saved
        preference as it was."""
        app = self.app
        if requested_mode is None:
            return
        if requested_mode is not app.mode:
            self.set_mode(requested_mode, persist=True)
        elif app.mode_is_temporary:
            # The dialog's value matches the mode already in force, so the
            # user has confirmed this session's override as the saved
            # setting. Nothing to erase; just stop calling it temporary.
            app.config_store.mode = requested_mode
            app.persist_config("the DM41L/DM41X mode")
            app.refresh_mode_indicator()
