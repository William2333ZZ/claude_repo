import type { RunStatus } from "@/lib/types";

const LABEL: Record<RunStatus, string> = {
  queued: "排队中",
  running: "运行中",
  succeeded: "成功",
  failed: "失败",
};
const TONE: Record<RunStatus, string> = {
  queued: "run",
  running: "run",
  succeeded: "ok",
  failed: "bad",
};

export function StatChip({ status }: { status: RunStatus }) {
  return <span className={`chip ${TONE[status]}`}>{LABEL[status]}</span>;
}
