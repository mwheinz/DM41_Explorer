"""
Shared layout helpers so the tab modules (Flags, Data Registers, XM Files,
...) don't each hand-roll a slightly different header row and drift apart
visually over time.

Originally just build_tab_header()/stripe_bg_color(); GitHub issue #25
added the ttk.Treeview theming/construction/selection helpers below after
a pylint duplicate-code (R0801) pass found gui/data_registers_tab.py,
gui/hex_view_tab.py, gui/program_tab.py, and gui/xm_files_tab.py had each
grown their own near-identical copy of that machinery, and
gui/overview_tab.py/gui/flags_tab.py/gui/key_assignments_tab.py had each
redefined the same CARD_FG/CARD_BORDER colors. A shared base class was
considered instead of extending this module and rejected: every one of
those tab classes already sits at or past pylint's own R0901 "too many
ancestors" ceiling (CTkFrame's own inheritance depth already accounts for
most of it), so inserting a shared base above them would trade one pylint
warning for a worse one, and the four Treeview tabs differ in enough real
ways (Data Registers' two side-by-side tables, XM Files' selection-driven
button enabling, Hex View's region-colored rows instead of plain zebra
striping) that a template-method hierarchy would need about as many
override hooks as the duplication it removed. Plain functions, called
explicitly, keep each tab's own __init__()/render() reading as its real
control flow.
"""

import logging
import tkinter
import tkinter.font as tkfont
from pathlib import Path
from tkinter import ttk, filedialog, messagebox
import customtkinter as ctk

from gui.contrast import SECONDARY_TEXT
from gui.scroll_support import SmoothTreeview
from gui.window_geometry import scaled_row_height, scaled_width

logger = logging.getLogger(__name__)

# Font family used for any field that displays or edits raw register/file
# *content* (hex bytes, addresses, decoded numbers, alpha text) -- as
# opposed to labels, names, or descriptive prose. A fixed-width font keeps
# these fields' columns aligned and makes it easy to eyeball hex digit
# counts. Centralized here so every tab/dialog that shows register content
# uses the same family instead of each hard-coding "Courier" separately.
MONOSPACE_FONT_FAMILY = "Courier"

# The smallest font size ui_font() will return, so a hint label never
# becomes unreadable however small the main font is set.
MIN_UI_FONT_SIZE = 8


def ui_font(offset: int = 0, **kwargs) -> "ctk.CTkFont":
    """A CTkFont whose size follows the application font (Preferences >
    Font): the configured size plus `offset` points, never below
    MIN_UI_FONT_SIZE. Use it instead of a hard-coded `size=` so captions
    and the status bar scale with the rest of the UI (GitHub issue #42).
    Extra keyword arguments (weight=, family=) pass through to CTkFont."""
    base = ctk.ThemeManager.theme["CTkFont"]["size"]
    return ctk.CTkFont(size=max(MIN_UI_FONT_SIZE, base + offset), **kwargs)


# Alternating-row background shade, shared by every tab with a table (Data
# Registers' ttk.Treeview, and the XM Files/Programs tabs' CTkScrollableFrame
# grids) so odd rows get the exact same subtle tint everywhere instead of
# each tab inventing its own slightly different color. Values match what
# data_registers_tab.py used before this was centralized here.
STRIPE_BG_DARK = "#2b2b2b"
STRIPE_BG_LIGHT = "#f4f4f4"

# Text and field colors of every ttk.Treeview (light, dark) -- see
# style_treeview(). Constants so tests/test_contrast.py can check them
# against the stripe and region backgrounds (GitHub issue #42).
TREEVIEW_FG = ("#1a1a1a", "#e6e6e6")
TREEVIEW_FIELD_BG = ("#ffffff", "#242424")

# The red Remove/Delete button shared by the Programs, Alarms and XM Files
# tabs. The text colors are explicit because the theme's own text color is
# black in the "gold" and "green" themes, which fails on this fill
# (tests/test_contrast.py checks every theme).
DANGER_BUTTON_KWARGS = {
    "fg_color": "#a03e3e",
    "hover_color": "#832f2f",
    "text_color": "#ffffff",
    "text_color_disabled": "#e6c8c8",
}

# Selected-row highlight for a ttk.Treeview -- see highlight_selected_row()
# below and gui/data_registers_tab.py's _on_tree_selected() docstring
# (GitHub issue #22) for why this is applied by hand via a per-item tag
# rather than relying solely on ttk's own "selected" state map. Formerly a
# separate identical copy in each of gui/data_registers_tab.py,
# gui/program_tab.py, and gui/xm_files_tab.py.
SELECTED_ROW_BG = "#1f6aa5"
SELECTED_ROW_FG = "#ffffff"

# Bordered/tinted "card" look shared by gui/overview_tab.py's summary
# cards, gui/flags_tab.py's flag grid, and gui/key_assignments_tab.py's
# default (unassigned) key cells -- formerly three identical copies.
CARD_FG = ("gray92", "gray17")
CARD_BORDER = ("gray80", "gray28")

# gui/overview_tab.py's cards and gui/flags_tab.py's flag grid both build
# their outer frame with this exact set of kwargs (border_width=1,
# corner_radius=10 alongside the colors above) -- unpack with
# `**CARD_KWARGS` rather than repeating the four keyword arguments at
# each call site. gui/key_assignments_tab.py's key cells use CARD_FG/
# CARD_BORDER directly instead, since their corner_radius (6, to suit a
# much smaller cell) differs from this shared default.
CARD_KWARGS = {
    "fg_color": CARD_FG,
    "border_width": 1,
    "border_color": CARD_BORDER,
    "corner_radius": 10,
}


def stripe_bg_color() -> str:
    """The alternating-row background color for the current appearance
    mode (dark vs. light)."""
    return STRIPE_BG_DARK if ctk.get_appearance_mode() == "Dark" else STRIPE_BG_LIGHT


def treeview_fonts():
    """(cell font, heading font) as Tk font tuples -- the monospace
    family at the application font size for cells, the UI family in bold
    for headings. The one place both style_treeview() and fit_columns()
    get them from, so what is measured is what is drawn."""
    theme_font = ctk.ThemeManager.theme["CTkFont"]
    return (
        (MONOSPACE_FONT_FAMILY, theme_font["size"]),
        (theme_font["family"], theme_font["size"], "bold"),
    )


def style_treeview(style_name: str = "Treeview", *, selectable: bool = True) -> str:
    """Applies the shared dark/light ttk.Treeview theming -- field/heading
    colors, the app's shared monospace font (see MONOSPACE_FONT_FAMILY),
    fixed row height -- under `style_name` (and f"{style_name}.Heading"),
    and returns the current stripe background color for the caller to use
    with `tree.tag_configure("oddrow", background=...)` (see
    apply_row_tags() below, which does exactly that).

    `style_name` defaults to ttk's own built-in "Treeview" style, which is
    what to pass for a tab that owns the only Treeview on screen at once
    (Data Registers, Hex View); pass a distinct name (e.g.
    "Programs.Treeview") for a tab that must not have its font/colors
    silently overwritten by -- or itself overwrite -- another tab's
    Treeview style, since ttk style names are process-global.

    `selectable=True` (the default) also maps ttk's own "selected" state
    to SELECTED_ROW_BG, as a harmless fallback -- see
    gui/data_registers_tab.py's `_on_tree_selected()` docstring (GitHub
    issue #22) for why the highlight that's actually visible comes from a
    hand-managed "selectedrow" tag instead (a per-item tag's background
    silently overrides this state map regardless of what it says, a
    long-standing Tk behavior). Pass `selectable=False` for a tab whose
    Treeview has `selectmode="none"` (Hex View, which colors rows by
    memory region instead) -- there, an explicitly empty `style.map(...)`
    call is what suppresses ttk's own selection highlight, which a stray
    click could otherwise still show on a tab that isn't interactive.
    """
    style = ttk.Style()
    try:
        style.theme_use("default")
    except Exception as e:
        # "default" ships with every Tcl/Tk this project supports; this is
        # a defensive fallback, not something expected to fire.
        logger.debug("Could not switch ttk theme to 'default': %s", e)
    dark = ctk.get_appearance_mode() == "Dark"
    bg = stripe_bg_color()
    field_bg = TREEVIEW_FIELD_BG[1 if dark else 0]
    fg = TREEVIEW_FG[1 if dark else 0]
    theme_font = ctk.ThemeManager.theme["CTkFont"]
    font, heading_font = treeview_fonts()
    style.configure(
        style_name,
        background=field_bg,
        fieldbackground=field_bg,
        foreground=fg,
        rowheight=scaled_row_height(theme_font["size"]),
        borderwidth=0,
        font=font,
    )
    style.configure(
        f"{style_name}.Heading", background=bg, foreground=fg, font=heading_font
    )
    if selectable:
        style.map(style_name, background=[("selected", SELECTED_ROW_BG)])
    else:
        # selectmode="none" already disables selection functionally, but
        # without this a stray click can still leave ttk's own focus/
        # active-row indicator visible -- see Hex View's own render() for
        # why that would look like a broken "clickable" affordance there.
        style.map(style_name, background=[], foreground=[])
    return bg


def build_tree_with_scrollbar(
    parent, columns: list, *, selectmode: str = "browse", style: str = None
):
    """Builds a ttk.Treeview plus the vertical tkinter.Scrollbar wired to
    it, both packed side-by-side filling `parent` -- the construction
    every Treeview-based tab here repeats (GitHub issues #21/#22's
    native-table performance fix; see e.g. gui/data_registers_tab.py's
    module docstring for why these tabs use ttk.Treeview instead of one
    CustomTkinter widget per row/cell at all).

    `columns` is a list of (column_id, heading_text, width, stretch)
    tuples, applied in order via `tree.heading()`/`tree.column()`. Widths
    are for the default font size and grow in proportion with a larger
    application font (GitHub issue #42).
    `style`, if given, is passed through as the Treeview's ttk style name
    (see style_treeview() above) -- omit it to use ttk's built-in
    "Treeview" style.

    Returns (tree, scrollbar). Row tag colors ("oddrow"/"selectedrow") are
    NOT applied here -- see apply_row_tags() below -- since callers vary
    in exactly when they have a stripe color ready to apply.

    The tree is a gui/scroll_support.py SmoothTreeview rather than a bare
    ttk.Treeview: a ttk.Treeview in every other respect (it only adds its
    own <MouseWheel>/<TouchpadScroll> bindings, and only on macOS), so
    that every table in the app gets the GitHub issue #29 scroll fix from
    this one construction site.
    """
    kwargs = {"show": "headings", "selectmode": selectmode}
    if style:
        kwargs["style"] = style
    tree = SmoothTreeview(parent, columns=[col[0] for col in columns], **kwargs)
    font_size = ctk.ThemeManager.theme["CTkFont"]["size"]
    for col, text, width, stretch in columns:
        tree.heading(col, text=text)
        tree.column(col, width=scaled_width(width, font_size), anchor="w", stretch=stretch)

    vsb = tkinter.Scrollbar(parent, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.pack(side="left", fill="both", expand=True)
    vsb.pack(side="left", fill="y")
    return tree, vsb


# Pixels added to the widest text in a column: the Treeview's own cell
# padding, and the room a heading keeps for its sort arrow and borders.
CELL_PADDING = 14
HEADING_PADDING = 24
# Only this many rows are measured; the first few hundred are
# representative and measuring thousands would slow every render.
FIT_ROW_LIMIT = 500


def fit_columns(tree: ttk.Treeview, columns: list) -> None:
    """Widens each non-stretching column of `tree` so its heading and the
    text of its rows are not truncated, measured in the fonts actually
    used (treeview_fonts()) rather than guessed from the font size.

    `columns` is the same (column_id, heading_text, width, stretch) list
    given to build_tree_with_scrollbar(). A column is never narrower than
    its scaled base width, and stretching columns are left alone -- they
    take whatever space remains. Call it after populating the rows.

    Fixed pixel widths (even scaled with the font) turned out not to be
    enough: the Alarms tab's 150-pixel time column truncated a full
    "YYYY-MM-DD HH:MM:SS" even at the default font size."""
    cell_spec, heading_spec = treeview_fonts()
    cell_font = tkfont.Font(font=cell_spec)
    heading_font = tkfont.Font(font=heading_spec)
    font_size = ctk.ThemeManager.theme["CTkFont"]["size"]
    ids = [col[0] for col in columns]
    widest = {
        col[0]: heading_font.measure(col[1]) + HEADING_PADDING for col in columns
    }
    for iid in tree.get_children()[:FIT_ROW_LIMIT]:
        for column_id, value in zip(ids, tree.item(iid, "values")):
            needed = cell_font.measure(str(value)) + CELL_PADDING
            if needed > widest[column_id]:
                widest[column_id] = needed
    for column_id, _text, width, stretch in columns:
        if not stretch:
            tree.column(column_id, width=max(scaled_width(width, font_size), widest[column_id]))


def build_tab_treeview(
    master, columns: list, *, style: str = "Treeview", selectmode: str = "browse"
):
    """Convenience wrapper for the common case of a tab with exactly one
    ttk.Treeview inside its own transparent "table_frame" --
    gui/program_tab.py and gui/xm_files_tab.py, which otherwise ended up
    with an identical construction sequence (table_frame, style_treeview(),
    build_tree_with_scrollbar(), apply_row_tags()) still long enough to
    trip pylint's own duplicate-code (R0801) check against each other,
    even after each individual piece was pulled out separately above.

    Builds and packs the table_frame itself (the same
    `fill="both", expand=True, padx=8, pady=(0, 8)` every Treeview-based
    tab uses), applies `style_treeview()`, builds the Treeview + scrollbar
    via `build_tree_with_scrollbar()`, and applies the oddrow/selectedrow
    tag colors via `apply_row_tags()` -- all in one call. Returns
    (table_frame, tree).

    NOT used by gui/data_registers_tab.py (two side-by-side trees sharing
    one stripe color, so the tag colors have to be applied to both at
    once rather than per-tree) or gui/hex_view_tab.py (`selectmode="none"`,
    no oddrow/selectedrow tags at all -- it colors rows by memory region
    instead) -- both build their table_frame and call the lower-level
    pieces directly since this wrapper's one-tree-with-tags shape doesn't
    fit either of them."""
    table_frame = ctk.CTkFrame(master, fg_color="transparent")
    table_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
    stripe_bg = style_treeview(style)
    tree, _ = build_tree_with_scrollbar(
        table_frame, columns, style=style, selectmode=selectmode
    )
    apply_row_tags(tree, stripe_bg)
    return table_frame, tree


def read_text_file_via_dialog(context: str, logger_obj: logging.Logger):
    """Prompts for a .txt file via a standard "Text files"/"All files"
    open dialog and reads it as ASCII text. Returns (path, content), or
    (None, None) if the user cancelled the dialog or the read failed --
    in the failure case, an error is shown via messagebox and logged as
    `logger_obj.warning("Could not read %s for {context}: %s", path, e)`.

    Shared by gui/data_registers_tab.py's `_import_registers()` and
    gui/xm_files_tab.py's `_import_file()`, which differ only in what
    `context` describes (e.g. "register import" vs. "XM file import")
    and what they do with the content afterward -- both need the
    *caller's* logger (not one local to this module) so the resulting
    log line is attributed to the right tab."""
    path = filedialog.askopenfilename(
        filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
    )
    if not path:
        return None, None
    try:
        content = Path(path).read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as e:
        logger_obj.warning("Could not read %s for %s: %s", path, context, e)
        messagebox.showerror("Could Not Read File", str(e))
        return None, None
    return path, content


def clear_tree_for_render(tree: ttk.Treeview, header_label, memory) -> bool:
    """The common render() prologue for a single-Treeview tab (Hex View,
    Programs, XM Files): clears every existing row, and -- if `memory`
    is None -- sets the tab's header to the shared "no memory loaded"
    text. Returns True when the caller should return immediately
    (`memory` was None), False when render() should continue populating
    the table.

    NOT used by gui/data_registers_tab.py (has to clear two side-by-side
    trees, not one) -- see that tab's own render() for why."""
    tree.delete(*tree.get_children())
    if memory is None:
        header_label.configure(text="(no memory state loaded)")
        return True
    return False


def apply_row_tags(trees, stripe_bg: str) -> None:
    """Configures the "oddrow"/"selectedrow" tag colors (see
    style_treeview()/highlight_selected_row()) on one or more
    ttk.Treeviews. `trees` may be a single Treeview or an iterable of them
    -- gui/data_registers_tab.py applies the same colors to its two
    side-by-side tables at once. Called both at construction and from
    each tab's own `refresh_theme()`."""
    if isinstance(trees, ttk.Treeview):
        trees = (trees,)
    for tree in trees:
        tree.tag_configure("oddrow", background=stripe_bg)
        tree.tag_configure(
            "selectedrow", background=SELECTED_ROW_BG, foreground=SELECTED_ROW_FG
        )


def clear_selected_row_tag(tree: ttk.Treeview) -> None:
    """Restores "oddrow"/no tag -- recomputed from each row's live
    position, not cached -- on whichever row in `tree` currently carries
    the "selectedrow" tag, if any. The first half of the GitHub issue #22
    highlight fix (see highlight_selected_row() below), factored out
    separately so gui/data_registers_tab.py's own `_on_tree_selected()`
    -- which has to do this across two side-by-side tables before
    re-tagging just one of them -- can reuse it too."""
    for pos, iid in enumerate(tree.get_children()):
        if "selectedrow" in tree.item(iid, "tags"):
            tree.item(iid, tags=("oddrow",) if pos % 2 else ())


def highlight_selected_row(tree: ttk.Treeview) -> None:
    """Gives `tree`'s currently-selected row a visible highlight --
    GitHub issue #22.

    ttk.Treeview has a built-in "selected" state background (set via
    `style.map()` in style_treeview() above), but a per-item tag's
    background overrides it regardless of selection state, which is why
    that alone never actually shows: every row here carries either the
    "oddrow" zebra-stripe tag or the default (untagged) style, and the
    tag always wins. The fix is NOT to give "selectedrow" priority over
    "oddrow" by listing it first either -- *which* tag wins when two tags
    on the same item both set a background is itself inconsistent across
    Tk builds (reliably "selectedrow" on this project's Linux/Xvfb dev
    environment, but reliably "oddrow" -- grey background, barely
    readable white text -- on a real Mac). So this never lets the two
    compete in the first place: while a row is selected it carries
    *only* the "selectedrow" tag (via clear_selected_row_tag() then this
    tag), so it doesn't matter which of those two agreeing sources wins.
    """
    clear_selected_row_tag(tree)
    selection = tree.selection()
    if selection:
        tree.item(selection[0], tags=("selectedrow",))


def build_caption_label(master, text: str) -> ctk.CTkLabel:
    """A small explanatory caption placed just under a tab's header --
    the shared look (12pt, gray, left-justified, wrapped at 900px)
    gui/key_assignments_tab.py and gui/program_tab.py each use for their
    own one-paragraph usage notes. Packed into `master` with the same
    padding both already used. Returns the label in case a caller wants
    to update its text later."""
    label = ctk.CTkLabel(
        master,
        text=text,
        font=ui_font(-1),
        text_color=SECONDARY_TEXT,
        anchor="w",
        justify="left",
        wraplength=900,
    )
    label.pack(fill="x", padx=8, pady=(0, 4))
    return label


def build_tab_header(master, button_kwargs: dict = None):
    """Builds the standard fixed tab header: a bold status label on the
    left (defaulting to the shared "no memory loaded" text every tab
    shows before a state is loaded) and, optionally, a single primary
    action button on the right.

    Packed into `master` with the same padding every tab uses, so the
    header stays put at the top of the tab while whatever scrollable
    body sits below it does the scrolling.

    `button_kwargs`, if given, is passed straight through to CTkButton
    (e.g. {"text": "Add File...", "width": 100, "command": ...}).

    Returns (header_frame, status_label).
    """
    header = ctk.CTkFrame(master, fg_color="transparent")
    header.pack(fill="x", padx=8, pady=8)

    label = ctk.CTkLabel(
        header, text="(no memory state loaded)", font=ctk.CTkFont(weight="bold")
    )
    label.pack(side="left")

    if button_kwargs:
        ctk.CTkButton(header, **button_kwargs).pack(side="right")

    return header, label
