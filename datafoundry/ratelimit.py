"""[M2-G1] 速率限制与登录失败锁定(docs/12 P1-1 整改),零新依赖。

内存实现(threading.Lock,滑动窗口),时钟可注入便于测试:
- SlidingWindow:每 key 每分钟请求数上限;超限返回需等待秒数(429 + Retry-After)
- LoginGuard:同一 (用户名, IP) 连续失败 N 次锁定 COOLDOWN 秒;锁定期内正确口令
  也拒绝(423);成功登录清零计数

已知边界(成文,docs/08 #17):多实例部署时各实例独立计数——速率上限被放大 N 倍、
锁定只在命中同一实例时生效;分布式限流待真实多实例需求触发再上(Q3 防线),
账本库(PG)不承载限流状态。

环境变量(create_app 读取):
  DATAFOUNDRY_RATE_RPM         全 API 每分钟上限,默认 240;0 = 关闭限流
  DATAFOUNDRY_LOGIN_MAX_FAILS  连续失败锁定阈值,默认 5
  DATAFOUNDRY_LOGIN_COOLDOWN   锁定秒数,默认 300
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindow:
    """每 key 在 window 秒内至多 limit 次;hit() 返回 0=放行,>0=需等待秒数。"""

    def __init__(self, limit: int, window: float = 60.0, now=time.monotonic):
        self.limit = int(limit)
        self.window = float(window)
        self._now = now
        self._lock = threading.Lock()
        self._hits: dict[str, deque] = defaultdict(deque)

    def hit(self, key: str) -> float:
        if self.limit <= 0:  # 0 = 关闭
            return 0.0
        with self._lock:
            now = self._now()
            q = self._hits[key]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) < self.limit:
                q.append(now)
                return 0.0
            return max(self.window - (now - q[0]), 0.001)


class LoginGuard:
    """登录失败锁定:连续 max_fails 次失败锁 cooldown 秒;成功清零。"""

    def __init__(self, max_fails: int = 5, cooldown: float = 300.0, now=time.monotonic):
        self.max_fails = int(max_fails)
        self.cooldown = float(cooldown)
        self._now = now
        self._lock = threading.Lock()
        self._fails: dict[str, int] = defaultdict(int)
        self._locked_until: dict[str, float] = {}

    def locked_for(self, key: str) -> float:
        """剩余锁定秒数;0 = 未锁定。锁到期自动清计数(重新给满额度)。"""
        with self._lock:
            until = self._locked_until.get(key, 0.0)
            now = self._now()
            if until > now:
                return until - now
            if key in self._locked_until:  # 锁刚过期:清状态
                del self._locked_until[key]
                self._fails.pop(key, None)
            return 0.0

    def fail(self, key: str) -> bool:
        """记一次失败;返回本次是否触发了锁定。"""
        with self._lock:
            self._fails[key] += 1
            if self._fails[key] >= self.max_fails:
                self._locked_until[key] = self._now() + self.cooldown
                return True
            return False

    def ok(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
            self._locked_until.pop(key, None)


def client_key(request) -> str:
    """限流分桶:带凭据(Bearer/API Key)按凭据散列分桶,匿名按 IP。
    XFF 取第一跳(Render 等反代注入;直连时该头可伪造——匿名桶只影响限流不影响授权)。"""
    auth = request.headers.get("authorization") or request.headers.get("x-api-key")
    if auth:
        return f"cred:{hash(auth) & 0xFFFFFFFF:x}"
    xff = request.headers.get("x-forwarded-for", "")
    ip = xff.split(",")[0].strip() if xff else (request.client.host if request.client else "unknown")
    return f"ip:{ip}"
