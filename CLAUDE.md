# CLAUDE.md

DM41_Explorer is a CustomTkinter desktop GUI (macOS, Linux, Windows) for
reading, editing and writing the memory of SwissMicros DM41L/DM41X calculators
(HP-41CX emulators), either live over the DM41L's serial console or offline
from `.dm41`/`.d41` state files. GitHub: `mwheinz/DM41_Explorer`. GPL-3.0.

## Environment and commands

The project venv is `dm41-venv/`; plain `python`, `pytest` and `ruff` are not
on PATH. Run everything from the repository root:

```sh
dm41-venv/bin/ruff format <changed .py files>   # CI gates on `ruff format --check .`
dm41-venv/bin/ruff check .
dm41-venv/bin/python -m pytest                   # ASK FIRST (see below); ~75 s
dm41-venv/bin/python -m pytest src/tests/test_regions.py -k name
cd src && ../dm41-venv/bin/python -m gui.app      # run the app
cd src && ../dm41-venv/bin/python -m tests.golden # regenerate golden.json
```

- pytest config is in `pyproject.toml` (`pythonpath = ["src"]`), so tests and
  modules import top-level packages: `from memory import Memory, DM41X`.
- ruff 0.17.0, line length 88, lint rules `E4 E7 E9 F`. Tests may use E402
  (GUI tests `pytest.importorskip` CustomTkinter before importing it).
- `src/build.sh` builds a PyInstaller binary; `tools/make_screenshots.py`
  regenerates README screenshots; `tools/make_xrom_library.py` builds XROM
  library JSON.

## Working rules

- **Ask before running pytest**, any part of it. Offer the command instead.
- **Format and lint every changed `.py` file before committing.**
- **Stage files by explicit path**, never `git add -A`/`git add .`. The user
  keeps untracked work files in the repo (e.g. new test captures); mention
  untracked files you didn't create rather than committing them.
- Commit or push only when asked. Don't open PRs, file issues or post issue
  comments without the user's OK for that specific action.
- Plans and design write-ups go in GitHub issue bodies/comments, not new
  files in `docs/`. `docs/` holds permanent reference specs (memory format,
  flags, programs, key assignments, ...); keep those in sync when decoding
  changes. Reference issue numbers (`#61`) in code comments.
- Windows is verified only by CI (the user has no Windows machine). Manual
  visual checks on macOS/Linux are fine to ask for.
- `issues.json` is a scratch issue dump, and `_to_delete/` is junk; both are
  gitignored. Ignore them.

## Commit style

Imperative, sentence-case subject with no trailing period, saying what the
change does for the user ("Read and write XM files whose name and header
straddle a region boundary"). The body explains *why*, and ties the change to
its issue ("Part of #61.", "Fifth step of #61."). Wrap at ~72 columns.

## Architecture (`src/`)

- `memory/` -- the data model, no Tk. `Memory` (memory.py) parses/serializes a
  state and hands out **region** objects (regions.py): `StatusRegisters`,
  `KeyAssignments`, `Alarms`, `ProgramMemory`, `DataMemory`,
  `ExtendedMemory` (xm_file.py). Regions read their boundaries **live** from
  the pointers in status register c, so a region held across an edit stays
  correct; `RegionSpan` is the immutable snapshot for display code.
  `DeviceProfile` (`DM41L`, `DM41X`) carries per-model facts (XM regions,
  built-in XROMs); a state file cannot say which model wrote it, so callers
  always pass `profile=`. `profile_fit.py` checks whether a state fits a model.
- Register byte order: state files and `Register._data` are MSB-first
  (`_data[0]` is HP-41 "byte 6"). Remember this when reading HP-41 docs.
- Instruction names: `functions.py` is raw data; go through `mnemonics.py`
  (canonical hp41uc names for text export, display names with Unicode glyphs
  for the GUI, input aliases for import). XROM names come from the JSON
  libraries in `memory/xroms/` via `xrom_library.py`. On a real DM41X, name
  lookup (ASN) prefers plugged-in module XROMs over same-named mainframe
  functions.
- `engine/` -- serial console protocol (`SerialManager`, `CommandEngine`,
  command classes). `tools/fake_dm41_device.py` fakes a device on a pty.
- `gui/` -- CustomTkinter app; `app.py` is the entry point, one module per
  tab/dialog. All fonts come from the roles in `gui/fonts.py`
  (`font_for(role)`); `test_no_hardcoded_fonts.py` fails on any literal
  family/size/`CTkFont(...)` elsewhere in `gui/`.
- `config.py` -- preferences file; writes go through
  `memory/atomic_write.py`.

## Errors and logging (see CONTRIBUTING.md)

- Each module: `logger = logging.getLogger(__name__)`; never configure
  handlers outside `gui/app.py`.
- `memory/` has **no loggers**: it raises `ValueError`/`DM41MemoryError` and
  the GUI code that catches it logs once.
- Every `messagebox.showerror(...)` gets a `logger.error`/`logger.exception`
  right next to it.

## Tests (`src/tests/`)

- Behavioral changes to `memory/` or `engine/` need tests.
- `tests/data/` holds real captures from DM41L/DM41X hardware. Names starting
  `dm41x` (and `.d41` files) load with the DM41X profile, the rest as DM41L
  (`fixture_loading.py`, `golden.py`). `tests/data/README.md` describes them.
- `golden.json` is ground truth checked against the calculator, not a record
  of current output. Regenerate only for an intentional decoding change, and
  review the diff line by line.
- GUI tests check behavior and widget structure, never pixel sizes or fonts;
  CI runs them on Linux (xvfb), macOS and Windows (x64 and ARM).
