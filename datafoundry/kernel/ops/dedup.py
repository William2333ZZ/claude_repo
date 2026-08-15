"""语料级去重算子(自研实现,零外部依赖)。

- exact_dedup   : 归一化文本指纹精确去重
- minhash_dedup : MinHash + LSH 近重复去重(字符 5-gram shingle,128 个哈希,16 band × 8 row,
                  近似阈值 ~0.75 Jaccard;候选对经 union-find 聚簇,每簇保留首个)
v0 为单机内存实现;Ray 分片版是后续里程碑,接口保持不变。
"""
from __future__ import annotations

import hashlib
import re
import struct
from typing import Iterable, Iterator

from datafoundry.kernel.registry import Op, register
from datafoundry.kernel.schema import add_trace

_norm_ws = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _norm_ws.sub(" ", text.lower()).strip()


@register
class ExactDedup(Op):
    name = "exact_dedup"
    kind = "dedup"
    cost_tier = "heuristic"
    cost_per_1k = 0.0005
    expected_retention = 0.85
    description = "归一化(小写+空白折叠)后按 blake2b 指纹精确去重,保留首个"

    def __init__(self):
        super().__init__()
        self._seen: set[bytes] = set()  # 实例级:流式分块下跨块存活([M2-E2])

    def process(self, sample):  # pragma: no cover - 语料级算子走 process_batch
        return sample

    def process_batch(self, samples: Iterable[dict]) -> Iterator[dict | None]:
        for s in samples:
            fp = hashlib.blake2b(_normalize(s["text"]).encode("utf-8"), digest_size=16).digest()
            if fp in self._seen:
                add_trace(s, self.name, "kill", "exact duplicate")
                yield None
                continue
            self._seen.add(fp)
            add_trace(s, self.name, "pass")
            yield s


class _UnionFind:
    def __init__(self):
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


@register
class MinHashDedup(Op):
    name = "minhash_dedup"
    kind = "dedup"
    cost_tier = "heuristic"
    cost_per_1k = 0.005
    expected_retention = 0.8
    description = "MinHash+LSH 近重复去重(默认 128 哈希/16 band,阈值~0.75 Jaccard),每簇保留首个"

    _MERSENNE = (1 << 61) - 1

    def __init__(self, num_perm: int = 128, bands: int = 16, shingle: int = 5):
        super().__init__(num_perm=num_perm, bands=bands, shingle=shingle)
        assert num_perm % bands == 0, "num_perm 必须能被 bands 整除"
        self.num_perm, self.bands, self.shingle = num_perm, bands, shingle
        self.rows = num_perm // bands
        ints = self._rand_ints(2 * num_perm)
        self._a = [(v % (self._MERSENNE - 1)) + 1 for v in ints[:num_perm]]
        self._b = [v % self._MERSENNE for v in ints[num_perm:]]
        # 实例级增量状态(流式分块下跨块存活,[M2-E2])
        self._uf = _UnionFind()
        self._buckets: dict[tuple[int, bytes], int] = {}
        self._next_idx = 0

    @staticmethod
    def _rand_ints(count: int) -> list[int]:
        """确定性伪随机 64 位整数序列(固定种子,保证跨进程签名一致)。"""
        out: list[int] = []
        block_idx = 0
        while len(out) < count:
            block = hashlib.blake2b(f"datafoundry-minhash-{block_idx}".encode(), digest_size=64).digest()
            out.extend(struct.unpack("<8Q", block))
            block_idx += 1
        return out[:count]

    def _signature(self, text: str) -> list[int] | None:
        norm = _normalize(text)
        if len(norm) < self.shingle:
            return None
        shingles = {
            int.from_bytes(
                hashlib.blake2b(norm[i : i + self.shingle].encode("utf-8"), digest_size=8).digest(),
                "little",
            )
            for i in range(len(norm) - self.shingle + 1)
        }
        return [
            min((a * sh + b) % self._MERSENNE for sh in shingles)
            for a, b in zip(self._a, self._b)
        ]

    def process(self, sample):  # pragma: no cover - 语料级算子走 process_batch
        return sample

    # [M2-E2] 增量语义:样本到达即判——任一 band 撞上已见桶即近重(该桶簇首必已保留)。
    # 与旧批式两遍聚簇的差异:晚到样本把两个既有簇传递合并时,旧版会追溯划簇、
    # 本版两簇首都已保留(合并只影响后续)。增量语义是分片执行器(#14)的前提,
    # 且使去重结果与 chunk 大小严格无关。
    def process_batch(self, samples: Iterable[dict]) -> Iterator[dict | None]:
        for s in samples:
            sig = self._signature(s["text"])
            if sig is None:  # 太短没法算签名,放行
                add_trace(s, self.name, "pass", "too short for signature")
                yield s
                continue
            idx = self._next_idx
            self._next_idx += 1
            matched = False
            for band in range(self.bands):
                chunk = sig[band * self.rows : (band + 1) * self.rows]
                key = (band, hashlib.blake2b(struct.pack(f"<{self.rows}Q", *chunk), digest_size=8).digest())
                if key in self._buckets:
                    self._uf.union(self._buckets[key], idx)
                    matched = True
                else:
                    self._buckets[key] = idx
            if matched:
                add_trace(s, self.name, "kill", f"near-dup of cluster {self._uf.find(idx)}")
                yield None
            else:
                add_trace(s, self.name, "pass")
                yield s
