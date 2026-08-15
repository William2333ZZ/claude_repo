// 死因的人话对照(老板版)——与 service/console.py GLOSS 同源同语;
// 统一语言变更必须两处同步(docs/22 纪律)。
export const GLOSS: Record<string, string> = {
  length_filter: "太短或太长,喂不进训练",
  symbol_ratio_filter: "符号杂讯占比过高(乱码/表格残渣)",
  repetition_filter: "复读机式重复(模板水文)",
  exact_dedup: "一模一样的重复",
  minhash_dedup: "换汤不换药的近似重复",
  arithmetic_consistency_verify: "解答自己把算术算错(假算式,复算必错)",
  math_answer_verify: "答案与参考答案对不上",
  quality_threshold_filter: "综合质量分不过线",
  llm_judge_filter: "AI 判审建议 + 已授权阈值裁决",
  blocklist_filter: "命中屏蔽词",
  pii_redact: "含个人敏感信息",
};

export function gloss(op: string | undefined): string {
  return (op && GLOSS[op]) || "见逐条死因";
}
