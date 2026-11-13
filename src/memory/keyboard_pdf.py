"""
A printable PDF diagram of the calculator's key assignments (GitHub issue
#76): the keyboard of the state's model, each key showing its unshifted
assignment on the key face and its shifted one on a gold strip above it,
then, on a page of its own, a list of every assignment.

Written with reportlab. Only the PDF standard fonts are used (nothing is
embedded): Helvetica for text, and Symbol for the few HP-41 glyphs
Helvetica's WinAnsi encoding lacks (Σ, ↑, ≠, ...). A character neither
font has is printed as its hp41uc trigraph (memory/trigraphs.py), so a
name never loses information.

Nothing here needs Tk; the Key Assignments tab calls
write_key_assignments_pdf().
"""

import datetime
import io
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple, Union

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape, letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from .keyboard import (
    GRID_COLUMNS,
    PROGRAM,
    KeyLabel,
    key_labels,
    layout_for_profile,
    layout_placements,
)
from .trigraphs import unicode_to_trigraphs

PAGE_SIZES = {"letter": letter, "a4": A4}
# The title of a state with no name (one read from a calculator, say).
UNTITLED = "Key assignments"
DEFAULT_PAGE_SIZE = "letter"

MARGIN = 36  # points (half an inch)

TEXT_FONT = "Helvetica"
BOLD_FONT = "Helvetica-Bold"
SYMBOL_FONT = "Symbol"

# Characters printed in the Symbol font, and the Symbol character each
# one becomes (∡ has no Symbol glyph; ∠ is the closest).
_SYMBOL_CHARS = {
    "Σ": "Σ",
    "↑": "↑",
    "↓": "↓",
    "≠": "≠",
    "∡": "∠",
}
# Emoji/text presentation selectors on the arrow keys' labels ("⬆︎").
_DROPPED_CHARS = {"︎", "️"}
# Fixed-key labels the standard fonts can't draw, and what to print.
_STATIC_LABEL_TEXT = {"⬆︎": "↑", "⬇︎": "↓"}

INK = colors.black
FAINT_INK = colors.HexColor("#6b6b6b")
KEY_BORDER = colors.HexColor("#4d4d4d")
KEY_FACE = colors.white
SHIFT_STRIP = colors.HexColor("#ffe27a")
FIXED_KEY_FILL = {
    "SHIFT": colors.HexColor("#ffd700"),
    "ALPHA": colors.HexColor("#add8e6"),
}
FIXED_KEY_DEFAULT_FILL = colors.HexColor("#d9d9d9")

# Font sizes (points). A name that doesn't fit its key at NAME_SIZE is
# shrunk, down to MIN_NAME_SIZE.
TITLE_SIZE = 16
SUBTITLE_SIZE = 9
NAME_SIZE = 11
MIN_NAME_SIZE = 5
KEY_NUMBER_SIZE = 6
LEGEND_SIZE = 8
TABLE_SIZE = 9
TABLE_LINE = 13
# The assignment list is set in blocks of three columns side by side.
TABLE_KEY_WIDTH = 28
TABLE_NAME_WIDTH = 72
TABLE_BLOCK_WIDTH = 175
FLAG_CLEAR_NOTE = "flag clear"
TABLE_BLOCK_GAP = 16

# Proportions of the keyboard: the gap between keys, and the shifted
# strip's share of a one-row key's height.
KEY_GAP = 5
STRIP_SHARE = 0.4
CORNER_RADIUS = 4
# The tallest a keyboard row may be, so a keyboard with few rows isn't
# stretched; the DM41L's 10-key rows are drawn on a landscape page.
MAX_ROW_HEIGHT = 58
LANDSCAPE_ROW_LENGTH = 7


def write_key_assignments_pdf(memory, path: Union[str, Path], **kwargs):
    """Writes key_assignments_pdf(memory, **kwargs) to `path`. Raises
    OSError if the file can't be written."""
    Path(path).write_bytes(key_assignments_pdf(memory, **kwargs))


def key_assignments_pdf(
    memory,
    *,
    name: str = "",
    date: Optional[datetime.date] = None,
    page_size: str = DEFAULT_PAGE_SIZE,
) -> bytes:
    """The PDF diagram of `memory`'s key assignments, as bytes.

    `name` is the state's name (its file name without the extension,
    say), the page title; a state with no name gets UNTITLED.
    Below the title, in smaller print, come the model, `date` (default:
    today) and the number of assignments. `page_size` is a key of
    PAGE_SIZES. The output is the same bytes every time for the same
    arguments."""
    if page_size not in PAGE_SIZES:
        raise ValueError(f"Unknown page size {page_size!r}")
    layout = layout_for_profile(memory.profile)
    labels = key_labels(memory, layout)
    size = PAGE_SIZES[page_size]
    if max(len(row) for row in layout) >= LANDSCAPE_ROW_LENGTH:
        size = landscape(size)

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=size, invariant=1)
    model = memory.profile.name if memory.profile else "HP-41"
    heading = name or UNTITLED
    pdf.setTitle(f"{name} key assignments" if name else UNTITLED)
    pdf.setCreator("DM41_Explorer")

    page = _Page(pdf, size)
    when = (date or datetime.date.today()).isoformat()
    count = len(labels)
    assignments = f"{count} assignment{'' if count == 1 else 's'}"
    subtitle = "  ·  ".join((model, when, assignments))
    page.heading(heading, subtitle)
    _draw_keyboard(page, layout, labels)
    _draw_legend(page, labels)
    page.new_page(heading, subtitle)
    _draw_table(page, labels, heading)
    pdf.save()
    return buffer.getvalue()


class _Page:
    """The canvas plus a cursor `y` moving down the page from the top
    margin, so each section starts below the last and the table can
    break onto a new page."""

    def __init__(self, pdf, size):
        self.pdf = pdf
        self.width, self.height = size
        self.left = MARGIN
        self.right = self.width - MARGIN
        self.bottom = MARGIN
        self.y = self.height - MARGIN

    @property
    def text_width(self) -> float:
        return self.right - self.left

    def heading(self, title: str, subtitle: str):
        self.y -= TITLE_SIZE
        draw_text(self.pdf, title, self.left, self.y, TITLE_SIZE, bold=True)
        self.y -= SUBTITLE_SIZE + 6
        self.pdf.setFillColor(FAINT_INK)
        draw_text(self.pdf, subtitle, self.left, self.y, SUBTITLE_SIZE)
        self.pdf.setFillColor(INK)
        self.y -= 16

    def ensure_room(self, height: float, continued_heading: str) -> bool:
        """Starts a new page (with a "continued" heading) unless `height`
        points fit above the bottom margin. True if a page was started."""
        if self.y - height >= self.bottom:
            return False
        self.new_page(continued_heading, "continued")
        return True

    def new_page(self, title: str, subtitle: str):
        """Ends this page and starts the next with a heading."""
        self.pdf.showPage()
        self.y = self.height - MARGIN
        self.heading(title, subtitle)


# -- Text -------------------------------------------------------------------


def text_runs(text: str, bold: bool = False) -> List[Tuple[str, str]]:
    """Splits `text` into (font, text) runs the standard fonts can draw:
    Helvetica for whatever WinAnsi encodes, Symbol for _SYMBOL_CHARS,
    and the hp41uc trigraph (in Helvetica) for anything else."""
    base = BOLD_FONT if bold else TEXT_FONT
    runs: List[Tuple[str, str]] = []
    for ch in text:
        if ch in _DROPPED_CHARS:
            continue
        if ch in _SYMBOL_CHARS:
            font, piece = SYMBOL_FONT, _SYMBOL_CHARS[ch]
        elif _in_winansi(ch):
            font, piece = base, ch
        else:
            font, piece = base, _fallback(ch)
        if runs and runs[-1][0] == font:
            runs[-1] = (font, runs[-1][1] + piece)
        else:
            runs.append((font, piece))
    return runs


def _in_winansi(ch: str) -> bool:
    try:
        ch.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return True


def _fallback(ch: str) -> str:
    trigraph = unicode_to_trigraphs(ch)
    if trigraph != ch and _in_winansi(trigraph):
        return trigraph
    return "?"


def text_width(text: str, size: float, bold: bool = False) -> float:
    return sum(stringWidth(piece, font, size) for font, piece in text_runs(text, bold))


def draw_text(pdf, text: str, x: float, y: float, size: float, bold=False):
    """Draws `text` with its left end at x, baseline y."""
    for font, piece in text_runs(text, bold):
        pdf.setFont(font, size)
        pdf.drawString(x, y, piece)
        x += stringWidth(piece, font, size)


def fitted_size(text: str, max_width: float, size: float = NAME_SIZE) -> float:
    """The largest font size, from `size` down to MIN_NAME_SIZE in half
    points, at which `text` fits in `max_width`."""
    while size > MIN_NAME_SIZE and text_width(text, size) > max_width:
        size -= 0.5
    return size


def draw_centered(pdf, text, cx, cy, max_width, size=NAME_SIZE, bold=False):
    """Draws `text` centered on (cx, cy), shrunk to fit max_width.
    Returns the left x and the size used."""
    size = fitted_size(text, max_width, size)
    width = text_width(text, size, bold)
    x = cx - width / 2
    # Cap height of Helvetica is about 0.72 em: centre on that.
    draw_text(pdf, text, x, cy - size * 0.36, size, bold)
    return x, size


# -- The keyboard -----------------------------------------------------------


class KeyBox(NamedTuple):
    """Where one cell of the keyboard is drawn, in points: left edge,
    top edge, size, and (for an assignable key) the height of the shifted
    strip across its top."""

    cell: object
    x: float
    y_top: float
    width: float
    height: float
    strip: float


def key_boxes(layout, left: float, top: float, width: float) -> List[KeyBox]:
    """The KeyBox of each of `layout`'s cells for a keyboard drawn
    `width` points wide with its top-left corner at (left, top). Rows are
    three quarters as tall as the widest key, up to MAX_ROW_HEIGHT."""
    column_width = width / GRID_COLUMNS
    widest_key = width / min(len(row) for row in layout)
    row_height = min(MAX_ROW_HEIGHT, widest_key * 0.75)
    boxes = []
    for placement in layout_placements(layout):
        height = placement.rowspan * row_height - KEY_GAP
        boxes.append(
            KeyBox(
                placement.cell,
                left + placement.column * column_width + KEY_GAP / 2,
                top - placement.row * row_height - KEY_GAP / 2,
                placement.columnspan * column_width - KEY_GAP,
                height,
                (row_height - KEY_GAP) * STRIP_SHARE,
            )
        )
    return boxes


def _draw_keyboard(page: _Page, layout, labels):
    boxes = key_boxes(layout, page.left, page.y, page.text_width)
    for box in boxes:
        if isinstance(box.cell, int):
            _draw_key(page.pdf, box, labels)
        else:
            _draw_fixed_key(page.pdf, box)
    page.y = min(box.y_top - box.height for box in boxes) - KEY_GAP / 2 - 12


def _draw_key(pdf, box: KeyBox, labels):
    key_number, x, y_top, width, height, strip = box
    # The whole key, then the shifted strip over its top.
    pdf.setStrokeColor(KEY_BORDER)
    pdf.setLineWidth(0.8)
    pdf.setFillColor(KEY_FACE)
    pdf.roundRect(x, y_top - height, width, height, CORNER_RADIUS, fill=1, stroke=1)
    pdf.setFillColor(SHIFT_STRIP)
    pdf.roundRect(x, y_top - strip, width, strip, CORNER_RADIUS, fill=1, stroke=1)
    # Square off the strip's bottom corners against the face.
    pdf.rect(x + 0.4, y_top - strip, width - 0.8, CORNER_RADIUS, fill=1, stroke=0)
    pdf.line(x, y_top - strip, x + width, y_top - strip)

    pdf.setFillColor(FAINT_INK)
    draw_text(
        pdf,
        f"{key_number:02d}",
        x + 3,
        y_top - height + 3,
        KEY_NUMBER_SIZE,
    )
    pdf.setFillColor(INK)

    inner = width - 8
    face_height = height - strip
    _draw_label(pdf, labels.get((key_number, True)), x, y_top, width, strip, inner)
    _draw_label(
        pdf,
        labels.get((key_number, False)),
        x,
        y_top - strip,
        width,
        face_height,
        inner,
    )


def _draw_label(pdf, label: Optional[KeyLabel], x, y_top, width, height, inner):
    """One assignment, centered in the box (x, y_top, width, height)."""
    if label is None:
        return
    cx = x + width / 2
    cy = y_top - height / 2
    marker = 0.0
    if label.kind == PROGRAM:
        marker = 6.0
    if label.flag_clear:
        # Room for the dashed box around the name.
        inner -= 6
    pdf.setFillColor(FAINT_INK if label.flag_clear else INK)
    left, size = draw_centered(
        pdf, label.name, cx + marker / 2, cy, inner - marker, bold=True
    )
    if label.kind == PROGRAM:
        _draw_program_marker(pdf, left - marker, cy, size)
    pdf.setFillColor(INK)
    if label.flag_clear:
        right = left + text_width(label.name, size, bold=True)
        left -= marker
        half = size * 0.65
        _draw_flag_clear_box(pdf, left - 3, cy + half, right - left + 6, 2 * half)


def _draw_program_marker(pdf, x, cy, size):
    """A small right-pointing triangle (the tab's ▸) before a global
    program's name."""
    half = size * 0.28
    path = pdf.beginPath()
    path.moveTo(x, cy - half)
    path.lineTo(x, cy + half)
    path.lineTo(x + half * 1.6, cy)
    path.close()
    pdf.drawPath(path, fill=1, stroke=0)


def _draw_flag_clear_box(pdf, x, y_top, width, height):
    """The dashed outline that marks an assignment whose key flag is
    clear (the tab's ⚠)."""
    pdf.saveState()
    pdf.setStrokeColor(FAINT_INK)
    pdf.setLineWidth(0.6)
    pdf.setDash(2, 2)
    pdf.rect(x, y_top - height, width, height, fill=0, stroke=1)
    pdf.restoreState()


def _draw_fixed_key(pdf, box: KeyBox):
    label, x, y_top, width, height, _ = box
    if not label:
        return
    pdf.setStrokeColor(KEY_BORDER)
    pdf.setLineWidth(0.8)
    pdf.setFillColor(FIXED_KEY_FILL.get(label, FIXED_KEY_DEFAULT_FILL))
    pdf.roundRect(x, y_top - height, width, height, CORNER_RADIUS, fill=1, stroke=1)
    pdf.setFillColor(INK)
    text = _STATIC_LABEL_TEXT.get(label, label)
    draw_centered(pdf, text, x + width / 2, y_top - height / 2, width - 8, bold=True)


# -- Legend and list --------------------------------------------------------


def _draw_legend(page: _Page, labels):
    pdf = page.pdf
    # (what to draw in front of the text, or None; the text)
    items = [(None, "Gold strip: shifted assignment. Key face: unshifted assignment.")]
    if any(label.kind == PROGRAM for label in labels.values()):
        items.append(
            (_legend_program_marker, "Global program (XEQ of a global label).")
        )
    if any(label.flag_clear for label in labels.values()):
        items.append(
            (
                _legend_flag_clear_box,
                "Key flag clear (as after LKAOFF): the calculator treats the "
                "key as unassigned.",
            )
        )
    for draw_mark, text in items:
        page.y -= LEGEND_SIZE + 4
        x = page.left
        if draw_mark is not None:
            draw_mark(pdf, x, page.y)
            x += 12
        draw_text(pdf, text, x, page.y, LEGEND_SIZE)
    page.y -= 18


def _legend_program_marker(pdf, x, baseline):
    _draw_program_marker(pdf, x + 1, baseline + LEGEND_SIZE * 0.36, LEGEND_SIZE)


def _legend_flag_clear_box(pdf, x, baseline):
    _draw_flag_clear_box(pdf, x, baseline + LEGEND_SIZE, 8, LEGEND_SIZE + 2)


def table_rows(labels) -> List[Tuple[str, KeyLabel]]:
    """(key, label) for each assignment, in key order with each key's
    unshifted assignment first. A shifted key is written as the
    calculator writes it, negative ("-11")."""
    return [
        (f"{'-' if shifted else ''}{key_number:02d}", label)
        for (key_number, shifted), label in sorted(labels.items())
    ]


def _draw_table(page: _Page, labels, heading: str):
    """The list of every assignment, in as many side-by-side blocks as
    the page is wide enough for, each filled to the foot of the page
    before the next starts. It has a page of its own after the
    keyboard's (the caller starts it); a list too long for that page
    carries on onto another."""
    pdf = page.pdf
    continued = heading
    page.ensure_room(TABLE_LINE * 4, continued)
    page.y -= TABLE_SIZE + 2
    draw_text(pdf, "All assignments", page.left, page.y, TABLE_SIZE + 1, bold=True)
    page.y -= 6
    rows = table_rows(labels)
    if not rows:
        page.y -= TABLE_LINE
        draw_text(pdf, "No keys are assigned.", page.left, page.y, TABLE_SIZE)
        return
    blocks = max(1, int((page.text_width + TABLE_BLOCK_GAP) // TABLE_BLOCK_WIDTH))
    block_width = (page.text_width + TABLE_BLOCK_GAP) / blocks
    while rows:
        page.ensure_room(TABLE_LINE * 2, continued)
        lines = int((page.y - page.bottom) // TABLE_LINE) - 1
        chunk, rows = rows[: lines * blocks], rows[lines * blocks :]
        # Each block runs to the foot of the page before the next starts.
        used = min(lines, len(chunk))
        top = page.y
        for block in range(blocks):
            part = chunk[block * lines : (block + 1) * lines]
            if not part:
                break
            left = page.left + block * block_width
            _table_block(page, left, left + block_width - TABLE_BLOCK_GAP, top, part)
        page.y = top - (used + 1) * TABLE_LINE
        if rows:
            page.y = page.bottom  # the next chunk starts a new page


def _table_block(page: _Page, left, right, top, rows):
    pdf = page.pdf
    columns = (left, left + TABLE_KEY_WIDTH, left + TABLE_KEY_WIDTH + TABLE_NAME_WIDTH)
    y = top - TABLE_LINE
    for x, text in zip(columns, ("Key", "Assignment", "Notes")):
        draw_text(pdf, text, x, y, TABLE_SIZE, bold=True)
    pdf.setLineWidth(0.5)
    pdf.setStrokeColor(FAINT_INK)
    pdf.line(left, y - 3, right, y - 3)
    pdf.setStrokeColor(INK)
    for key, label in rows:
        y -= TABLE_LINE
        draw_text(pdf, key, columns[0], y, TABLE_SIZE)
        name_x = columns[1]
        if label.kind == PROGRAM:
            _draw_program_marker(pdf, name_x, y + TABLE_SIZE * 0.36, TABLE_SIZE)
            name_x += 7
        draw_text(pdf, label.name, name_x, y, TABLE_SIZE)
        if label.flag_clear:
            draw_text(pdf, FLAG_CLEAR_NOTE, columns[2], y, TABLE_SIZE)
