import { gloss } from "@/lib/gloss";
import type { RejectSample } from "@/lib/types";

// 被杀样本抽检卡:死因说人话,原文截断展示——每一条都可复核(docs/22 统一语言纪律)。
export function KillSample({ sample }: { sample: RejectSample }) {
  const step = sample.trace?.at(-1);
  return (
    <div className="kill-sample">
      <b>{gloss(step?.op)}</b>
      {step?.detail && <span> · {step.detail}</span>}
      <br />
      <span className="quote">{(sample.text || "").slice(0, 160)}</span>
    </div>
  );
}
