"""[M2-E2] 流式 I/O 测试(#13):分块不变性(核心等价主张)与内存平坦(缩尺版)。"""
import hashlib
import json
import subprocess
import sys
import textwrap

import pytest

import datafoundry.kernel.ops  # noqa: F401  导入即注册
from datafoundry.kernel.runner import run_pipeline

STEPS = [
    {"op": "length_filter", "params": {"min_len": 5}},
    {"op": "exact_dedup"},
    {"op": "repetition_filter"},
]


def _write_corpus(path, n):
    with open(path, "w", encoding="utf-8") as fh:
        for i in range(n):
            if i % 7 == 0:  # 重复样本(考验跨块去重状态)
                text = "重复样本文本,内容完全一致,用于测试去重算子的跨块状态。"
            elif i % 11 == 0:
                text = "短"  # 会被长度过滤杀
            else:
                text = f"这是第 {i} 条正常样本,含有足够长度的正文内容以通过过滤,编号 {i}。"
            fh.write(json.dumps({"text": text, "meta": {"i": i}}, ensure_ascii=False) + "\n")


def _digest(p, ordered=True):
    """内容指纹:剔除 id(带 secrets 随机后缀,任意两次运行必不同,与分块无关)后规范化哈希。
    ordered=False 按多重集比较——rejects 是无序审计袋(写入交错随分块变化,集合不变)。"""
    lines = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            obj = json.loads(line)
            obj.pop("id", None)
            lines.append(json.dumps(obj, ensure_ascii=False, sort_keys=True))
    if not ordered:
        lines.sort()
    h = hashlib.sha256()
    for ln in lines:
        h.update(ln.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def test_chunk_invariance_outputs_identical(tmp_path):
    """核心等价主张:chunk=37(多块,故意非整除)与 chunk=10^9(单块=全量)内容逐条一致。"""
    corpus = tmp_path / "c.jsonl"
    _write_corpus(corpus, 2_000)
    m_small = run_pipeline(corpus, STEPS, tmp_path / "small", chunk_size=37)
    m_full = run_pipeline(corpus, STEPS, tmp_path / "full", chunk_size=10**9)

    assert _digest(tmp_path / "small/output.jsonl") == _digest(tmp_path / "full/output.jsonl")  # 幸存者保序一致
    assert _digest(tmp_path / "small/rejects.jsonl", ordered=False) == \
           _digest(tmp_path / "full/rejects.jsonl", ordered=False)  # 死因袋按多重集一致
    for key in ("n_in", "n_out", "retention", "est_cost_total"):
        assert m_small[key] == m_full[key]
    for a, b in zip(m_small["per_op"], m_full["per_op"]):
        assert {k: a[k] for k in ("op", "in", "out", "killed", "retention")} == \
               {k: b[k] for k in ("op", "in", "out", "killed", "retention")}


def test_dedup_state_survives_chunk_boundary(tmp_path):
    corpus = tmp_path / "c.jsonl"
    with open(corpus, "w", encoding="utf-8") as fh:
        for i in range(10):  # 同文本 10 条,chunk=3 使其散布在 4 个块里
            fh.write(json.dumps({"text": "完全相同的文本内容,跨块出现,应只保留第一条。"}) + "\n")
    m = run_pipeline(corpus, [{"op": "exact_dedup"}], tmp_path / "out", chunk_size=3)
    assert (m["n_in"], m["n_out"]) == (10, 1)


def test_skipped_lines_reported_in_manifest(tmp_path):
    corpus = tmp_path / "c.jsonl"
    corpus.write_text('{"text": "有效样本,长度足够通过一切过滤器,正文正文正文。"}\n{broken json\n\n', encoding="utf-8")
    m = run_pipeline(corpus, [{"op": "exact_dedup"}], tmp_path / "out", chunk_size=10)
    assert m["skipped_lines"] == 1 and m["n_in"] == 1


_MEM_SCRIPT = textwrap.dedent("""
    import json, resource, sys, tempfile, pathlib
    import datafoundry.kernel.ops  # noqa
    from datafoundry.kernel.runner import run_pipeline
    n = int(sys.argv[1])
    d = pathlib.Path(tempfile.mkdtemp())
    with open(d / "c.jsonl", "w", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({"text": f"内存平坦性测试样本编号 {i},正文填充至足够长度以模拟真实负载。" * 3}) + "\\n")
    run_pipeline(d / "c.jsonl", [{"op": "length_filter", "params": {"min_len": 5}},
                                 {"op": "exact_dedup"}], d / "out", chunk_size=1000)
    print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
""")


@pytest.mark.slow
def test_memory_flat_across_dataset_sizes(tmp_path):
    """验收缩尺版:2 万 vs 6 万样本(3×数据),峰值 RSS 增幅 < 60%(全量载入约为 ~3×)。"""
    def peak(n):
        r = subprocess.run([sys.executable, "-c", _MEM_SCRIPT, str(n)],
                           capture_output=True, text=True, check=True)
        return int(r.stdout.strip())

    small, big = peak(20_000), peak(60_000)
    assert big < small * 1.6, f"内存未解耦: 2万={small} 6万={big}"
