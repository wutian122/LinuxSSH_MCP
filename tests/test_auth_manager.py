from linux_ssh_mcp.auth_manager import AuthManager


def test_auth_manager_store_and_get_password(monkeypatch):
    store = {}

    def set_password(service_name: str, key: str, password: str) -> None:
        store[(service_name, key)] = password

    def get_password(service_name: str, key: str):
        return store.get((service_name, key))

    monkeypatch.setattr("keyring.set_password", set_password)
    monkeypatch.setattr("keyring.get_password", get_password)

    auth = AuthManager(service_name="test")
    auth.store_credentials(host="1.2.3.4", username="root", password="secret")
    creds = auth.get_credentials(host="1.2.3.4", username="root")
    assert creds.password == "secret"
    assert creds.private_key_path is None
    assert creds.auth_mode == "password"


def test_auth_manager_store_and_get_key_path(monkeypatch):
    store = {}

    def set_password(service_name: str, key: str, password: str) -> None:
        store[(service_name, key)] = password

    def get_password(service_name: str, key: str):
        return store.get((service_name, key))

    monkeypatch.setattr("keyring.set_password", set_password)
    monkeypatch.setattr("keyring.get_password", get_password)

    auth = AuthManager(service_name="test")
    auth.store_credentials(host="1.2.3.4", username="root", private_key_path="/id_ed25519")
    creds = auth.get_credentials(host="1.2.3.4", username="root")
    assert creds.private_key_path == "/id_ed25519"
    assert creds.password is None
    assert creds.auth_mode == "key"


def test_auth_manager_mixed_mode(monkeypatch):
    store = {}

    def set_password(service_name: str, key: str, password: str) -> None:
        store[(service_name, key)] = password

    def get_password(service_name: str, key: str):
        return store.get((service_name, key))

    monkeypatch.setattr("keyring.set_password", set_password)
    monkeypatch.setattr("keyring.get_password", get_password)

    auth = AuthManager(service_name="test")
    auth.store_credentials(
        host="1.2.3.4",
        username="root",
        password="secret",
        private_key_path="/id_ed25519",
    )
    creds = auth.get_credentials(host="1.2.3.4", username="root")
    assert creds.auth_mode == "mixed"
