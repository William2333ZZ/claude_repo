import time

from datafoundry.security import (
    create_token,
    hash_api_key,
    hash_password,
    new_api_key,
    parse_token,
    role_rank,
    verify_password,
)

SECRET = b"x" * 32


def test_password_roundtrip():
    h = hash_password("correct horse battery")
    assert verify_password("correct horse battery", h)
    assert not verify_password("wrong", h)
    assert h != hash_password("correct horse battery")  # 盐随机,哈希不同


def test_token_roundtrip_and_expiry():
    token = create_token(SECRET, 1, "alice", "admin")
    payload = parse_token(SECRET, token)
    assert payload and payload["sub"] == "alice" and payload["role"] == "admin" and payload["uid"] == 1

    expired = create_token(SECRET, 1, "alice", "admin", ttl=-10)
    assert parse_token(SECRET, expired) is None


def test_token_tamper_and_wrong_secret():
    token = create_token(SECRET, 1, "alice", "viewer")
    assert parse_token(b"y" * 32, token) is None
    assert parse_token(SECRET, token[:-2] + "aa") is None
    assert parse_token(SECRET, "garbage") is None


def test_api_key_hash():
    plain, digest = new_api_key()
    assert plain.startswith("dfk_")
    assert hash_api_key(plain) == digest
    assert hash_api_key("dfk_other") != digest


def test_role_rank_order():
    assert role_rank("viewer") < role_rank("engineer") < role_rank("admin")
