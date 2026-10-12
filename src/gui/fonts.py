"""
The application's font roles (GitHub issue #61).

Every font the app uses comes from one of four named roles, each a family,
a size and bold, set in Preferences:

  * "ui"    -- labels, buttons, tabs, tables: CustomTkinter's theme font
  * "small" -- captions, hints, the status bar
  * "title" -- dialog and section headings
  * "mono"  -- hex bytes, register contents, program text, XROM fields

A role's family or size left at "" / 0 in the preferences file means "this
platform's default", worked out here rather than stored, so a change to the
defaults reaches existing users:

  * "ui" uses the family Tk's own TkDefaultFont resolves to -- the system
    font on macOS and Windows, the desktop's default on Linux -- at
    CustomTkinter's theme size. CustomTkinter's theme asks for "SF Display"
    or "Roboto" instead, which are usually not installed, so the font
    actually drawn was whatever the system substituted.
  * "mono" uses TkFixedFont's family (Menlo, Consolas, DejaVu Sans Mono,
    ...) at the "ui" size.
  * "small" is the "ui" font 2 points smaller; "title" is the "ui" font in
    bold.

A configured family that isn't installed falls back to the role's default,
so a preferences file copied from another machine stays readable.

resolve_roles() is the rule, with no Tk, so it is unit-testable headless.
apply() feeds it the facts only a live Tk knows and records the answer;
font_for() and font_spec() hand it out. Fonts are read when a widget is
built, so a change takes effect after a restart -- as before.
"""

import logging
import tkinter
import tkinter.font as tkfont
from typing import NamedTuple

import customtkinter as ctk

from config import FONT_ROLES

logger = logging.getLogger(__name__)

# No role's size goes below this, so a caption never becomes unreadable
# however small the main font is set.
MIN_FONT_SIZE = 8

# How much smaller the default "small" role is than the "ui" role.
SMALL_FONT_OFFSET = 2


class FontSpec(NamedTuple):
    family: str
    size: int
    bold: bool

    def tk_tuple(self) -> tuple:
        """The Tk font description, e.g. for a ttk style's font=."""
        if self.bold:
            return (self.family, self.size, "bold")
        return (self.family, self.size)


class PlatformDefaults(NamedTuple):
    """What a role left at "" / 0 resolves to on this machine."""

    ui_family: str
    mono_family: str
    size: int


def resolve_roles(stored: dict, defaults: PlatformDefaults, installed) -> dict:
    """{role: FontSpec} for every role in FONT_ROLES.

    `stored` maps a role to its (family, size, bold) from the preferences
    file (see ProjectConfig.font_role()); a missing role counts as all
    defaults. `installed` is the set of family names this machine has; a
    stored family not among them (compared without case) falls back to the
    role's default family."""
    installed = {name.casefold() for name in installed}

    def pick(role, default_family, default_size, default_bold):
        family, size, bold = stored.get(role, ("", 0, default_bold))
        if family and family.casefold() not in installed:
            logger.warning(
                "Font %r for the %s role is not installed; using %r",
                family,
                role,
                default_family,
            )
            family = ""
        return FontSpec(
            family or default_family,
            max(MIN_FONT_SIZE, size or default_size),
            bold,
        )

    ui = pick("ui", defaults.ui_family, defaults.size, False)
    return {
        "ui": ui,
        "small": pick("small", ui.family, ui.size - SMALL_FONT_OFFSET, False),
        "title": pick("title", ui.family, ui.size, True),
        "mono": pick("mono", defaults.mono_family, ui.size, False),
    }


# The fonts apply() settled on, {role: FontSpec}; empty until it runs.
_resolved: dict = {}


def _named_font_family(root, name: str) -> str:
    return tkfont.nametofont(name, root=root).actual("family")


def apply(config_store, root=None) -> dict:
    """Works out every role's font from `config_store` (a ProjectConfig, or
    None for all defaults) and this machine, records them for font_for(),
    and makes the "ui" role CustomTkinter's theme font -- which every
    CTkFont() and every widget without its own font= picks up when built.

    Has to run after the color theme is set (which resets the theme font)
    and before any widget is built. `root` is any Tk widget; it defaults
    to the default root. Returns {role: FontSpec}."""
    root = root or tkinter._get_default_root("look up the platform fonts")
    theme_font = ctk.ThemeManager.theme["CTkFont"]
    defaults = PlatformDefaults(
        ui_family=_named_font_family(root, "TkDefaultFont"),
        mono_family=_named_font_family(root, "TkFixedFont"),
        size=theme_font["size"],
    )
    stored = (
        {role: config_store.font_role(role) for role in FONT_ROLES}
        if config_store is not None
        else {}
    )
    resolved = resolve_roles(stored, defaults, tkfont.families(root))
    theme_font["family"] = resolved["ui"].family
    theme_font["size"] = resolved["ui"].size
    _resolved.clear()
    _resolved.update(resolved)
    return dict(resolved)


def reset() -> None:
    """Forgets the fonts apply() settled on, so the next font_for() works
    them out again. For tests."""
    _resolved.clear()


def font_spec(role: str) -> FontSpec:
    """The FontSpec of `role`. If apply() hasn't run (a test building one
    widget on its own), it runs now with every role at its default."""
    if role not in FONT_ROLES:
        raise ValueError(f"Unknown font role {role!r}")
    if not _resolved:
        apply(None)
    return _resolved[role]


def font_for(role: str, **overrides) -> "ctk.CTkFont":
    """A CTkFont in `role`'s family, size and weight. `overrides` (size=,
    weight=, slant=, ...) pass through to CTkFont for the few places that
    need a variant of a role, e.g. a bold caption."""
    spec = font_spec(role)
    kwargs = {
        "family": spec.family,
        "size": spec.size,
        "weight": "bold" if spec.bold else "normal",
    }
    kwargs.update(overrides)
    return ctk.CTkFont(**kwargs)
