"""元数据存储:用户 / API Key / 数据集 / 运行 / 审计 / 订单与额度账本。

后端二选一(P0-1 账本外置):
  - SQLite(默认,零依赖):DATAFOUNDRY_HOME/meta.db,适合本地与单机
  - PostgreSQL:设 DATAFOUNDRY_DB_URL(或平台注入的 DATABASE_URL)为 postgres://…
    即启用;需要 `pip install 'datafoundry[pg]'`。账本(orders/credit_ledger)
    随全部元数据落库外置,容器重建不丢——钱的记录只能放这里。

数据目录 DATAFOUNDRY_HOME(默认 ./.datafoundry):
  meta.db      元数据库(仅 SQLite 后端)
  secret.key   令牌签名密钥(0600)
  runs/<id>/   每次运行的 output/rejects/manifest(工件可再生,不入库)
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from datafoundry.service.security import (
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
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    plan TEXT NOT NULL,
    credits INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_ref TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    paid_at REAL
);
CREATE TABLE IF NOT EXISTS credit_ledger (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    delta INTEGER NOT NULL,
    reason TEXT NOT NULL,
    order_id TEXT,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS device_codes (
    id INTEGER PRIMARY KEY,
    device_hash TEXT UNIQUE NOT NULL,
    user_code TEXT UNIQUE NOT NULL,
    label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    user_id INTEGER,
    poll_interval REAL NOT NULL,
    last_poll REAL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
"""

_USER_CODE_ALPHABET = "BCDFGHJKMNPQRSTVWXZ23456789"  # 无易混字符(0/O/1/I/L/E/A/U/Y)

# SQLite DDL → PostgreSQL DDL 的最小转写(顺序敏感:先改主键再改普通列)
_PG_DDL_MAP = [
    ("INTEGER PRIMARY KEY", "BIGSERIAL PRIMARY KEY"),
    ("user_id INTEGER", "user_id BIGINT"),
    ("REAL", "DOUBLE PRECISION"),
]


def resolve_db_url(db_url: str | None = None) -> str:
    """显式参数 > DATAFOUNDRY_DB_URL > 平台注入的 DATABASE_URL;空串 = SQLite。"""
    url = db_url or os.environ.get("DATAFOUNDRY_DB_URL") or os.environ.get("DATABASE_URL") or ""
    return url if url.startswith(("postgres://", "postgresql://")) else ""


def pg_schema(sqlite_schema: str = _SCHEMA) -> str:
    ddl = sqlite_schema
    for a, b in _PG_DDL_MAP:
        ddl = ddl.replace(a, b)
    return ddl


class Store:
    def __init__(self, home: str | Path | None = None, db_url: str | None = None):
        self.home = Path(home or os.environ.get("DATAFOUNDRY_HOME", ".datafoundry")).resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self.secret = load_or_create_secret(self.home)
        self.runs_dir = self.home / "runs"
        self.runs_dir.mkdir(exist_ok=True)
        self.uploads_dir = self.home / "uploads"
        self.uploads_dir.mkdir(exist_ok=True)
        self._lock = threading.Lock()
        url = resolve_db_url(db_url)
        self.dialect = "postgres" if url else "sqlite"
        if self.dialect == "postgres":
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as e:  # pragma: no cover - 环境缺驱动时的提示路径
                raise RuntimeError(
                    "检测到 PostgreSQL 连接串,但缺少驱动:pip install 'datafoundry[pg]'"
                ) from e
            self._db = psycopg.connect(url, autocommit=True, row_factory=dict_row)
            self.IntegrityError = psycopg.IntegrityError
            with self._lock:
                for stmt in pg_schema().split(";"):
                    if stmt.strip():
                        self._db.execute(stmt)
        else:
            self._db = sqlite3.connect(self.home / "meta.db", check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self.IntegrityError = sqlite3.IntegrityError
            with self._lock:
                self._db.executescript(_SCHEMA)
                self._db.commit()

    # ---------- 通用(方言漏斗:所有 SQL 都从这里过) ----------

    def _sql(self, sql: str) -> str:
        return sql if self.dialect == "sqlite" else sql.replace("?", "%s")

    @contextmanager
    def _txn(self):
        """事务边界:pg 走显式 transaction(autocommit 连接),sqlite 走 commit/rollback。"""
        with self._lock:
            if self.dialect == "postgres":
                with self._db.transaction():
                    yield self._db
            else:
                try:
                    yield self._db
                    self._db.commit()
                except Exception:
                    self._db.rollback()
                    raise

    def _exec(self, sql: str, args: tuple = ()):
        with self._txn() as db:
            return db.execute(self._sql(sql), args)

    def _query(self, sql: str, args: tuple = ()) -> list:
        with self._lock:
            return self._db.execute(self._sql(sql), args).fetchall()

    def _insert_id(self, sql: str, args: tuple = ()) -> int:
        """自增主键插入:sqlite 用 lastrowid,pg 用 RETURNING id。"""
        if self.dialect == "postgres":
            with self._txn() as db:
                return db.execute(self._sql(sql) + " RETURNING id", args).fetchone()["id"]
        return self._exec(sql, args).lastrowid

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
            new_id = self._insert_id(
                "INSERT INTO users(username, pw_hash, role, created_at) VALUES(?,?,?,?)",
                (username, hash_password(password), role, time.time()),
            )
        except self.IntegrityError:
            raise ValueError(f"用户已存在: {username}") from None
        return {"id": new_id, "username": username, "role": role}

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
        new_id = self._insert_id(
            "INSERT INTO api_keys(user_id, key_hash, label, created_at) VALUES(?,?,?,?)",
            (user_id, key_hash, label[:80] or "default", time.time()),
        )
        return {"id": new_id, "key": plain, "label": label, "note": "明文只展示这一次"}

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

    # ---------- 计费:订单与额度账本(额度事实源在内核) ----------

    def create_order(self, user_id: int, plan: str, plan_def: dict, provider: str) -> dict:
        order_id = "ord_" + secrets.token_hex(8)
        self._exec(
            "INSERT INTO orders(id, user_id, plan, credits, amount_cents, currency, provider, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (order_id, user_id, plan, plan_def["credits"], plan_def["amount_cents"],
             plan_def["currency"], provider, time.time()),
        )
        return self.get_order(order_id)

    def get_order(self, order_id: str) -> dict | None:
        rows = self._query("SELECT * FROM orders WHERE id=?", (order_id,))
        return dict(rows[0]) if rows else None

    def set_order_ref(self, order_id: str, provider_ref: str) -> None:
        self._exec("UPDATE orders SET provider_ref=? WHERE id=?", (provider_ref, order_id))

    def mark_order_paid(self, order_id: str) -> dict | None:
        """幂等:重复回调只记账一次。成功返回订单,已支付/不存在返回 None。
        置 paid 与记账同事务——账本外置后这仍是唯一的资金写入点。"""
        with self._txn() as db:
            cur = db.execute(
                self._sql("UPDATE orders SET status='paid', paid_at=? WHERE id=? AND status='pending'"),
                (time.time(), order_id),
            )
            if cur.rowcount == 0:
                return None
            row = db.execute(self._sql("SELECT * FROM orders WHERE id=?"), (order_id,)).fetchone()
            db.execute(
                self._sql("INSERT INTO credit_ledger(user_id, delta, reason, order_id, ts) VALUES(?,?,?,?,?)"),
                (row["user_id"], row["credits"], f"purchase:{row['plan']}", order_id, time.time()),
            )
            return dict(row)

    def add_credits(self, user_id: int, delta: int, reason: str, order_id: str | None = None) -> None:
        self._exec(
            "INSERT INTO credit_ledger(user_id, delta, reason, order_id, ts) VALUES(?,?,?,?,?)",
            (user_id, delta, reason, order_id, time.time()),
        )

    def credit_balance(self, user_id: int) -> int:
        rows = self._query("SELECT COALESCE(SUM(delta),0) AS bal FROM credit_ledger WHERE user_id=?", (user_id,))
        return int(rows[0]["bal"])

    def list_orders(self, user_id: int) -> list[dict]:
        return [dict(r) for r in self._query(
            "SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC", (user_id,))]

    # ---------- 设备授权(RFC 8628 风格,feishu-cli / taptap 同款流程) ----------

    def device_start(self, label: str, ttl: float = 600.0, poll_interval: float = 3.0) -> dict:
        """发起设备授权:返回 device_code(明文,只此一次)与 user_code。库中只存 device_code 哈希。"""
        device_code = "dfd_" + secrets.token_urlsafe(32)
        now = time.time()
        for _ in range(10):  # user_code 撞库重试
            raw = "".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(8))
            user_code = f"{raw[:4]}-{raw[4:]}"
            try:
                self._exec(
                    "INSERT INTO device_codes(device_hash, user_code, label, poll_interval, created_at, expires_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (hash_api_key(device_code), user_code, label[:80] or "cli", poll_interval, now, now + ttl),
                )
                break
            except self.IntegrityError:
                continue
        else:
            raise RuntimeError("user_code 生成失败,请重试")
        return {
            "device_code": device_code,
            "user_code": user_code,
            "expires_in": int(ttl),
            "interval": poll_interval,
        }

    @staticmethod
    def normalize_user_code(code: str) -> str:
        raw = code.strip().upper().replace("-", "").replace(" ", "")
        return f"{raw[:4]}-{raw[4:]}" if len(raw) == 8 else code.strip().upper()

    def device_decide(self, user_code: str, user_id: int, approve: bool) -> str:
        """用户在浏览器/程序侧对 user_code 做出决定。返回结果状态。"""
        code = self.normalize_user_code(user_code)
        rows = self._query("SELECT * FROM device_codes WHERE user_code=?", (code,))
        if not rows:
            return "not_found"
        row = rows[0]
        if row["status"] == "pending" and time.time() > row["expires_at"]:
            self._exec("UPDATE device_codes SET status='expired' WHERE id=?", (row["id"],))
            return "expired"
        if row["status"] != "pending":
            return row["status"]
        status = "approved" if approve else "denied"
        self._exec("UPDATE device_codes SET status=?, user_id=? WHERE id=?", (status, user_id, row["id"]))
        return status

    def device_poll(self, device_code: str) -> dict:
        """CLI 轮询:pending/slow_down/approved(一次性发 API Key)/denied/expired/invalid。"""
        rows = self._query("SELECT * FROM device_codes WHERE device_hash=?", (hash_api_key(device_code),))
        if not rows:
            return {"status": "invalid"}
        row = rows[0]
        now = time.time()
        if row["status"] == "pending":
            if now > row["expires_at"]:
                self._exec("UPDATE device_codes SET status='expired' WHERE id=?", (row["id"],))
                return {"status": "expired"}
            if row["last_poll"] and now - row["last_poll"] < row["poll_interval"]:
                return {"status": "slow_down", "interval": row["poll_interval"]}
            self._exec("UPDATE device_codes SET last_poll=? WHERE id=?", (now, row["id"]))
            return {"status": "authorization_pending", "interval": row["poll_interval"]}
        if row["status"] == "approved":
            user = self.get_user(row["user_id"])
            if not user:
                return {"status": "invalid"}
            key = self.create_api_key(user["id"], f"device:{row['label']}")
            self._exec("UPDATE device_codes SET status='claimed' WHERE id=?", (row["id"],))
            return {
                "status": "approved",
                "api_key": key["key"],
                "key_id": key["id"],
                "username": user["username"],
                "role": user["role"],
            }
        return {"status": row["status"]}  # denied / claimed / expired

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

    def set_run_eval(self, run_id: str, eval_block: dict) -> bool:
        """[M1-A3] 评测分数写回 run:merge 进 manifest 的 eval 区块,闭环在此接通。"""
        run = self.get_run(run_id)
        if not run:
            return False
        manifest = run.get("manifest") or {}
        manifest["eval"] = eval_block
        self._exec(
            "UPDATE runs SET manifest_json=? WHERE id=?",
            (json.dumps(manifest, ensure_ascii=False), run_id),
        )
        return True

    def recipe_history(self, recipe_name: str) -> list[dict]:
        """[M2-F2] 配方效果档案:该配方名下全部 run 的 (数据集, 指纹, 计数, 成本, 评测) 记录。
        LIKE 先粗筛(SQLite/PG 通吃),Python 精确核对 manifest 字段;含历史 hash(配方
        内容变更后旧 run 仍按当时 hash 记录,档案自然呈现版本演化)。"""
        rows = self._query(
            "SELECT id, dataset_id, status, created_at, manifest_json FROM runs "
            "WHERE manifest_json LIKE ? ORDER BY created_at DESC",
            (f'%"recipe_name": "{recipe_name}"%',),
        )
        out = []
        for r in rows:
            try:
                m = json.loads(r["manifest_json"] or "{}")
            except json.JSONDecodeError:
                continue
            if m.get("recipe_name") != recipe_name:
                continue
            ev = m.get("eval") or {}
            out.append({
                "run_id": r["id"],
                "dataset_id": r["dataset_id"],
                "status": r["status"],
                "created_at": r["created_at"],
                "recipe_hash": m.get("recipe_hash"),
                "n_in": m.get("n_in"),
                "n_out": m.get("n_out"),
                "retention": m.get("retention"),
                "est_cost_total": m.get("est_cost_total"),
                "eval_accuracy": ev.get("accuracy"),
                "evalset": ev.get("evalset"),
            })
        return out

    def list_runs(self) -> list[dict]:
        return [
            dict(r)
            for r in self._query(
                "SELECT id, name, dataset_id, status, created_by, created_at, finished_at FROM runs "
                "ORDER BY created_at DESC"
            )
        ]
