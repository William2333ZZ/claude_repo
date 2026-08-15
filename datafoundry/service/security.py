"""认证原语:基于成熟开源组件,不自研密码学。

- 口令哈希 : argon2-cffi(Argon2id,OWASP 首选;哈希串自带盐与参数,支持参数升级 rehash)
- 会话令牌 : PyJWT(HS256,服务端随机密钥签发,带 exp/jti;密钥文件 0600)
- API Key  : `dfk_` 前缀 + 32 字节随机;库中只存 SHA-256 哈希,明文只在创建时展示一次
- RBAC     : viewer(0) < engineer(1) < admin(2)

升级路径(docs/05):对接企业 SSO 用 OIDC(Keycloak / Casdoor 等开源 IdP);
资源级授权引入 pycasbin。诚实的边界:v0 无速率限制、无账号锁定,TLS 交给反代。
"""
from __future__ import annotations

import hashlib
import os
import secrets
import time
from pathlib import Path

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

ROLES = {"viewer": 0, "engineer": 1, "admin": 2}
TOKEN_TTL_SECONDS = 12 * 3600
_JWT_ALG = "HS256"

_hasher = PasswordHasher()  # Argon2id,库默认参数(随版本跟进 OWASP 建议)


def role_rank(role: str) -> int:
    if role not in ROLES:
        raise ValueError(f"未知角色: {role}")
    return ROLES[role]


# ---------- 口令(argon2-cffi) ----------

def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, pw_hash: str) -> bool:
    try:
        _hasher.verify(pw_hash, password)
        return True
    except VerifyMismatchError:
        return False
    except Exception:  # 损坏的哈希串等,一律视为不通过
        return False


def password_needs_rehash(pw_hash: str) -> bool:
    return _hasher.check_needs_rehash(pw_hash)


# ---------- 服务端密钥 ----------

def load_or_create_secret(data_dir: Path) -> bytes:
    path = data_dir / "secret.key"
    if path.exists():
        return path.read_bytes()
    data_dir.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(secret)
    return secret


# ---------- 会话令牌(PyJWT) ----------

def create_token(secret: bytes, user_id: int, username: str, role: str, ttl: int = TOKEN_TTL_SECONDS) -> str:
    now = int(time.time())
    return jwt.encode(
        {"uid": user_id, "sub": username, "role": role, "iat": now, "exp": now + ttl, "jti": secrets.token_hex(8)},
        secret,
        algorithm=_JWT_ALG,
    )


def parse_token(secret: bytes, token: str) -> dict | None:
    """合法且未过期返回 payload,否则 None。"""
    try:
        payload = jwt.decode(token, secret, algorithms=[_JWT_ALG], options={"require": ["exp", "sub"]})
    except jwt.InvalidTokenError:
        return None
    if payload.get("role") not in ROLES or not isinstance(payload.get("uid"), int):
        return None
    return payload


def payload_to_user(payload: dict) -> dict:
    return {"id": payload["uid"], "username": payload["sub"], "role": payload["role"]}


def user_from_session_cookie(secret: bytes, cookie_value: str | None) -> dict | None:
    """df_session cookie → user,浏览器同源请求的认证桥(docs/30 §3 修订)。
    与 Bearer 令牌同源同签(create_token 签发),只是载体从 header 换成 cookie。"""
    if not cookie_value:
        return None
    payload = parse_token(secret, cookie_value)
    return payload_to_user(payload) if payload else None


# ---------- API Key ----------

def new_api_key() -> tuple[str, str]:
    """返回 (明文, 哈希)。明文只此一次,库中只存哈希。"""
    plain = "dfk_" + secrets.token_urlsafe(32)
    return plain, hashlib.sha256(plain.encode("ascii")).hexdigest()


def hash_api_key(plain: str) -> str:
    return hashlib.sha256(plain.encode("ascii")).hexdigest()
