import json
from pathlib import Path

import pytest

from linux_ssh_mcp.config_manager import ConfigManager


def test_config_manager_loads_json_then_env_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "ssh_mcp_config.json"
    config_file.write_text(
        json.dumps({"log_level": "WARNING"}, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setenv("SSH_MCP_LOG_LEVEL", "ERROR")
    manager = ConfigManager.load(config_file=config_file)
    assert manager.settings.log_level == "ERROR"


def test_config_manager_uses_default_config_file_if_env_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "c.json"
    config_file.write_text(json.dumps({"log_level": "WARNING"}), encoding="utf-8")
    monkeypatch.setenv("SSH_MCP_CONFIG_FILE", str(config_file))

    manager = ConfigManager.load()
    assert manager.settings.log_level == "WARNING"
