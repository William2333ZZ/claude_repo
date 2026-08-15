"""本地凭据缓存(对齐 feishu-cli 的 ~/.feishu-cli/token.json 模式)。

文件: ~/.datafoundry/credentials.json(0600),或 $DATAFOUNDRY_CONFIG_DIR/credentials.json
优先级(高到低): 显式参数 > 环境变量(DATAFOUNDRY_URL / DATAFOUNDRY_API_KEY) > 凭据文件 > 默认值
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def config_dir() -> Path:
    return Path(os.environ.get("DATAFOUNDRY_CONFIG_DIR", Path.home() / ".datafoundry"))


def credentials_path() -> Path:
    return config_dir() / "credentials.json"


def save_credentials(url: str, api_key: str, username: str, role: str, key_id: int | None = None) -> Path:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"url": url, "api_key": api_key, "username": username, "role": role, "key_id": key_id}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    os.chmod(path, 0o600)
    return path


def load_credentials() -> dict | None:
    path = credentials_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("api_key"):
        return None
    return data


def clear_credentials() -> bool:
    path = credentials_path()
    if path.is_file():
        path.unlink()
        return True
    return False
