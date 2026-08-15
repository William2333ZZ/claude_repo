import Link from "next/link";
import { notFound } from "next/navigation";
import { apiServerStatus } from "@/lib/api-server";
import { Card } from "@/components/Card";
import { StatChip } from "@/components/StatChip";
import type { RecipeHistory } from "@/lib/types";

// 配方档案页(30-B):同一配方的历史战绩——留存、成本、评测分,选配方看证据不看感觉。
export default async function RecipeHistoryPage({ params }: { params: Promise<{ name: string }> }) {
  const { name } = await params;
  const res = await apiServerStatus<RecipeHistory>(`/recipes/${name}/history`);

  if (res.status === 401) {
    return (
      <main>
        <Card>
          <p className="err">会话已过期,请重新登录。</p>
          <Link className="btn" href={`/login?next=${encodeURIComponent(`/console/recipes/${name}`)}`}>
            重新登录
          </Link>
        </Card>
      </main>
    );
  }
  if (!res.data) notFound();

  const { current_hash, runs } = res.data;

  return (
    <main>
      <div className="appbar">
        <Link className="brand" href="/console">
          <i />
          DataFoundry
        </Link>
        <span className="who mono">{name}</span>
      </div>

      <Card>
        <p className="hint">
          当前配方 hash <span className="mono">{current_hash.slice(0, 12)}</span> ·
          同一配方的历史运行,hash 变了就是版本演化(选配方看证据,不看感觉)
        </p>
      </Card>

      <Card title={`运行记录(${runs.length} 条)`}>
        {runs.length === 0 && <p className="empty">该配方还没有运行档案。</p>}
        {runs.map((r) => (
          <Link className="runrow" href={`/console/runs/${r.run_id}`} key={r.run_id}>
            <div>
              <div className="mono">{r.run_id}</div>
              <div className="meta num">
                {r.n_in ?? "-"}→{r.n_out ?? "-"}
                {r.retention != null && ` · 留存 ${(r.retention * 100).toFixed(1)}%`}
                {r.eval_accuracy != null && ` · 评测 ${(r.eval_accuracy * 100).toFixed(1)}%`}
              </div>
            </div>
            <StatChip status={r.status} />
          </Link>
        ))}
      </Card>
    </main>
  );
}
