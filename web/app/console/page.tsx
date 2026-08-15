import Link from "next/link";
import { apiServer } from "@/lib/api-server";
import { Card } from "@/components/Card";
import { StatChip } from "@/components/StatChip";
import type { BillingMe, RunSummary } from "@/lib/types";

// 总览页(30-B 起点,docs/28 §2):余额 + 最近运行 + 订单——读专用,零写路径。
// Server Component:数据在服务端取好直接渲染,组件本身零 fetch(docs/30 §3)。
export default async function ConsoleOverview() {
  const [runs, billing] = await Promise.all([
    apiServer<RunSummary[]>("/runs"),
    apiServer<BillingMe>("/billing/me"),
  ]);

  if (runs === null && billing === null) {
    // middleware 只查 cookie 在场性,真伪归后端裁决(docs/30 §3)——令牌过期落到这里
    return (
      <main>
        <Card>
          <p className="err">会话已过期,请重新登录。</p>
          <Link className="btn" href="/login?next=/console">重新登录</Link>
        </Card>
      </main>
    );
  }

  const recent = (runs || []).slice(0, 10);

  return (
    <main>
      <div className="appbar">
        <span className="brand"><i />DataFoundry</span>
        <span className="who">总览</span>
      </div>

      {billing && (
        <Card title="余额(1 条样本 = 1 条额度)">
          <div className="big num">
            {billing.balance.toLocaleString()}
            <small>条</small>
          </div>
        </Card>
      )}

      <Card title={`最近运行(${recent.length} 条)`}>
        {recent.length === 0 && (
          <p className="empty">还没有运行记录——去技能剧本里说一句"帮我清洗一批数据"。</p>
        )}
        {recent.map((r) => (
          <Link className="runrow" href={`/console/runs/${r.id}`} key={r.id}>
            <div>
              <div className="mono">{r.id}</div>
              <div className="meta">{r.name}</div>
            </div>
            <StatChip status={r.status} />
          </Link>
        ))}
      </Card>

      {billing && (
        <Card title="订单">
          {billing.orders.length === 0 && <p className="empty">还没有订单。</p>}
          {billing.orders.slice(0, 5).map((o) => (
            <div className="runrow" key={o.id}>
              <div>
                <div className="mono">{o.id}</div>
                <div className="meta">
                  {o.plan} · {(o.amount_cents / 100).toFixed(2)} {o.currency.toUpperCase()}
                </div>
              </div>
              <span className={`chip ${o.status === "pending" ? "run" : "ok"}`}>{o.status}</span>
            </div>
          ))}
        </Card>
      )}

      <footer className="brandline">DataFoundry · 原生的是认知,确定的是裁决与账本</footer>
    </main>
  );
}
