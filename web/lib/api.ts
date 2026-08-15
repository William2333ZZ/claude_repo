// 窄腰客户端:一切数据经 /df 代理走 REST——看板没有第二条取数路径(docs/28 §1)。
export async function api<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/df${path}`, { ...init, cache: "no-store" });
  if (res.status === 401) {
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
    throw new Error("未登录");
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { detail = (await res.json()).detail ?? detail; } catch {}
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}
