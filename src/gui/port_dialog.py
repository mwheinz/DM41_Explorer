"""
Modal dialog for choosing a serial port to connect to the DM41L.
"""

import platform
import customtkinter as ctk

from gui.dialog_common import build_dialog_button_row
from gui.contrast import WARNING_TEXT

PLATFORM_SYSTEM = platform.system()

DIALOG_WIDTH = 420


class PortSelectionDialog(ctk.CTkToplevel):
    """
    Blocking modal dialog that uses a dropdown menu to let the user pick
    a serial port (or rescan, or cancel). The result is available via
    `.result` after the dialog is closed (None if cancelled).
    """

    def __init__(
        self, master, serial_manager, default_port: str = None, message: str = None
    ):
        super().__init__(master)
        self.title("Connect to DM41L")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        if PLATFORM_SYSTEM == "Darwin" and hasattr(master, "_menubar"):
            self.config(menu=master._menubar)

        self._serial_manager = serial_manager
        self.result = None
        self._default_port = default_port

        if message:
            ctk.CTkLabel(
                self,
                text=message,
                wraplength=DIALOG_WIDTH - 40,
                justify="left",
                text_color=WARNING_TEXT,
            ).pack(padx=16, pady=(16, 8), anchor="w")

        ctk.CTkLabel(self, text="Available serial ports:").pack(
            padx=16, pady=(4, 4), anchor="w"
        )

        self._selected_port = ctk.StringVar(value="")
        self._port_menu = ctk.CTkOptionMenu(self, variable=self._selected_port)
        self._port_menu.pack(padx=16, pady=(0, 20), fill="x")

        self._populate_ports()

        _, self._connect_button = build_dialog_button_row(
            self,
            primary_text="Connect",
            on_primary=self._on_connect,
            on_cancel=self._on_cancel,
            extra_buttons=[("Rescan", self._populate_ports)],
            pack_kwargs={"padx": 16, "pady": (0, 16), "fill": "x"},
        )

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # Fix the dialog to a constant width so short status messages don't
        # leave the buttons jammed together; height still adapts to whether
        # a message is shown.
        self.update_idletasks()
        self.geometry(f"{DIALOG_WIDTH}x{self.winfo_reqheight()}")
        self.minsize(DIALOG_WIDTH, self.winfo_reqheight())

    def _populate_ports(self):
        """Refreshes the dropdown options with current available ports."""
        ports = self._serial_manager.get_available_ports()

        if not ports:
            self._port_menu.configure(values=["No ports found"], state="disabled")
            self._selected_port.set("")
            return

        self._port_menu.configure(values=ports, state="normal")

        preselect = self._default_port if self._default_port in ports else ports[0]
        self._selected_port.set(preselect)

    def _on_connect(self):
        port = self._selected_port.get()
        if port:
            self.result = port
            self.grab_release()
            self.destroy()

    def _on_cancel(self):
        self.result = None
        self.grab_release()
        self.destroy()
