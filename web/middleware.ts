// 四区制之 Auth&Middleware(docs/13 §4.5):/console 区无会话即去 /login。
// 只查 cookie 在场性;真伪由 API 端每次请求裁决(窄腰是唯一权威)。
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function middleware(req: NextRequest) {
  if (!req.cookies.get("df_session")) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("next", req.nextUrl.pathname);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = { matcher: ["/console/:path*", "/console"] };
