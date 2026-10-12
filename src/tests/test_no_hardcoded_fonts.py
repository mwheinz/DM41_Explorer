"""Guard for GitHub issue #61: every font in src/gui comes from a font role.

gui/fonts.py is the one place that builds fonts from a family and size;
everything else asks it with font_for(role) or font_spec(role). This test
reads the source of every other module in src/gui and fails on what would
bypass that:

  * a CTkFont(...) built directly
  * a family= or size= given as a literal (CTkFont(size=12),
    tkfont.Font(family="Courier"), ...)
  * a font= given as a literal tuple or string (font=("Courier", 12))
  * a font family named in a string ("Courier", "Helvetica", ...), or one
    of Tk's named fonts, which gui/fonts.py resolves

It reads the syntax tree, not the text, so comments and docstrings can
still mention fonts.
"""

import ast
from pathlib import Path

import pytest

GUI_DIR = Path(__file__).resolve().parent.parent / "gui"
FONTS_MODULE = "fonts.py"

# Families a hard-coded font is likely to name, and Tk's named fonts.
# Compared without case, as a whole string.
FONT_NAMES = {
    name.casefold()
    for name in (
        "Arial",
        "Cantarell",
        "Consolas",
        "Courier",
        "Courier New",
        "DejaVu Sans",
        "DejaVu Sans Mono",
        "Helvetica",
        "Helvetica Neue",
        "Liberation Mono",
        "Liberation Sans",
        "Menlo",
        "Monaco",
        "Roboto",
        "SF Display",
        "Segoe UI",
        "Times",
        "Times New Roman",
        "Verdana",
        "TkDefaultFont",
        "TkFixedFont",
        "TkTextFont",
        "TkHeadingFont",
        "TkCaptionFont",
        "TkSmallCaptionFont",
        "TkMenuFont",
        "TkTooltipFont",
        "TkIconFont",
    )
}


def _docstring_nodes(tree) -> set:
    """The string constants that are docstrings, which may name fonts."""
    nodes = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                nodes.add(body[0].value)
    return nodes


def _called_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _is_literal(node) -> bool:
    return isinstance(node, (ast.Constant, ast.Tuple, ast.List)) and not (
        isinstance(node, ast.Constant) and node.value is None
    )


def font_violations(source: str) -> list:
    """(line, message) for each way `source` bypasses the font roles."""
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if _called_name(node) == "CTkFont":
                found.append((node.lineno, "builds a CTkFont; use font_for(role)"))
            for keyword in node.keywords:
                if keyword.arg == "family" and isinstance(keyword.value, ast.Constant):
                    found.append((node.lineno, "a literal family="))
                elif (
                    keyword.arg == "size"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, (int, float))
                ):
                    found.append((node.lineno, "a literal size="))
                elif keyword.arg == "font" and _is_literal(keyword.value):
                    found.append((node.lineno, "a literal font="))
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node not in docstrings
            and node.value.strip().casefold() in FONT_NAMES
        ):
            found.append((node.lineno, f"names the font {node.value!r}"))
    return sorted(found)


def _gui_modules():
    return sorted(p for p in GUI_DIR.glob("*.py") if p.name != FONTS_MODULE)


@pytest.mark.parametrize("path", _gui_modules(), ids=lambda p: p.name)
def test_no_font_is_hard_coded(path):
    violations = font_violations(path.read_text(encoding="utf-8"))
    assert not violations, "\n".join(
        f"src/gui/{path.name}:{line}: {message}" for line, message in violations
    )


def test_every_gui_module_is_checked():
    names = {p.name for p in _gui_modules()}
    assert {"app.py", "tab_common.py", "preferences_dialog.py"} <= names
    assert FONTS_MODULE not in names


# ---- the checker itself, so the guard can't pass by finding nothing ----


@pytest.mark.parametrize(
    "source",
    [
        "f = ctk.CTkFont()",
        "f = CTkFont(weight='bold')",
        "f = font_for('ui', size=12)",
        "f = tkfont.Font(family=name, size=11)",
        "f = tkfont.Font(family='Menlo')",
        "label = ctk.CTkLabel(root, font=('Courier', 12))",
        "label = ctk.CTkLabel(root, font='TkFixedFont')",
        "style.configure('Treeview', font=['Helvetica', 12, 'bold'])",
        "MONO = 'Courier'",
        "f = tkfont.nametofont('TkDefaultFont')",
    ],
)
def test_the_checker_finds_a_hard_coded_font(source):
    assert font_violations(source)


@pytest.mark.parametrize(
    "source",
    [
        "f = font_for('mono')",
        "f = font_for('small', weight='bold')",
        "label = ctk.CTkLabel(root, font=font_for('title'))",
        "label = ctk.CTkLabel(root, font=None)",
        "style.configure('Treeview', font=font)",
        "cell = tkfont.Font(font=spec)",
        "sample.configure(family=spec.family, size=spec.size)",
        "image = ctk.CTkImage(light, size=(20, 20))",
        "frame.grid_columnconfigure(0, minsize=40)",
        'def f():\n    """Uses Courier, says the docstring."""',
        "# Courier, in a comment",
    ],
)
def test_the_checker_allows_a_font_role(source):
    assert font_violations(source) == []
