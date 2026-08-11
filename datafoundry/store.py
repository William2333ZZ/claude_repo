"""SQLite 元数据存储:用户 / API Key / 数据集 / 运行 / 审计。

数据目录 DATAFOUNDRY_HOME(默认 ./.datafoundry):
  meta.db      元数据库
  secret.key   令牌签名密钥(0600)
  runs/<id>/   每次运行的 output/rejects/manifest
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from datafoundry.security import (
    hash_api_key,
    hash_password,
    load_or_create_secret,
    new_api_key,
    password_needs_rehash,
    verify_password,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    pw_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    key_hash TEXT UNIQUE NOT NULL,
    label TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    n_samples INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    dataset_id TEXT NOT NULL REFERENCES datasets(id),
    steps_json TEXT NOT NULL,
    status TEXT NOT NULL,
    manifest_json TEXT,
    error TEXT,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    username TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


class Store:
    def __init__(self, home: str | Path | None = None):
        self.home = Path(home or os.environ.get("DATAFOUNDRY_HOME", ".datafoundry")).resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self.secret = load_or_create_secret(self.home)
        self.runs_dir = self.home / "runs"
        self.runs_dir.mkdir(exist_ok=True)
        self.uploads_dir = self.home / "uploads"
        self.uploads_dir.mkdir(exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.home / "meta.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    # ---------- 通用 ----------

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def _query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def audit(self, username: str, action: str, detail: str = "") -> None:
        self._exec(
            "INSERT INTO audit(ts, username, action, detail) VALUES(?,?,?,?)",
            (time.time(), username, action, detail[:500]),
        )

    # ---------- 用户 ----------

    def create_user(self, username: str, password: str, role: str) -> dict:
        if not username or not username.isidentifier():
            raise ValueError("用户名只允许字母/数字/下划线且不以数字开头")
        if len(password) < 8:
            raise ValueError("口令至少 8 位")
        try:
            cur = self._exec(
                "INSERT INTO users(username, pw_hash, role, created_at) VALUES(?,?,?,?)",
                (username, hash_password(password), role, time.time()),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"用户已存在: {username}") from None
        return {"id": cur.lastrowid, "username": username, "role": role}

    def authenticate(self, username: str, password: str) -> dict | None:
        rows = self._query("SELECT * FROM users WHERE username=?", (username,))
        if not rows:
            # 用户不存在也走一次哈希,避免时间侧信道区分用户是否存在
            hash_password(password)
            return None
        row = rows[0]
        if not verify_password(password, row["pw_hash"]):
            return None
        if password_needs_rehash(row["pw_hash"]):  # argon2 参数升级时透明重哈希
            self._exec("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(password), row["id"]))
        return {"id": row["id"], "username": row["username"], "role": row["role"]}

    def get_user(self, user_id: int) -> dict | None:
        rows = self._query("SELECT id, username, role FROM users WHERE id=?", (user_id,))
        return dict(rows[0]) if rows else None

    def list_users(self) -> list[dict]:
        return [dict(r) for r in self._query("SELECT id, username, role, created_at FROM users ORDER BY id")]

    def count_users(self) -> int:
        return self._query("SELECT COUNT(*) AS n FROM users")[0]["n"]

    # ---------- API Key ----------

    def create_api_key(self, user_id: int, label: str) -> dict:
        plain, key_hash = new_api_key()
        cur = self._exec(
            "INSERT INTO api_keys(user_id, key_hash, label, created_at) VALUES(?,?,?,?)",
            (user_id, key_hash, label[:80] or "default", time.time()),
        )
        return {"id": cur.lastrowid, "key": plain, "label": label, "note": "明文只展示这一次"}

    def resolve_api_key(self, plain: str) -> dict | None:
        rows = self._query(
            "SELECT u.id, u.username, u.role FROM api_keys k JOIN users u ON u.id=k.user_id "
            "WHERE k.key_hash=? AND k.revoked=0",
            (hash_api_key(plain),),
        )
        return dict(rows[0]) if rows else None

    def list_api_keys(self, user_id: int) -> list[dict]:
        return [
            dict(r)
            for r in self._query(
                "SELECT id, label, revoked, created_at FROM api_keys WHERE user_id=? ORDER BY id", (user_id,)
            )
        ]

    def revoke_api_key(self, user_id: int, key_id: int) -> bool:
        cur = self._exec("UPDATE api_keys SET revoked=1 WHERE id=? AND user_id=?", (key_id, user_id))
        return cur.rowcount > 0

    # ---------- 数据集 ----------

    def register_dataset(self, name: str, path: str, n_samples: int, created_by: str) -> dict:
        ds_id = "ds_" + secrets.token_hex(6)
        self._exec(
            "INSERT INTO datasets(id, name, path, n_samples, created_by, created_at) VALUES(?,?,?,?,?,?)",
            (ds_id, name[:120], path, n_samples, created_by, time.time()),
        )
        return {"id": ds_id, "name": name, "path": path, "n_samples": n_samples}

    def get_dataset(self, ds_id: str) -> dict | None:
        rows = self._query("SELECT * FROM datasets WHERE id=?", (ds_id,))
        return dict(rows[0]) if rows else None

    def list_datasets(self) -> list[dict]:
        return [dict(r) for r in self._query("SELECT * FROM datasets ORDER BY created_at DESC")]

    # ---------- 运行 ----------

    def create_run(self, name: str, dataset_id: str, steps: list[dict], created_by: str) -> dict:
        run_id = "run_" + secrets.token_hex(6)
        self._exec(
            "INSERT INTO runs(id, name, dataset_id, steps_json, status, created_by, created_at) "
            "VALUES(?,?,?,?, 'queued', ?, ?)",
            (run_id, name[:120], dataset_id, json.dumps(steps, ensure_ascii=False), created_by, time.time()),
        )
        return {"id": run_id, "status": "queued"}

    def set_run_status(self, run_id: str, status: str, manifest: dict | None = None, error: str | None = None) -> None:
        self._exec(
            "UPDATE runs SET status=?, manifest_json=COALESCE(?, manifest_json), error=?, finished_at=? WHERE id=?",
            (
                status,
                json.dumps(manifest, ensure_ascii=False) if manifest else None,
                error,
                time.time() if status in ("succeeded", "failed") else None,
                run_id,
            ),
        )

    def get_run(self, run_id: str) -> dict | None:
        rows = self._query("SELECT * FROM runs WHERE id=?", (run_id,))
        if not rows:
            return None
        row = dict(rows[0])
        row["steps"] = json.loads(row.pop("steps_json"))
        mj = row.pop("manifest_json")
        row["manifest"] = json.loads(mj) if mj else None
        return row

    def list_runs(self) -> list[dict]:
        return [
            dict(r)
            for r in self._query(
                "SELECT id, name, dataset_id, status, created_by, created_at, finished_at FROM runs "
                "ORDER BY created_at DESC"
            )
        ]
