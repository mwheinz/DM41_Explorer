"""Helpers for tests that build real Tk windows.

Window-system geometry is only meaningful once a window is really on screen,
and that takes event-loop time that differs per platform -- on Windows,
customtkinter withdraws every new window while it recolors the titlebar and
brings it back from an after() callback, and a window's reported size lags
behind what was set on it until the loop has run.  Tests that read a
window's geometry therefore map it and pump the loop first (show, pump), or
else assert on what the code asked for rather than on what the window system
reported.
"""

import time


def pump(window, seconds: float = 0.5, until=None) -> bool:
    """Runs Tk's event loop for up to `seconds` (or until `until()` is true).
    Returns whether `until` was met (always True without one)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        window.update()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until() if until is not None else True


def show(window, timeout: float = 5.0) -> bool:
    """Maps `window` and pumps the event loop until it is really on screen."""
    window.deiconify()
    return pump(window, timeout, until=window.winfo_viewable)
