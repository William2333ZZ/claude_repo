"""[M1-A1] 中文数学应用题评测集生成器 v1。

设计原则:
- 12 个参数化模板 × 5 实例 = 60 题;答案由代码计算,从构造上保证正确(零答案错误)
- 固定种子,任何机器重跑逐字节一致(评测集可审计、可复现)
- 全部整数答案 -> 判分为精确匹配,全自动、零波动来源

用法: python evalsets/generate_math_zh.py  (在仓库根目录执行,产出 evalsets/math_zh_v1.jsonl)
"""
from __future__ import annotations

import json
import pathlib
import random

SEED = 20260811
PER_TEMPLATE = 5


def t01_change(r: random.Random) -> tuple[str, int]:
    a, n, change = r.randint(3, 9), r.randint(3, 8), r.randint(1, 20)
    pay = a * n + change
    return f"小明买了 {n} 本单价 {a} 元的笔记本,付给店员 {pay} 元。店员应找零多少元?", change


def t02_distance(r: random.Random) -> tuple[str, int]:
    v, t = r.choice([40, 55, 60, 72, 80, 90]), r.randint(2, 6)
    return f"一辆汽车以每小时 {v} 千米的速度匀速行驶了 {t} 小时,共行驶了多少千米?", v * t


def t03_discount(r: random.Random) -> tuple[str, int]:
    p, d = r.choice([120, 200, 250, 300, 480, 600]), r.randint(6, 9)
    return f"一件原价 {p} 元的外套按{d}折出售,现价是多少元?", p * d // 10


def t04_average(r: random.Random) -> tuple[str, int]:
    m = r.randint(60, 95)
    nums = [m - 3, m + 1, m - 2, m + 4]
    return (
        f"小红四次数学测验的成绩分别是 {nums[0]}、{nums[1]}、{nums[2]}、{nums[3]} 分,平均分是多少分?",
        m,
    )


def t05_cowork(r: random.Random) -> tuple[str, int]:
    a, b = r.choice([(6, 3), (12, 4), (20, 5), (30, 6), (12, 6), (10, 10), (8, 8)])
    ans = a * b // (a + b)
    return f"一项工程,甲单独做需要 {a} 天完成,乙单独做需要 {b} 天完成。两人合作需要多少天完成?", ans


def t06_age(r: random.Random) -> tuple[str, int]:
    k, c = r.randint(3, 6), r.randint(6, 12)
    s = c * (k + 1)
    return f"今年父亲的年龄是小华的 {k} 倍,两人年龄之和是 {s} 岁。小华今年多少岁?", c


def t07_chicken_rabbit(r: random.Random) -> tuple[str, int]:
    rabbits, chickens = r.randint(3, 12), r.randint(3, 12)
    h, legs = rabbits + chickens, 4 * rabbits + 2 * chickens
    return f"笼子里有若干只鸡和兔,从上面数有 {h} 个头,从下面数有 {legs} 只脚。兔有多少只?", rabbits


def t08_fill_pool(r: random.Random) -> tuple[str, int]:
    b = r.randint(2, 8)
    a = b + r.randint(2, 6)
    t = r.randint(3, 8)
    v = (a - b) * t
    return (
        f"一个水池同时打开进水管和排水管,进水管每小时进水 {a} 升,排水管每小时排水 {b} 升。"
        f"要注满 {v} 升的水池需要多少小时?",
        t,
    )


def t09_ratio_share(r: random.Random) -> tuple[str, int]:
    a, b = r.choice([(2, 3), (3, 4), (2, 5), (3, 5), (4, 5), (1, 3)])
    u = r.randint(6, 20)
    total = (a + b) * u
    big = max(a, b) * u
    return f"把 {total} 颗糖按 {a}:{b} 分给甲、乙两人,分得多的一人得到多少颗?", big


def t10_profit(r: random.Random) -> tuple[str, int]:
    c = r.choice([50, 100, 200, 250, 400])
    rate = r.choice([10, 15, 20, 25, 30, 40])
    s = c * (100 + rate) // 100
    return f"一件商品进价 {c} 元,售价 {s} 元,利润率是百分之多少?(只答数字)", rate


def t11_arith_sum(r: random.Random) -> tuple[str, int]:
    a1, d, n = r.randint(2, 9), r.randint(2, 5), r.randint(5, 10)
    total = n * a1 + n * (n - 1) // 2 * d
    return f"一个等差数列首项是 {a1},公差是 {d},前 {n} 项的和是多少?", total


def t12_trees(r: random.Random) -> tuple[str, int]:
    i = r.choice([4, 5, 6, 8, 10])
    k = r.randint(8, 25)
    length = i * k
    return f"在一条 {length} 米长的小路一侧植树,每隔 {i} 米栽一棵,两端都要栽,一共要栽多少棵树?", k


TEMPLATES = [
    ("change", t01_change), ("distance", t02_distance), ("discount", t03_discount),
    ("average", t04_average), ("cowork", t05_cowork), ("age", t06_age),
    ("chicken_rabbit", t07_chicken_rabbit), ("fill_pool", t08_fill_pool),
    ("ratio_share", t09_ratio_share), ("profit", t10_profit),
    ("arith_sum", t11_arith_sum), ("trees", t12_trees),
]


def generate() -> list[dict]:
    rng = random.Random(SEED)
    items, seen = [], set()
    for tname, fn in TEMPLATES:
        made = 0
        while made < PER_TEMPLATE:
            q, ans = fn(rng)
            if q in seen:  # 参数撞车则重抽,保证 60 题唯一
                continue
            seen.add(q)
            items.append({"id": f"math_zh_v1-{tname}-{made + 1}", "template": tname, "question": q, "answer": int(ans)})
            made += 1
    return items


if __name__ == "__main__":
    items = generate()
    out = pathlib.Path(__file__).parent / "math_zh_v1.jsonl"
    with open(out, "w", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")
    print(f"wrote {len(items)} items -> {out}")
