"""[M1-C1a] 带噪训练语料生成器 v2(Owner 决策 2026-08-11:程序化生成,不依赖 LLM)。

v2 修订(v1 实测教训):模板参数空间过小导致语料内部大量真重复,污染真值分类。
v2 给每个模板加 人名/物品/语境 池,题面唯一空间扩大两个数量级;题面全局去重(seen 集合);
垃圾/跑题文本组合式生成保证多样性。同参数不同人名的"语义近重复"保留——那是真实语料的
自然冗余,由去重算子处置,组间公平。

meta 字段:question(训练 instruction)、answer(训练 output,含"答案: N")、
reference(正确答案,math_answer_verify 用)、_gt(隐藏真值:算子禁读,仅报告分析用)。

用法: python experiments/m1_c1/generate_corpus.py
"""
from __future__ import annotations

import json
import pathlib
import random

SEED = 20260812
N_TOTAL = 5000
RATIOS = {"clean": 0.60, "wrong_answer": 0.15, "spam": 0.08, "offtopic": 0.07, "dup": 0.05, "near_dup": 0.05}

HERE = pathlib.Path(__file__).parent
EVALSET = HERE.parent.parent / "evalsets" / "math_zh_v1.jsonl"

NAMES = ["小明", "小红", "小华", "小刚", "小丽", "小军", "小芳", "小强", "小燕", "小林",
         "小雨", "小杰", "小婷", "小凯", "小雪", "小峰", "小晴", "小龙", "小梅", "小超"]


def t_change(r):
    name, item = r.choice(NAMES), r.choice(["笔记本", "钢笔", "练习册", "文件夹", "水彩笔", "便签本"])
    a, n, ch = r.randint(3, 12), r.randint(3, 9), r.randint(1, 25)
    pay = a * n + ch
    q = f"{name}买了 {n} 本单价 {a} 元的{item},付给店员 {pay} 元。店员应找零多少元?"
    s = f"先算总价:{a}×{n}={a * n} 元。再算找零:{pay}-{a * n}={ch} 元。\n答案: {ch}"
    return q, s, ch


def t_distance(r):
    veh = r.choice(["汽车", "客车", "货车", "大巴"])
    v, t = 5 * r.randint(7, 24), r.randint(2, 7)
    q = f"一辆{veh}以每小时 {v} 千米的速度匀速行驶了 {t} 小时,共行驶了多少千米?"
    s = f"路程=速度×时间:{v}×{t}={v * t} 千米。\n答案: {v * t}"
    return q, s, v * t


def t_discount(r):
    item = r.choice(["外套", "书包", "台灯", "球鞋", "毛衣", "雨伞"])
    p, d = 10 * r.randint(6, 90), r.randint(6, 9)
    ans = p * d // 10
    q = f"一件原价 {p} 元的{item}按{d}折出售,现价是多少元?"
    s = f"{d}折即原价的 {d}/10:{p}×{d}÷10={ans} 元。\n答案: {ans}"
    return q, s, ans


def t_average(r):
    name, subj = r.choice(NAMES), r.choice(["数学", "语文", "英语", "科学"])
    m = r.randint(60, 96)
    nums = [m - 3, m + 1, m - 2, m + 4]
    q = f"{name}四次{subj}测验的成绩分别是 {nums[0]}、{nums[1]}、{nums[2]}、{nums[3]} 分,平均分是多少分?"
    s = f"四次总分:{'+'.join(map(str, nums))}={sum(nums)} 分。平均分:{sum(nums)}÷4={m} 分。\n答案: {m}"
    return q, s, m


_COWORK_PAIRS = [(a, b) for a in range(2, 41) for b in range(a, 41) if (a * b) % (a + b) == 0]


def t_cowork(r):
    task = r.choice(["一项工程", "一批零件的加工", "一面墙的粉刷", "一份资料的录入", "一块地的翻整", "一批图书的整理"])
    a, b = r.choice(_COWORK_PAIRS)
    ans = a * b // (a + b)
    q = f"{task},甲单独做需要 {a} 天完成,乙单独做需要 {b} 天完成。两人合作需要多少天完成?"
    s = (f"甲每天完成 1/{a},乙每天完成 1/{b},合作每天完成 1/{a}+1/{b}={a + b}/{a * b}。"
         f"合作天数:{a * b}÷{a + b}={ans} 天。\n答案: {ans}")
    return q, s, ans


def t_age(r):
    parent, name = r.choice(["父亲", "母亲", "叔叔", "姑姑"]), r.choice(NAMES)
    k, c = r.randint(3, 7), r.randint(5, 14)
    total = c * (k + 1)
    q = f"今年{parent}的年龄是{name}的 {k} 倍,两人年龄之和是 {total} 岁。{name}今年多少岁?"
    s = f"两人年龄和是{name}的 {k}+1={k + 1} 倍,{name}年龄:{total}÷{k + 1}={c} 岁。\n答案: {c}"
    return q, s, c


def t_chicken(r):
    variant = r.choice([("鸡", "兔", 2, 4), ("两轮摩托车", "四轮汽车", 2, 4)])
    small, big, ls, lb = variant
    nb, ns = r.randint(3, 18), r.randint(3, 18)
    h, legs = nb + ns, lb * nb + ls * ns
    unit = "只脚" if big == "兔" else "个轮子"
    place = "笼子里" if big == "兔" else "停车场里"
    q = f"{place}有若干{small}和{big},共 {h} 个,数{unit}共 {legs} 个。{big}有多少?"
    s = (f"假设全是{small},应有 {h}×{ls}={ls * h} 个,实际多出 {legs}-{ls * h}={legs - ls * h} 个,"
         f"每个{big}多 {lb - ls} 个,{big}:{legs - ls * h}÷{lb - ls}={nb}。\n答案: {nb}")
    return q, s, nb


def t_pool(r):
    box = r.choice(["水池", "鱼缸", "蓄水桶"])
    b = r.randint(2, 9)
    a = b + r.randint(2, 7)
    t = r.randint(3, 9)
    v = (a - b) * t
    q = (f"一个{box}同时打开进水管和排水管,进水管每小时进水 {a} 升,排水管每小时排水 {b} 升。"
         f"要注满 {v} 升的{box}需要多少小时?")
    s = f"每小时净进水 {a}-{b}={a - b} 升,时间:{v}÷{a - b}={t} 小时。\n答案: {t}"
    return q, s, t


def t_ratio(r):
    item = r.choice(["糖", "弹珠", "贴纸", "卡片"])
    a, b = r.choice([(1, 2), (2, 3), (3, 4), (2, 5), (3, 5), (4, 5), (1, 3), (1, 4), (3, 7), (2, 7)])
    u = r.randint(5, 30)
    total, big = (a + b) * u, max(a, b) * u
    q = f"把 {total} 颗{item}按 {a}:{b} 分给甲、乙两人,分得多的一人得到多少颗?"
    s = f"共 {a}+{b}={a + b} 份,每份 {total}÷{a + b}={u} 颗,多的一人 {max(a, b)} 份:{max(a, b)}×{u}={big} 颗。\n答案: {big}"
    return q, s, big


def t_profit(r):
    goods = r.choice(["商品", "玩具", "书包", "台灯"])
    c = r.choice([50, 80, 100, 120, 150, 200, 240, 250, 300, 400])
    rate = 5 * r.randint(1, 10)
    if c * rate % 100:
        rate = 20
    sell = c + c * rate // 100
    q = f"一件{goods}进价 {c} 元,售价 {sell} 元,利润率是百分之多少?(只答数字)"
    s = f"利润:{sell}-{c}={sell - c} 元。利润率:{sell - c}÷{c}×100={rate}。\n答案: {rate}"
    return q, s, rate


def t_arith(r):
    a1, d, n = r.randint(2, 15), r.randint(2, 7), r.randint(4, 12)
    total = n * a1 + n * (n - 1) // 2 * d
    q = f"一个等差数列首项是 {a1},公差是 {d},前 {n} 项的和是多少?"
    s = (f"末项:{a1}+({n}-1)×{d}={a1 + (n - 1) * d}。"
         f"和:({a1}+{a1 + (n - 1) * d})×{n}÷2={total}。\n答案: {total}")
    return q, s, total


def t_trees(r):
    place, thing = r.choice([("小路", "树"), ("操场边", "树"), ("河堤", "花盆")])
    i = r.choice([3, 4, 5, 6, 8, 10, 12, 15])
    k = r.randint(8, 40)
    q = f"在一条 {i * k} 米长的{place}一侧栽{thing},每隔 {i} 米栽一棵,两端都要栽,一共要栽多少棵?"
    s = f"间隔数:{i * k}÷{i}={k} 个,两端都栽要加 1:{k}+1={k + 1} 棵。\n答案: {k + 1}"
    return q, s, k + 1


TEMPLATES = [t_change, t_distance, t_discount, t_average, t_cowork, t_age,
             t_chicken, t_pool, t_ratio, t_profit, t_arith, t_trees]


def make_spam(r) -> str:
    opener = r.choice(["震惊!", "速看!", "最后一天!", "家长必看!", "限时优惠!"])
    product = r.choice(["数学提分神器", "小升初密卷", "口算训练营", "名师网课", "错题打印机"])
    action = r.choice(
        [f"点击 http://spam{r.randint(100, 999)}.example 立抢",
         f"加微信 138{r.randint(10000000, 99999999)} 领取",
         "全场三折先到先得", "评论区扣1免费送"]
    )
    tail = r.choice(["!!!$$$###@@@", "!!!///|||~~~^^^&&&", "(((%%%)))***!!!", "$$$@@@###!!!"])
    return f"{opener}{product},{action}{tail}"


def make_offtopic(r) -> str:
    return r.choice(
        [f"物业通知:下周{r.choice('一二三四五')}上午{r.randint(8, 11)}点小区停水,请提前储水。",
         f"图书馆开放时间调整为早{r.randint(7, 9)}点到晚{r.randint(8, 10)}点,借书上限提高到{r.randint(8, 15)}本。",
         f"运动会通知:各班周{r.choice('三四五')}前交报名表,项目有跳远、{r.choice(['接力', '拔河', '铅球'])}和长跑。",
         f"食堂本周新增{r.choice(['番茄牛腩', '香菇滑鸡', '麻婆豆腐'])}窗口,营业到晚上{r.randint(7, 9)}点。"]
    )


def _mk(question, answer_text, reference, gt):
    return {"text": f"题目:{question}\n解答:{answer_text}", "question": question,
            "answer": answer_text, "reference": str(reference), "_gt": gt}


def generate() -> list[dict]:
    rng = random.Random(SEED)
    seen = {json.loads(l)["question"] for l in EVALSET.read_text(encoding="utf-8").splitlines() if l.strip()}
    counts = {k: int(N_TOTAL * v) for k, v in RATIOS.items()}
    counts["clean"] += N_TOTAL - sum(counts.values())

    def fresh_item():
        for _ in range(300):
            q, s, ans = rng.choice(TEMPLATES)(rng)
            if q not in seen:
                seen.add(q)
                return q, s, ans
        raise RuntimeError("题面空间耗尽,扩大模板参数池")

    corpus, clean_bank = [], []
    for _ in range(counts["clean"]):
        q, s, ans = fresh_item()
        item = _mk(q, s, ans, "clean")
        corpus.append(item)
        clean_bank.append(item)
    for _ in range(counts["wrong_answer"]):  # 计算滑坡:步骤看似合理,最终答案偏移
        q, s, ans = fresh_item()
        wrong = ans + rng.choice([-2, -1, 1, 2, 10])
        corpus.append(_mk(q, s.replace(f"答案: {ans}", f"答案: {wrong}"), ans, "wrong_answer"))
    for _ in range(counts["spam"]):
        q, _, ans = fresh_item()
        corpus.append(_mk(q, make_spam(rng), ans, "spam"))
    for _ in range(counts["offtopic"]):
        q, _, ans = fresh_item()
        corpus.append(_mk(q, make_offtopic(rng), ans, "offtopic"))
    for _ in range(counts["dup"]):
        corpus.append({**rng.choice(clean_bank), "_gt": "dup"})
    for _ in range(counts["near_dup"]):
        base = rng.choice(clean_bank)
        text = base["text"]
        for old in NAMES:
            if old in text:
                new = rng.choice([n for n in NAMES if n != old])
                text = text.replace(old, new)
                break
        else:
            text = text.replace("题目:", "题目:请看,", 1)
        corpus.append({**base, "text": text, "_gt": "near_dup"})
    rng.shuffle(corpus)
    return corpus


if __name__ == "__main__":
    corpus = generate()
    out = HERE / "corpus_raw.jsonl"
    with open(out, "w", encoding="utf-8") as fh:
        for c in corpus:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    from collections import Counter
    print(f"wrote {len(corpus)} -> {out}")
    print("gt 分布:", dict(Counter(c["_gt"] for c in corpus)))
    uniq = len({c["question"] for c in corpus})
    print(f"唯一题面: {uniq}")
