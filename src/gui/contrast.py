"""
Color-contrast helpers (GitHub issue #42, item 1).

CustomTkinter's built-in themes pair colors that read poorly: the light-mode
"blue" theme puts #DCE4EE text on a #3B8ED0 button (2.7:1), unselected tab
labels are #DCE4EE on #979DA2 (2.1:1), and the "gold" and "green" themes put
near-white text on bright fills. This module holds

  * the WCAG 2.x contrast-ratio math (relative luminance, as used by
    https://www.w3.org/TR/WCAG21/#dfn-contrast-ratio);
  * `improve_theme_contrast()`, which rewrites a CustomTkinter theme dict in
    place so every text/background pair that matters reaches the minimum
    ratio; and
  * the few palette colors the tabs and dialogs share (secondary text,
    warning text) so no module hard-codes a gray that is too faint.

Everything here works on color *strings* ("#rrggbb", "grayNN", "white",
"black") and needs no Tk display, so the whole module is unit-testable
headless. Colors it cannot parse (such as "transparent") are left alone.
"""

import re

# WCAG's AA threshold for normal-size text.
MIN_TEXT_CONTRAST = 4.5
# Disabled controls are exempt from WCAG, but text that is barely visible
# is still a usability problem; this keeps it legible without making it
# look enabled.
MIN_DISABLED_CONTRAST = 3.0

# How far a background may be darkened/lightened (0..1, a fraction of the
# way to black/white) to keep a theme's own text polarity before it is
# cheaper to flip the text from light to dark (or back) instead. White on
# the blue theme's #3B8ED0 needs about 15%; white on the green theme's
# #2CC985 would need about 45%, so green gets dark text.
MAX_BACKGROUND_SHIFT = 0.2

# -- Shared palette --------------------------------------------------------
# (light mode, dark mode). Each is checked against every background it is
# used on by tests/test_contrast.py, so changing one here that no longer
# reaches MIN_TEXT_CONTRAST fails the suite.

# Hints, captions and anything that is meant to look secondary.
SECONDARY_TEXT = ("gray35", "gray70")

# Warnings ("restart required", "modified", port problems).
WARNING_TEXT = ("#8a4b00", "#e8963a")

_GRAY = re.compile(r"^gr[ae]y(\d{1,3})$")
_HEX = re.compile(r"^#([0-9a-fA-F]{6})$")
_NAMED = {"white": (255, 255, 255), "black": (0, 0, 0)}


def parse_color(color):
    """Returns `color` as an (r, g, b) tuple of 0-255 ints, or None if it
    is not one of the forms this module understands."""
    if not isinstance(color, str):
        return None
    text = color.strip().lower()
    if text in _NAMED:
        return _NAMED[text]
    match = _HEX.match(text)
    if match:
        value = match.group(1)
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))
    match = _GRAY.match(text)
    if match and int(match.group(1)) <= 100:
        # Tk's grayNN is NN percent of 255, rounded.
        level = int(int(match.group(1)) * 255 / 100 + 0.5)
        return (level, level, level)
    return None


def to_hex(rgb) -> str:
    """The inverse of parse_color() for hex output: (r, g, b) -> "#rrggbb"."""
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(round(c)))) for c in rgb))


def luminance(rgb) -> float:
    """WCAG relative luminance, 0 (black) to 1 (white)."""

    def channel(value):
        v = value / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(color_a, color_b) -> float:
    """WCAG contrast ratio (1 to 21) between two colors, given as strings
    or (r, g, b) tuples. Raises ValueError for a string it cannot parse."""
    values = []
    for color in (color_a, color_b):
        rgb = parse_color(color) if isinstance(color, str) else tuple(color)
        if rgb is None:
            raise ValueError(f"Unrecognized color {color!r}")
        values.append(luminance(rgb))
    lighter, darker = max(values), min(values)
    return (lighter + 0.05) / (darker + 0.05)


def resolve(color, mode: str):
    """Picks the light or dark half of a CustomTkinter (light, dark) color
    pair; a plain string is returned as is. `mode` is "Light" or "Dark"."""
    if isinstance(color, (tuple, list)):
        return color[1] if mode == "Dark" else color[0]
    return color


def _blend(rgb, target, amount):
    return tuple(c + (t - c) * amount for c, t in zip(rgb, target))


def _fit_background(text_rgb, bg_rgb, minimum):
    """Moves `bg_rgb` away from `text_rgb` (darker if the text is lighter,
    lighter if the text is darker) in 1% steps until the ratio reaches
    `minimum`. Returns (new_rgb, shift) where shift is the fraction of the
    way to black/white that was needed; stops at black/white if `minimum`
    is unreachable."""
    target = (0, 0, 0) if luminance(text_rgb) > luminance(bg_rgb) else (255, 255, 255)
    for step in range(101):
        shift = step / 100
        candidate = _blend(bg_rgb, target, shift)
        if contrast_ratio(text_rgb, tuple(int(round(c)) for c in candidate)) >= minimum:
            return tuple(int(round(c)) for c in candidate), shift
    return target, 1.0


def adjust_background(text, bg, minimum=MIN_TEXT_CONTRAST) -> str:
    """Returns `bg`, darkened or lightened just enough that `text` reaches
    `minimum` on it. Unchanged (same string) if it already does or if
    either color cannot be parsed."""
    text_rgb, bg_rgb = parse_color(text), parse_color(bg)
    if text_rgb is None or bg_rgb is None:
        return bg
    if contrast_ratio(text_rgb, bg_rgb) >= minimum:
        return bg
    return to_hex(_fit_background(text_rgb, bg_rgb, minimum)[0])


def adjust_text(text, bg, minimum=MIN_TEXT_CONTRAST) -> str:
    """Returns `text`, moved just far enough toward black or white to reach
    `minimum` on `bg`. It prefers the direction that increases the contrast
    it already has, and goes the other way only when that direction can't
    get there (light text on a bright fill). Unchanged if it already
    reaches `minimum` or either color can't be parsed."""
    text_rgb, bg_rgb = parse_color(text), parse_color(bg)
    if text_rgb is None or bg_rgb is None:
        return text
    if contrast_ratio(text_rgb, bg_rgb) >= minimum:
        return text
    away = (0, 0, 0) if luminance(bg_rgb) > luminance(text_rgb) else (255, 255, 255)
    other = (255, 255, 255) if away == (0, 0, 0) else (0, 0, 0)
    for target in (away, other):
        for step in range(101):
            candidate = tuple(int(round(c)) for c in _blend(text_rgb, target, step / 100))
            if contrast_ratio(candidate, bg_rgb) >= minimum:
                return to_hex(candidate)
    return to_hex(away)


def improve_pair(text, bg, minimum=MIN_TEXT_CONTRAST):
    """Returns (text, bg) adjusted so the pair reaches `minimum`.

    A pair that already passes is returned untouched. Otherwise the text
    keeps its polarity (light stays light) and becomes pure white/black,
    and the background moves only as far as needed -- unless that would
    take more than MAX_BACKGROUND_SHIFT, in which case flipping the text
    to the opposite extreme changes the background less and wins."""
    text_rgb, bg_rgb = parse_color(text), parse_color(bg)
    if text_rgb is None or bg_rgb is None:
        return text, bg
    if contrast_ratio(text_rgb, bg_rgb) >= minimum:
        return text, bg
    same = (255, 255, 255) if luminance(text_rgb) > luminance(bg_rgb) else (0, 0, 0)
    opposite = (0, 0, 0) if same == (255, 255, 255) else (255, 255, 255)
    same_bg, same_shift = _fit_background(same, bg_rgb, minimum)
    if same_shift <= MAX_BACKGROUND_SHIFT:
        return to_hex(same), to_hex(same_bg)
    opposite_bg, opposite_shift = _fit_background(opposite, bg_rgb, minimum)
    if opposite_shift < same_shift:
        return to_hex(opposite), to_hex(opposite_bg)
    return to_hex(same), to_hex(same_bg)


# (widget, text key, primary backgrounds, secondary backgrounds,
#  quieter-text key, minimum for that quieter text)
#
# The first primary background decides the text color via improve_pair();
# the other primaries and the secondaries (hover states) then only have
# their backgrounds adjusted, so one text color serves all of them.
_THEME_GROUPS = (
    ("CTkButton", "text_color", ("fg_color",), ("hover_color",),
     "text_color_disabled", MIN_DISABLED_CONTRAST),
    ("CTkOptionMenu", "text_color", ("fg_color",), (),
     "text_color_disabled", MIN_DISABLED_CONTRAST),
    ("CTkComboBox", "text_color", ("fg_color",), (),
     "text_color_disabled", MIN_DISABLED_CONTRAST),
    ("CTkSegmentedButton", "text_color", ("selected_color", "unselected_color"),
     ("selected_hover_color", "unselected_hover_color"),
     "text_color_disabled", MIN_DISABLED_CONTRAST),
    ("CTkEntry", "text_color", ("fg_color",), (),
     "placeholder_text_color", MIN_TEXT_CONTRAST),
    ("CTkTextbox", "text_color", ("fg_color",), (), None, None),
    ("DropdownMenu", "text_color", ("fg_color",), ("hover_color",), None, None),
)

# Hover/pressed colors that must stay darker than the color they hover
# over once the base has been darkened: (widget, base key, derived key).
_KEEP_DARKER = (
    ("CTkButton", "fg_color", "hover_color"),
    ("CTkOptionMenu", "fg_color", "button_color"),
    ("CTkOptionMenu", "button_color", "button_hover_color"),
    ("CTkSegmentedButton", "selected_color", "selected_hover_color"),
)


def _pair(value):
    """A theme color as a [light, dark] list (a lone string applies to
    both modes)."""
    if isinstance(value, (tuple, list)):
        return list(value)
    return [value, value]


def _store(entry, key, original, light_dark):
    """Writes [light, dark] back, keeping a lone string a lone string."""
    if isinstance(original, (tuple, list)):
        entry[key] = list(light_dark)
    elif light_dark[0] == light_dark[1]:
        entry[key] = light_dark[0]
    else:
        entry[key] = list(light_dark)


def improve_theme_contrast(theme: dict, minimum=MIN_TEXT_CONTRAST) -> None:
    """Rewrites the CustomTkinter theme dict (`ctk.ThemeManager.theme`) in
    place so the text on its buttons, menus, tabs and text fields reaches
    `minimum` in both appearance modes. Call it after the theme is loaded
    and before any widget is built, the same constraint as the font
    override in gui/app.py. Safe to call repeatedly."""
    for widget, text_key, primaries, secondaries, quiet_key, quiet_min in _THEME_GROUPS:
        entry = theme.get(widget)
        if not entry or text_key not in entry:
            continue
        originals = {
            key: entry[key]
            for key in (text_key, quiet_key, *primaries, *secondaries)
            if key and key in entry
        }
        texts = _pair(entry[text_key])
        values = {key: _pair(entry[key]) for key in originals if key != text_key}
        for mode in (0, 1):
            first = True
            for key in primaries:
                if key not in values:
                    continue
                if first:
                    texts[mode], values[key][mode] = improve_pair(
                        texts[mode], values[key][mode], minimum
                    )
                    first = False
                else:
                    values[key][mode] = adjust_background(
                        texts[mode], values[key][mode], minimum
                    )
            for key in secondaries:
                if key in values:
                    values[key][mode] = adjust_background(
                        texts[mode], values[key][mode], minimum
                    )
            for widget_name, base, derived in _KEEP_DARKER:
                if widget_name != widget or base not in values or derived not in values:
                    continue
                base_rgb = parse_color(values[base][mode])
                derived_rgb = parse_color(values[derived][mode])
                text_rgb = parse_color(texts[mode])
                if None in (base_rgb, derived_rgb, text_rgb):
                    continue
                light_text = luminance(text_rgb) > luminance(base_rgb)
                if light_text and luminance(derived_rgb) >= luminance(base_rgb):
                    values[derived][mode] = to_hex(_blend(base_rgb, (0, 0, 0), 0.2))
            if quiet_key in values and primaries[0] in values:
                values[quiet_key][mode] = adjust_text(
                    values[quiet_key][mode], values[primaries[0]][mode], quiet_min
                )
        _store(entry, text_key, originals[text_key], texts)
        for key, new_value in values.items():
            _store(entry, key, originals[key], new_value)
