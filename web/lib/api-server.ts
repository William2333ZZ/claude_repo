// 服务端专用取数(Server Components,docs/30 §3/§4):
// df_session cookie 与 Bearer 令牌同源同签(security.py create_token/parse_token 共用一套),
// SSR 场景没有浏览器去帮你带同源 cookie,所以读 cookie 后手动转发为 Authorization 头,
// 直连后端(不经 /df 反代——反代只拦浏览器发起的请求)。
import { cookies } from "next/headers";

const API = process.env.DF_API_BASE || "https://datafoundry.onrender.com";

export interface ServerFetch<T> {
  status: number;
  data: T | null;
}

// 带状态码的版本:401(未认证)与 404(资源不存在)对用户是两种不同的话——
// "会话过期,请重登" 用在错的地方就是一条含糊的错误(违反 docs/28 §5「错误即导购」)。
export async function apiServerStatus<T = unknown>(path: string, init?: RequestInit): Promise<ServerFetch<T>> {
  const session = (await cookies()).get("df_session")?.value;
  if (!session) return { status: 401, data: null };
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: { ...(init?.headers || {}), Authorization: `Bearer ${session}` },
    cache: "no-store",
  });
  if (!res.ok) return { status: res.status, data: null };
  return { status: res.status, data: (await res.json()) as T };
}

// 大多数页面只要"有就渲染,没有就当没登录"这一种粒度——薄封装省得每处都解构。
export async function apiServer<T = unknown>(path: string, init?: RequestInit): Promise<T | null> {
  return (await apiServerStatus<T>(path, init)).data;
}
