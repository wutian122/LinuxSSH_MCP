from __future__ import annotations

from dataclasses import dataclass

import keyring


@dataclass(frozen=True)
class SSHCredentials:
    host: str
    username: str
    password: str | None
    private_key_path: str | None

    @property
    def auth_mode(self) -> str:
        if self.private_key_path and self.password:
            return "mixed"
        if self.private_key_path:
            return "key"
        if self.password:
            return "password"
        return "none"


class AuthManager:
    def __init__(self, *, service_name: str = "linux-ssh-mcp") -> None:
        self._service_name = service_name

    def store_credentials(
        self,
        *,
        host: str,
        username: str,
        password: str | None = None,
        private_key_path: str | None = None,
    ) -> None:
        if not password and not private_key_path:
            raise ValueError("至少提供password或private_key_path")

        if password:
            keyring.set_password(
                self._service_name,
                self._key(host, username, "password"),
                password,
            )
        if private_key_path:
            keyring.set_password(
                self._service_name,
                self._key(host, username, "private_key_path"),
                private_key_path,
            )

    def get_credentials(self, *, host: str, username: str) -> SSHCredentials:
        password = keyring.get_password(self._service_name, self._key(host, username, "password"))
        private_key_path = keyring.get_password(
            self._service_name,
            self._key(host, username, "private_key_path"),
        )
        return SSHCredentials(
            host=host,
            username=username,
            password=password,
            private_key_path=private_key_path,
        )

    @staticmethod
    def _key(host: str, username: str, field: str) -> str:
        return f"{host}|{username}|{field}".lower()
