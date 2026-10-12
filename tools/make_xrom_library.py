#!/usr/bin/env python3
"""Builds DM41_Explorer XROM library files (GitHub issue #44)
from a module image or from an hp41uc XROM list.

Run from the repository root:

    python3 tools/make_xrom_library.py MODULE.mod [MODULE.mod ...] -o OUT_DIR
    python3 tools/make_xrom_library.py LIST.txt --title "My ROM" -o OUT_DIR

The conversion itself is memory/xrom_import.py's, the same code the
application's XROM manager uses to import a file (issue #53); see that
module for what is read from each kind of file.

Each input becomes OUT_DIR/<id>.json, where <id> is the input's file name
in lower case, with anything but letters and digits turned into "-".
Warnings (a FAT entry whose name can't be read, which is left out) go to
stderr.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from memory.xrom_import import convert_file, library_json  # noqa: E402
from memory.xrom_library import XromLibraryError  # noqa: E402


def write_library(library: dict, out_dir: Path) -> Path:
    """Writes `library` (JSON data) to OUT_DIR/<id>.json."""
    out = out_dir / f"{library['id']}.json"
    out.write_text(library_json(library), encoding="utf-8")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "inputs", nargs="+", type=Path, help=".mod or hp41uc .txt files"
    )
    parser.add_argument("-o", "--out-dir", type=Path, required=True)
    parser.add_argument("--title", help="library title, for an hp41uc .txt input")
    args = parser.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    status = 0
    for path in args.inputs:
        try:
            library, warnings = convert_file(path, args.title)
        except XromLibraryError as e:
            print(e, file=sys.stderr)
            status = 1
            continue
        for warning in warnings:
            print(f"{path.name}: {warning}", file=sys.stderr)
        print(write_library(library, args.out_dir))
    return status


if __name__ == "__main__":
    sys.exit(main())
