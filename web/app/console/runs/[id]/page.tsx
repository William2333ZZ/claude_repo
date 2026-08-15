import Link from "next/link";
import { notFound } from "next/navigation";
import { apiServerStatus } from "@/lib/api-server";
import { Card } from "@/components/Card";
import { DeathBar } from "@/components/DeathBar";
import { KillSample } from "@/components/KillSample";
import { deathCauses } from "@/lib/death";
import { gloss } from "@/lib/gloss";
import type { RejectSample, Run } from "@/lib/types";

// Run 详情页(30-B,docs/28 §2「死因首屏」):死因说人话,每一条被杀的都可复核。
export default async function RunDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const run = await apiServerStatus<Run>(`/runs/${id}`);

  if (run.status === 401) {
    return (
      <main>
        <Card>
          <p className="err">会话已过期,请重新登录。</p>
          <Link className="btn" href={`/login?next=${encodeURIComponent(`/console/runs/${id}`)}`}>
            重新登录
          </Link>
        </Card>
      </main>
    );
  }
  if (!run.data) notFound(); // 404 = 确实无此运行,不是"再登录一次就好"的话术(错误即导购)

  const r = run.data;
  const m = r.manifest;
  const nIn = m?.n_in ?? 0;
  const nOut = m?.n_out ?? 0;
  const killed = Math.max(nIn - nOut, 0);
  const retention = m?.retention != null ? (m.retention * 100).toFixed(1) : "-";

  let causes: ReturnType<typeof deathCauses> = [];
  let samples: RejectSample[] = [];
  let sampled = 0;
  if (killed > 0) {
    const rejects = await apiServerStatus<{ rejects: RejectSample[] }>(`/runs/${id}/rejects?n=100`);
    samples = rejects.data?.rejects ?? [];
    sampled = samples.length;
    causes = deathCauses(samples);
  }
  // n=100 是端点上限:killed 超过它时,拿到的是文件里前 N 条而非全量——
  // 占比基数必须相应换成"抽样内占比",否则会系统性低估(docs/30 §7 登记的已知取舍)。
  const isFullSample = sampled >= killed;
  const denom = isFullSample ? nIn : sampled;
  const top = causes[0]?.count ?? 1;

  return (
    <main>
      <div className="appbar">
        <Link className="brand" href="/console">
          <i />
          DataFoundry
        </Link>
        <span className="who mono">{r.id}</span>
      </div>

      <Card>
        {r.status === "succeeded" ? (
          <p className="headline num">
            这批 <b className="in">{nIn.toLocaleString()}</b> 条数据,
            <span className="live">
              {nOut.toLocaleString()} 条活了下来(留存 {retention}%)
            </span>
            ,{killed.toLocaleString()} 条被淘汰——
            <b>每一条被杀的都有可辩护的死因,任何一条都可复核。</b>
          </p>
        ) : (
          <p className="headline">
            状态:<span className="bad">{r.status}</span>
            {r.status === "failed" && <> · 失败已全额退款。错误:{r.error}</>}
          </p>
        )}
        <p className="facts mono">
          配方 hash {m?.recipe_hash ?? "-"} · 平台版本 {m?.platform_version ?? "-"} · 预估成本{" "}
          {m?.est_cost_total ?? "-"}(先算账,再干活)
        </p>
      </Card>

      {causes.length > 0 && (
        <Card title={isFullSample ? "死因分布——它们为什么被杀" : `死因分布(前 ${sampled} 条,共淘汰 ${killed} 条)`}>
          {causes.map((c) => (
            <DeathBar key={c.op} op={c.op} label={gloss(c.op)} count={c.count} denom={denom} top={top} />
          ))}
        </Card>
      )}

      {samples.length > 0 && (
        <Card title="被杀样本抽检(逐条可辩护)">
          {samples.slice(0, 5).map((s, i) => (
            <KillSample key={i} sample={s} />
          ))}
        </Card>
      )}

      {r.status === "succeeded" && (
        <Card>
          <div className="row">
            <span className="hint">幸存集 {nOut.toLocaleString()} 条,JSONL</span>
            <a className="btn" href={`/df/runs/${id}/output`}>
              下载交付
            </a>
          </div>
        </Card>
      )}
    </main>
  );
}
