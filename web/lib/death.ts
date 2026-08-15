import type { RejectSample } from "./types";

export interface DeathCause {
  op: string;
  count: number;
}

// 按最后一步裁决算子计数——语义与 kernel/audit.py 的 death_cause_stats 一致,
// 但输入来源不同:那边读全量 rejects.jsonl(服务端进程内),这里读 REST 抽样
// (前端没有文件系统访问权,§见调用方对「抽样 vs 全量」的显式标注)。
export function deathCauses(rejects: RejectSample[]): DeathCause[] {
  const counts = new Map<string, number>();
  for (const s of rejects) {
    const op = s.trace?.at(-1)?.op ?? "unknown";
    counts.set(op, (counts.get(op) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([op, count]) => ({ op, count }))
    .sort((a, b) => b.count - a.count);
}
