"""只读控制台(#19;docs/26 定位:只读证据页,"转发给老板"用)。

三页服务端渲染,零 JS 框架、零外链:/console(数据集+运行)、/console/runs/{id}
(死因首屏)、/console/recipes/{name}(配方档案)。认证走 HTTP Basic(浏览器原生,
零会话设施,直连 RBAC;HTTPS 下可接受)。文案纪律:docs/14 §11.1 老板版——每个数字
配一句人话;死因说"可辩护的理由",不说"质量低"。控制台是第四投影,**永不长出写路径**。
"""
from __future__ import annotations

import base64
import html
import json
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from datafoundry.kernel.audit import death_cause_stats

# 死因的人话对照(老板版):算子名 → 一句谁都能懂的话
GLOSS = {
    "length_filter": "太短或太长,喂不进训练",
    "symbol_ratio_filter": "符号杂讯占比过高(乱码/表格残渣)",
    "repetition_filter": "复读机式重复(模板水文)",
    "exact_dedup": "一模一样的重复",
    "minhash_dedup": "换汤不换药的近似重复",
    "arithmetic_consistency_verify": "解答自己把算术算错(假算式,复算必错)",
    "math_answer_verify": "答案与参考答案对不上",
    "quality_threshold_filter": "综合质量分不过线",
    "llm_judge_filter": "AI 判审建议 + 已授权阈值裁决",
    "blocklist_filter": "命中屏蔽词",
    "pii_redact": "含个人敏感信息",
}

_CSS = """<style>
body{font-family:system-ui,-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;
  margin:0;background:#f6f7f9;color:#1a1d21}
header{background:#101418;color:#fff;padding:14px 28px;font-size:15px}
header a{color:#9dc8ff;text-decoration:none;margin-right:18px}
main{max-width:960px;margin:24px auto;padding:0 16px}
h1{font-size:22px;margin:8px 0 4px}h2{font-size:16px;margin:26px 0 8px}
.sub{color:#5a6572;font-size:13px;margin-bottom:18px}
.card{background:#fff;border:1px solid #e3e7ec;border-radius:10px;padding:18px 20px;margin:14px 0}
.big{font-size:30px;font-weight:700}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;color:#5a6572;font-weight:600;padding:6px 8px;border-bottom:1px solid #e3e7ec}
td{padding:7px 8px;border-bottom:1px solid #f0f2f5;vertical-align:top}
a{color:#0b62d6}
.bar{background:#e8edf3;border-radius:4px;height:14px;overflow:hidden}
.bar>i{display:block;height:100%;background:#c2483c}
.gloss{color:#5a6572;font-size:12.5px}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px}
.kill{background:#fbf3f2;border-left:3px solid #c2483c;padding:8px 12px;margin:8px 0;
  border-radius:0 6px 6px 0;font-size:13px}
.ok{color:#1d7a3d}.bad{color:#c2483c}
footer{color:#8a94a0;font-size:12px;margin:30px 0;text-align:center}
</style>"""


def _page(title: str, body: str) -> str:
    return (f'<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(title)} · DataFoundry</title>{_CSS}</head><body>"
            f'<header><a href="/console">DataFoundry 控制台</a>'
            f'<span style="color:#67707a">只读证据页 · 每一条数据的去留都有可辩护的理由</span></header>'
            f"<main>{body}</main><footer>DataFoundry · 原生的是认知,确定的是裁决与账本</footer></body></html>")


def _e(v) -> str:
    return html.escape(str(v if v is not None else ""))


def _ts(t) -> str:
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(t)))
    except (TypeError, ValueError):
        return "-"


def build_router(store) -> APIRouter:
    router = APIRouter()

    def console_user(request: Request) -> dict:
        hdr = request.headers.get("authorization", "")
        if hdr.lower().startswith("basic "):
            try:
                username, _, password = base64.b64decode(hdr[6:]).decode("utf-8").partition(":")
                user = store.authenticate(username, password)
                if user:
                    return user
            except Exception:  # 编码/格式异常一律按未认证处理
                pass
        raise HTTPException(401, "控制台需要登录(任意平台账号)",
                            headers={"WWW-Authenticate": 'Basic realm="DataFoundry Console"'})

    @router.get("/console", response_class=HTMLResponse)
    def console_index(request: Request):
        console_user(request)
        runs = store.list_runs()[:50]
        datasets = store.list_datasets()[:50]
        run_rows = "".join(
            f'<tr><td><a href="/console/runs/{_e(r["id"])}" class="mono">{_e(r["id"])}</a></td>'
            f'<td>{_e(r.get("name"))}</td><td class="mono">{_e(r.get("dataset_id"))}</td>'
            f'<td class="{"ok" if r.get("status") == "succeeded" else ("bad" if r.get("status") == "failed" else "")}">'
            f'{_e(r.get("status"))}</td><td>{_ts(r.get("created_at"))}</td></tr>'
            for r in runs) or '<tr><td colspan="5">还没有运行记录</td></tr>'
        ds_rows = "".join(
            f'<tr><td class="mono">{_e(d.get("id"))}</td><td>{_e(d.get("name"))}</td>'
            f'<td>{_e(d.get("n_samples"))}</td><td>{_ts(d.get("created_at"))}</td></tr>'
            for d in datasets) or '<tr><td colspan="4">还没有数据集</td></tr>'
        body = (
            "<h1>运行与数据集</h1>"
            '<div class="sub">点开任意运行,看它杀掉了什么、为什么——这是给能拍板的人看的页面。</div>'
            f'<div class="card"><h2 style="margin-top:0">运行(最近 {len(runs)} 条)</h2>'
            f"<table><tr><th>运行</th><th>名称</th><th>数据集</th><th>状态</th><th>时间</th></tr>{run_rows}</table></div>"
            f'<div class="card"><h2 style="margin-top:0">数据集</h2>'
            f"<table><tr><th>ID</th><th>名称</th><th>样本数</th><th>时间</th></tr>{ds_rows}</table></div>")
        return _page("控制台", body)

    @router.get("/console/runs/{run_id}", response_class=HTMLResponse)
    def console_run(run_id: str, request: Request):
        console_user(request)
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(404, "无此运行")
        m = run.get("manifest") or {}
        n_in, n_out = m.get("n_in", 0), m.get("n_out", 0)
        killed = max(n_in - n_out, 0)
        retention = f"{m.get('retention', 0) * 100:.1f}" if m.get("retention") is not None else "-"
        # 死因首屏:分布 + 人话
        rejects_path = store.runs_dir / run_id / "rejects.jsonl"
        bars = ""
        if rejects_path.exists() and killed:
            causes = death_cause_stats(rejects_path).most_common(10)
            top = causes[0][1] if causes else 1
            bars = "".join(
                f'<tr><td class="mono">{_e(op)}</td>'
                f'<td style="width:45%"><div class="bar"><i style="width:{max(cnt / top * 100, 2):.0f}%"></i></div></td>'
                f'<td>{cnt} 条({cnt / max(n_in, 1) * 100:.1f}%)</td>'
                f'<td class="gloss">{_e(GLOSS.get(op, "见逐条死因"))}</td></tr>'
                for op, cnt in causes)
        samples = ""
        if rejects_path.exists():
            picked = []
            with open(rejects_path, encoding="utf-8") as fh:
                for line in fh:
                    if len(picked) >= 5:
                        break
                    try:
                        picked.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            samples = "".join(
                f'<div class="kill"><b>{_e((s.get("trace") or [{}])[-1].get("op"))}</b> · '
                f'{_e((s.get("trace") or [{}])[-1].get("detail"))}<br>'
                f'<span class="gloss">{_e(s.get("text", "")[:160])}</span></div>'
                for s in picked)
        status = run.get("status")
        headline = (f'这批 <b>{n_in:,}</b> 条数据,<span class="ok"><b>{n_out:,}</b> 条活了下来'
                    f"(留存 {retention}%)</span>,{killed:,} 条被淘汰——"
                    "<b>每一条被杀的都有可辩护的死因,任何一条都可复核。</b>"
                    if status == "succeeded" else
                    f'状态:<span class="bad">{_e(status)}</span>'
                    + (f' · 失败已全额退款。错误:{_e(run.get("error"))}' if status == "failed" else ""))
        facts = (f'配方 hash <span class="mono">{_e(m.get("recipe_hash", "-"))}</span> · '
                 f'平台版本 <span class="mono">{_e(m.get("platform_version", "-"))}</span> · '
                 f'预估成本 {_e(m.get("est_cost_total", "-"))}(先算账,再干活)')
        body = (
            f'<h1>运行 <span class="mono">{_e(run_id)}</span></h1>'
            f'<div class="sub">{facts}</div>'
            f'<div class="card"><div class="big">{headline}</div></div>'
            + (f'<div class="card"><h2 style="margin-top:0">死因分布(它们为什么被杀)</h2>'
               f"<table><tr><th>裁决算子</th><th>占比</th><th>数量</th><th>人话</th></tr>{bars}</table></div>" if bars else "")
            + (f'<div class="card"><h2 style="margin-top:0">被杀样本抽检(逐条可辩护)</h2>{samples}</div>' if samples else ""))
        return _page(f"运行 {run_id}", body)

    @router.get("/console/recipes/{name}", response_class=HTMLResponse)
    def console_recipe(name: str, request: Request):
        console_user(request)
        history = store.recipe_history(name)
        rows = "".join(
            f'<tr><td><a href="/console/runs/{_e(h["run_id"])}" class="mono">{_e(h["run_id"])}</a></td>'
            f'<td class="mono">{_e(h.get("recipe_hash", "-"))}</td><td>{_e(h.get("status"))}</td>'
            f'<td>{_e(h.get("n_in", "-"))}→{_e(h.get("n_out", "-"))}</td>'
            f'<td>{_e(h.get("est_cost_total", "-"))}</td>'
            f'<td>{_e(h.get("eval_accuracy", "-"))}</td><td>{_ts(h.get("created_at"))}</td></tr>'
            for h in history) or '<tr><td colspan="7">该配方还没有运行档案</td></tr>'
        body = (
            f"<h1>配方档案 · {_e(name)}</h1>"
            '<div class="sub">同一配方的历史战绩:留存、成本、评测分——选配方看证据,不看感觉。</div>'
            f'<div class="card"><table><tr><th>运行</th><th>配方版本</th><th>状态</th>'
            f"<th>进→出</th><th>成本</th><th>评测分</th><th>时间</th></tr>{rows}</table></div>")
        return _page(f"配方 {name}", body)

    return router
