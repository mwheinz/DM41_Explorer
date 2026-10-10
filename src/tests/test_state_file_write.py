"""Memory.to_file() writes atomically: a failure part-way through must
leave an existing state file untouched, never truncated."""

import os
import sys
from pathlib import Path

import pytest

from memory import Memory

DATA_DIR = Path(__file__).parent / "data"

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permissions and symlinks"
)


def _leftovers(directory, name):
    return [p for p in os.listdir(directory) if p != name]


def test_to_file_writes_the_state_and_clears_modified(tmp_path):
    memory = Memory.from_file(DATA_DIR / "6x-xm.dm41")
    memory.is_modified()  # sets the flag
    path = tmp_path / "out.dm41"

    memory.to_file(path)

    assert Memory.from_file(path) == memory
    assert memory.modified is False
    assert _leftovers(tmp_path, "out.dm41") == []


def test_failed_write_leaves_the_existing_file_untouched(tmp_path, monkeypatch):
    path = tmp_path / "state.dm41"
    original = (DATA_DIR / "6x-xm.dm41").read_text(encoding="utf-8")
    path.write_text(original, encoding="utf-8")
    memory = Memory()
    memory.is_modified()

    def fail(self):
        raise OSError("disk full")

    monkeypatch.setattr(Memory, "to_string", fail)
    with pytest.raises(OSError, match="disk full"):
        memory.to_file(path)

    assert path.read_text(encoding="utf-8") == original
    assert memory.modified is True
    assert _leftovers(tmp_path, "state.dm41") == []


def test_failed_replace_leaves_the_existing_file_untouched(tmp_path, monkeypatch):
    path = tmp_path / "state.dm41"
    path.write_text("original", encoding="utf-8")

    def fail(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="replace failed"):
        Memory().to_file(path)

    assert path.read_text(encoding="utf-8") == "original"
    assert _leftovers(tmp_path, "state.dm41") == []


@posix_only
def test_an_existing_file_keeps_its_permissions(tmp_path):
    path = tmp_path / "state.dm41"
    path.write_text("original", encoding="utf-8")
    os.chmod(path, 0o640)

    Memory().to_file(path)

    assert os.stat(path).st_mode & 0o777 == 0o640


@posix_only
def test_a_new_file_gets_the_usual_permissions(tmp_path):
    umask = os.umask(0o022)
    try:
        path = tmp_path / "new.dm41"
        Memory().to_file(path)
        assert os.stat(path).st_mode & 0o777 == 0o644
    finally:
        os.umask(umask)


@posix_only
def test_a_symlinked_state_file_is_replaced_through_the_link(tmp_path):
    real = tmp_path / "real.dm41"
    real.write_text("original", encoding="utf-8")
    link = tmp_path / "link.dm41"
    link.symlink_to(real)

    Memory().to_file(link)

    assert link.is_symlink()
    assert real.read_text(encoding="utf-8") == Memory().to_string()
