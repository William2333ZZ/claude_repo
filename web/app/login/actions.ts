"use server";
// 登录即 Server Action(docs/30 §3):口令走浏览器→Next.js 服务端→后端,原生 HTTPS 表单提交,
// 从不落入任何客户端 JS 变量——密码永不经过 agent(docs/26 §1)的浏览器版:也永不经过前端 JS。
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

const API = process.env.DF_API_BASE || "https://datafoundry.onrender.com";
const SESSION_TTL = 12 * 3600; // 与后端 security.TOKEN_TTL_SECONDS 同值,契约变更两端同改

export async function loginAction(formData: FormData) {
  const username = String(formData.get("username") || "");
  const password = String(formData.get("password") || "");
  const next = String(formData.get("next") || "/console");

  const res = await fetch(`${API}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
    cache: "no-store",
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const msg = (body as { detail?: string }).detail || "登录失败";
    redirect(`/login?next=${encodeURIComponent(next)}&err=${encodeURIComponent(msg)}`);
  }

  const { token } = (await res.json()) as { token: string };
  (await cookies()).set("df_session", token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: SESSION_TTL,
  });
  redirect(next);
}
