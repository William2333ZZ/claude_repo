import Link from "next/link";
import { apiServerStatus } from "@/lib/api-server";
import { Card } from "@/components/Card";
import type { Dataset } from "@/lib/types";

// 数据集页(30-B):只读列表,零写路径(上传仍在技能剧本侧,docs/28 §3 白名单)。
export default async function DatasetsPage() {
  const res = await apiServerStatus<Dataset[]>("/datasets");

  if (res.status === 401) {
    return (
      <main>
        <Card>
          <p className="err">会话已过期,请重新登录。</p>
          <Link className="btn" href="/login?next=/console/datasets">
            重新登录
          </Link>
        </Card>
      </main>
    );
  }

  const datasets = res.data ?? [];

  return (
    <main>
      <div className="appbar">
        <Link className="brand" href="/console">
          <i />
          DataFoundry
        </Link>
        <span className="who">数据集</span>
      </div>

      <Card title={`数据集(${datasets.length} 个)`}>
        {datasets.length === 0 && (
          <p className="empty">
            还没有数据集——去技能剧本里说一句"帮我清洗一批数据",上传会自动建一个。
          </p>
        )}
        {datasets.map((d) => (
          <div className="runrow" key={d.id}>
            <div>
              <div className="mono">{d.id}</div>
              <div className="meta">{d.name}</div>
            </div>
            <span className="chip run num">{d.n_samples.toLocaleString()} 条</span>
          </div>
        ))}
      </Card>
    </main>
  );
}
