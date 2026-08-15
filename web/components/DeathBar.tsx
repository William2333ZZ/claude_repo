// 死因分布条(展示层原件):count/denom 给百分比,count/top 给条形宽度——
// denom 由调用方决定是"占全量"还是"占抽样"(见 death.ts 头注,不在此处臆断)。
export function DeathBar({
  op,
  label,
  count,
  denom,
  top,
}: {
  op: string;
  label: string;
  count: number;
  denom: number;
  top: number;
}) {
  const pct = denom > 0 ? ((count / denom) * 100).toFixed(1) : "0.0";
  const width = Math.max((count / Math.max(top, 1)) * 100, 2);
  return (
    <div className="cause">
      <div>
        <div className="gl">{label}</div>
        <div className="op mono">{op}</div>
      </div>
      <div className="n num">
        {count.toLocaleString()}
        <small>{pct}%</small>
      </div>
      <div className="track">
        <i style={{ width: `${width}%` }} />
      </div>
    </div>
  );
}
