// 服务端专用取数(Server Components,docs/30 §3/§4):
// df_session cookie 与 Bearer 令牌同源同签(security.py create_token/parse_token 共用一套),
// SSR 场景没有浏览器去帮你带同源 cookie,所以读 cookie 后手动转发为 Authorization 头,
// 直连后端(不经 /df 反代——反代只拦浏览器发起的请求)。
import { cookies } from "next/headers";

const API = process.env.DF_API_BASE || "https://datafoundry.onrender.com";

export async function apiServer<T = unknown>(path: string, init?: RequestInit): Promise<T | null> {
  const session = (await cookies()).get("df_session")?.value;
  if (!session) return null;
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: { ...(init?.headers || {}), Authorization: `Bearer ${session}` },
    cache: "no-store",
  });
  if (!res.ok) return null;
  return res.json() as Promise<T>;
}
