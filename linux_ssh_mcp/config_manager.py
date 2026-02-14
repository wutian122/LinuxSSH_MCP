from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from linux_ssh_mcp.settings import SSHMCPSettings


class ConfigManager:
    def __init__(self, settings: SSHMCPSettings) -> None:
        self.settings = settings

    @classmethod
    def load(
        cls,
        *,
        config_file: Path | None = None,
        env_file: Path | None = None,
        env_prefix: str = "SSH_MCP_",
    ) -> ConfigManager:
        config_path = config_file or Path(
            os.getenv(f"{env_prefix}CONFIG_FILE", "ssh_mcp_config.json")
        )

        json_data: dict[str, Any] = {}
        if config_path.exists() and config_path.is_file():
            json_data = cls._read_json(config_path)

        dotenv_data: dict[str, Any] = {}
        if env_file is not None:
            dotenv_data = cls._read_dotenv(env_file, env_prefix)
        elif Path(".env").exists():
            dotenv_data = cls._read_dotenv(Path(".env"), env_prefix)

        env_data = cls._read_env(os.environ, env_prefix)
        merged: dict[str, Any] = {
            **json_data,
            **dotenv_data,
            **env_data,
            "config_file": config_path,
        }
        settings = SSHMCPSettings.model_validate(merged)
        return cls(settings)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("配置文件必须是JSON对象")
        return raw

    @staticmethod
    def _read_dotenv(path: Path, env_prefix: str) -> dict[str, Any]:
        raw = dotenv_values(path)
        return ConfigManager._read_env(raw, env_prefix)

    @staticmethod
    def _read_env(mapping: Mapping[str, Any], env_prefix: str) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for field_name in SSHMCPSettings.model_fields.keys():
            env_key = f"{env_prefix}{field_name.upper()}"
            if env_key in mapping and mapping[env_key] not in (None, ""):
                data[field_name] = mapping[env_key]
        return data
