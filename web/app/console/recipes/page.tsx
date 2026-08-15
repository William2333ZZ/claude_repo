import Link from "next/link";
import { apiServerStatus } from "@/lib/api-server";
import { Card } from "@/components/Card";
import type { RecipeSummary } from "@/lib/types";

// 配方索引页(30-B):列出可用配方,点进去看战绩档案。
export default async function RecipesPage() {
  const res = await apiServerStatus<RecipeSummary[]>("/recipes");

  if (res.status === 401) {
    return (
      <main>
        <Card>
          <p className="err">会话已过期,请重新登录。</p>
          <Link className="btn" href="/login?next=/console/recipes">
            重新登录
          </Link>
        </Card>
      </main>
    );
  }

  const recipes = res.data ?? [];

  return (
    <main>
      <div className="appbar">
        <Link className="brand" href="/console">
          <i />
          DataFoundry
        </Link>
        <span className="who">配方</span>
      </div>

      <Card title={`配方(${recipes.length} 个)`}>
        {recipes.length === 0 && <p className="empty">还没有配方。</p>}
        {recipes.map((r) => (
          <Link className="runrow" href={`/console/recipes/${r.name}`} key={r.name}>
            <div>
              <div>{r.title}</div>
              <div className="meta mono">
                {r.name} · {r.scenario}
              </div>
            </div>
            <span className="chip run mono">{r.hash.slice(0, 8)}</span>
          </Link>
        ))}
      </Card>
    </main>
  );
}
