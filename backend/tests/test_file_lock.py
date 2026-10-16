from __future__ import annotations

import subprocess
import sys

import pytest

from app.file_lock import file_lock


def test_reentrant_lock_releases_after_exception(tmp_path):
    path = tmp_path / "write.lock"
    with pytest.raises(ValueError), file_lock(path), file_lock(tmp_path / "." / "write.lock"):
        raise ValueError("interrupted write")
    with file_lock(path):
        assert path.is_file()


def test_lock_serializes_separate_processes(tmp_path):
    path = tmp_path / "write.lock"
    script = """
import sys
from pathlib import Path
from app.file_lock import file_lock
print('ready', flush=True)
with file_lock(Path(sys.argv[1])):
    print('acquired', flush=True)
"""
    with file_lock(path):
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", script, str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        assert process.stdout.readline().strip() == "ready"
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=0.2)
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stderr
    assert stdout.strip() == "acquired"
