"""占位符映射库：与业务库物理隔离的加密存储，只有隐私层可以读写。"""

from __future__ import annotations

import os
import sqlite3
import string
import threading
from pathlib import Path

from cryptography.fernet import Fernet

from ..config import settings

_PREFIX = {
    "company": "企业",
    "person": "人员P",
    "phone": "电话",
    "landline": "电话",
    "id_card": "证件",
    "bank_card": "账号",
    "email": "邮箱",
    "tech_secret": "技术细节",
}

_lock = threading.Lock()


def _key() -> bytes:
    env_key = os.environ.get("TECHINS_VAULT_KEY")
    if env_key:
        return env_key.encode()
    path = settings.data_dir / ".vault_key"
    if not path.exists():
        path.write_bytes(Fernet.generate_key())
        path.chmod(0o600)
    return path.read_bytes()


_fernet = Fernet(_key())
_db_path: Path = settings.data_dir / "privacy_vault.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path, timeout=30, check_same_thread=False)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS vault (scope TEXT, token TEXT, kind TEXT, cipher BLOB, PRIMARY KEY(scope, token))"
    )
    return conn


def _company_label(index: int) -> str:
    letters = string.ascii_uppercase
    label = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        label = letters[rem] + label
    return label


class TokenVault:
    """单次运行（scope）内，同一原文始终映射为同一占位符，保证跨步骤一致。"""

    def __init__(self, scope: str) -> None:
        self.scope = scope
        self._forward: dict[str, str] = {}
        self._reverse: dict[str, str] = {}
        self._counters: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        with _lock, _conn() as conn:
            rows = conn.execute("SELECT token, kind, cipher FROM vault WHERE scope=?", (self.scope,)).fetchall()
        for token, kind, cipher in rows:
            original = _fernet.decrypt(cipher).decode()
            self._forward[original] = token
            self._reverse[token] = original
            self._counters[kind] = self._counters.get(kind, 0) + 1

    def token_for(self, kind: str, original: str) -> str:
        if original in self._forward:
            return self._forward[original]
        prefix_kind = "phone" if kind == "landline" else kind
        count = self._counters.get(prefix_kind, 0)
        if prefix_kind == "company":
            label = _PREFIX["company"] + _company_label(count)
        else:
            label = f"{_PREFIX[prefix_kind]}{count + 1}"
        token = f"【{label}】"
        self._counters[prefix_kind] = count + 1
        self._forward[original] = token
        self._reverse[token] = original
        with _lock, _conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO vault(scope, token, kind, cipher) VALUES (?,?,?,?)",
                (self.scope, token, prefix_kind, _fernet.encrypt(original.encode())),
            )
        return token

    def originals(self) -> list[str]:
        return list(self._forward.keys())

    def rehydrate_text(self, text: str) -> str:
        if not text or "【" not in text:
            return text
        for token in sorted(self._reverse, key=len, reverse=True):
            if token in text:
                text = text.replace(token, self._reverse[token])
        return text

    @property
    def size(self) -> int:
        return len(self._forward)
