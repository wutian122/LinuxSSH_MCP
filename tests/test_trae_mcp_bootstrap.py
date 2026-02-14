from __future__ import annotations

from pathlib import Path

import pytest

from trae_mcp_bootstrap import build_execv_args


def test_build_execv_args_points_to_venv_python(tmp_path: Path) -> None:
    venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_bytes(b"")

    exe, argv = build_execv_args(project_root=tmp_path, extra_args=["--x", "1"])

    assert exe.endswith("python.exe")
    assert argv[:3] == [str(venv_python.resolve()), "-m", "linux_ssh_mcp"]
    assert argv[3:] == ["--x", "1"]


def test_build_execv_args_raises_when_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        build_execv_args(project_root=tmp_path, extra_args=[])
