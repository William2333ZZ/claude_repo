"""[P0-1] 账本外置:存储方言层单元测试(无需真实 PostgreSQL)。

真实 PG 行为由 tests/test_store_pg_live.py 在 CI 的 postgres 服务容器里验证。
"""
import sqlite3

from datafoundry.store import _SCHEMA, Store, pg_schema, resolve_db_url


def test_resolve_db_url_precedence(monkeypatch):
    monkeypatch.delenv("DATAFOUNDRY_DB_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert resolve_db_url() == ""
    monkeypatch.setenv("DATABASE_URL", "postgres://a/b")
    assert resolve_db_url() == "postgres://a/b"
    monkeypatch.setenv("DATAFOUNDRY_DB_URL", "postgresql://c/d")
    assert resolve_db_url() == "postgresql://c/d"  # 专属变量优先于平台注入
    assert resolve_db_url("postgres://e/f") == "postgres://e/f"  # 显式参数最高
    # 非 postgres 串(如 mysql://)不启用,回落 SQLite
    monkeypatch.setenv("DATAFOUNDRY_DB_URL", "mysql://x/y")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert resolve_db_url() == ""


def test_pg_schema_transform():
    ddl = pg_schema()
    assert "INTEGER PRIMARY KEY" not in ddl  # 自增主键全部转 BIGSERIAL
    assert "BIGSERIAL PRIMARY KEY" in ddl
    assert " REAL" not in ddl  # 时间戳列全部转 DOUBLE PRECISION
    assert "DOUBLE PRECISION" in ddl
    assert "user_id BIGINT" in ddl  # 外键列位宽与 BIGSERIAL 对齐
    # TEXT 主键表(orders/datasets/runs)不受影响
    assert ddl.count("TEXT PRIMARY KEY") == _SCHEMA.count("TEXT PRIMARY KEY")


def test_sqlite_default_and_sql_passthrough(tmp_path, monkeypatch):
    monkeypatch.delenv("DATAFOUNDRY_DB_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    st = Store(home=tmp_path)
    assert st.dialect == "sqlite"
    assert st.IntegrityError is sqlite3.IntegrityError
    assert st._sql("SELECT * FROM t WHERE a=? AND b=?") == "SELECT * FROM t WHERE a=? AND b=?"


def test_placeholder_translation_for_pg(tmp_path):
    st = Store(home=tmp_path)  # sqlite 实例,借用实例方法验证转写逻辑
    st.dialect = "postgres"
    assert st._sql("INSERT INTO t(a,b) VALUES(?,?)") == "INSERT INTO t(a,b) VALUES(%s,%s)"
    st.dialect = "sqlite"


def test_schema_has_no_question_mark_literals():
    # 方言转写用全局 replace('?','%s'):约定任何 SQL 字面量里不得出现 '?'
    assert "?" not in _SCHEMA
