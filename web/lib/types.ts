// 契约层(docs/30 §3/§5):镜像后端响应形状——手镜像非生成(债务已登记,docs/30 §7)。
// 字段改名两端同 PR;来源权威永远是后端 OpenAPI(/openapi.json),本文件只是给组件用的静态快照。

export interface User {
  id: number;
  username: string;
  role: "viewer" | "engineer" | "admin";
}

export interface Order {
  id: string;
  user_id: number;
  plan: string;
  credits: number;
  amount_cents: number;
  currency: string;
  provider: string;
  provider_ref: string | null;
  status: "pending" | "paid" | "credited";
  created_at: number;
  paid_at: number | null;
}

export interface BillingMe {
  balance: number;
  orders: Order[];
}

export interface Plan {
  name: string;
  credits: number;
  amount_cents: number;
  currency: string;
}

export interface PaymentIntent {
  provider_ref?: string;
  pay_url?: string;
  code_url?: string;
  [k: string]: unknown; // 网关字段随 provider 变化(alipay/stripe),前端只取需要的键
}

export interface Dataset {
  id: string;
  name: string;
  path: string;
  n_samples: number;
  created_by: string;
  created_at: number;
}

export type RunStatus = "queued" | "running" | "succeeded" | "failed";

export interface RunSummary {
  id: string;
  name: string;
  dataset_id: string;
  status: RunStatus;
  created_by: string;
  created_at: number;
  finished_at: number | null;
}

export interface RunManifest {
  n_in: number;
  n_out: number;
  retention?: number;
  recipe_hash?: string;
  recipe_name?: string;
  platform_version?: string;
  est_cost_total?: number;
  eval?: { accuracy: number; evalset: string; [k: string]: unknown };
  [k: string]: unknown;
}

export interface Run extends RunSummary {
  steps: Array<{ op: string; params: Record<string, unknown> }>;
  manifest: RunManifest | null;
  error: string | null;
}

export interface TraceStep {
  op: string;
  detail?: string;
}

export interface RejectSample {
  id?: string;
  text: string;
  trace?: TraceStep[];
}

export interface RecipeRun {
  id: string;
  dataset_id: string;
  status: RunStatus;
  created_at: number;
  recipe_hash?: string;
  n_in?: number;
  n_out?: number;
  est_cost_total?: number;
  eval_accuracy?: number;
}

export interface ApiError {
  detail: string;
}
