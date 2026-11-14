"""
Opening the main window at its remembered size and position, and saving
them again as the user changes them (GitHub issues #42, #63 and #77).

The rules for what size and position to use are the pure functions in
gui/window_geometry.py; `WindowSizeKeeper` applies them to the live
window, and saves the size a moment after the user stops resizing, so it
survives however the app ends (force quit, crash, a quit path that never
reaches on_close()).
"""

import logging

import customtkinter as ctk

from gui.screens import screen_rectangles
from gui.window_geometry import (
    geometry_string,
    initial_window_size,
    minimum_window_size,
    parse_geometry,
    parse_position,
    restored_position,
)

logger = logging.getLogger(__name__)


class WindowSizeKeeper:
    """Keeps the size and position of `window`, a CTk root window, in
    `window.config_store`."""

    # How long after the last resize event the window size is saved.
    RESIZE_SAVE_DELAY_MS = 1000

    # How long the window manager gets to place the window at launch
    # before a size change counts as the user's.
    TRACKING_DELAY_MS = 2000

    def __init__(self, window):
        self.window = window
        # Whether resizes are the user's yet (see apply()).
        self.tracking = False
        # The size and position last applied or saved: only a change from
        # these is written to the preferences.
        self.applied_size = None
        self.applied_position = None
        self._resize_after_id = None
        self._tracking_after_id = None

    def apply(self):
        """Sets the minimum size and the opening size (GitHub issue #42).

        Must run after the window's layout is built: the minimum is never
        less than what the tab bar and status bar ask for, and both grow
        with the application font. The opening size is the one saved at
        the last close if there is one, else a default scaled with the
        font; see gui/window_geometry.py for the rules.
        """
        window = self.window
        config = window.config_store
        window.update_idletasks()
        needed = (
            window._reverse_window_scaling(window.winfo_reqwidth()),
            window._reverse_window_scaling(window.winfo_reqheight()),
        )
        screen = (
            window._reverse_window_scaling(window.winfo_screenwidth()),
            window._reverse_window_scaling(window.winfo_screenheight()),
        )
        font_size = ctk.ThemeManager.theme["CTkFont"]["size"]
        window.minsize(*minimum_window_size(font_size, needed, screen))
        width, height = initial_window_size(
            config.window_size, font_size, needed, screen
        )
        # The position saved at the last close too, if the window would
        # still be on a screen there (GitHub issue #63); otherwise the
        # window manager places it.
        position = restored_position(
            config.window_position, (width, height), screen_rectangles(window)
        )
        window.geometry(geometry_string((width, height), position))
        self.applied_size = (width, height)
        self.applied_position = position
        # Let the window manager finish placing the window before resizes
        # count as the user's: the size is then re-read, and every later
        # change is saved shortly after it stops (see _on_configure()).
        self._tracking_after_id = window.after(
            self.TRACKING_DELAY_MS, self._start_tracking
        )

    def _start_tracking(self):
        window = self.window
        self._tracking_after_id = None
        window.update_idletasks()
        geometry = window.geometry()
        self.applied_size = parse_geometry(geometry) or self.applied_size
        self.applied_position = parse_position(geometry) or self.applied_position
        self.tracking = True
        window.bind("<Configure>", self._on_configure, add="+")

    def _on_configure(self, event):
        """Saves the window size a moment after the user stops resizing."""
        if event.widget is not self.window or not self.tracking:
            return
        if self._resize_after_id is not None:
            self.window.after_cancel(self._resize_after_id)
        self._resize_after_id = self.window.after(
            self.RESIZE_SAVE_DELAY_MS, self._save_after_resize
        )

    def _save_after_resize(self):
        self._resize_after_id = None
        self.remember()

    def cancel_timers(self):
        """Cancels the pending size timers, so none fires into a window
        that no longer exists."""
        for name in ("_tracking_after_id", "_resize_after_id"):
            after_id = getattr(self, name)
            if after_id is not None:
                try:
                    self.window.after_cancel(after_id)
                except Exception:
                    pass
                setattr(self, name, None)

    def save_now(self):
        """For closing: saves the size now instead of when the pending
        timer would have."""
        if self._resize_after_id is not None:
            self.window.after_cancel(self._resize_after_id)
            self._resize_after_id = None
        self.remember()

    def is_maximized(self) -> bool:
        """True if the window is maximized/zoomed, whose size must not be
        remembered (restoring it would open a normal window at full-screen
        size)."""
        try:
            if self.window.state() == "zoomed":
                return True
        except Exception:
            pass
        try:
            return bool(self.window.attributes("-zoomed"))  # X11
        except Exception:
            return False

    def remember(self):
        """Saves the current window size and position to the preferences,
        for the next launch. Only what the user changed is saved, and the
        position only once tracking has started: until then the window
        manager may still be placing the window. Never raises: failing to
        save mustn't stop the app from closing."""
        config = self.window.config_store
        try:
            if self.is_maximized():
                return
            geometry = self.window.geometry()
            size = parse_geometry(geometry)
            position = parse_position(geometry) if self.tracking else None
            new_size = size is not None and size != self.applied_size
            new_position = position is not None and position != self.applied_position
            if not (new_size or new_position):
                return  # nothing the user changed: don't touch the file
            if new_size:
                config.window_size = size
            if new_position:
                config.window_position = position
            config.save()
            # Only once saved, so a failed save is tried again later.
            if new_size:
                self.applied_size = size
            if new_position:
                self.applied_position = position
        except Exception as e:
            logger.warning("Could not save the window size and position: %s", e)
