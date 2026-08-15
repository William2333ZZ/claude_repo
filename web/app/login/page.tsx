import Link from "next/link";
import { loginAction } from "./actions";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; err?: string }>;
}) {
  const { next = "/console", err } = await searchParams;
  return (
    <main>
      <form action={loginAction} className="form">
        <h1>登录 DataFoundry</h1>
        <input type="hidden" name="next" value={next} />
        {err && <p className="err">{err}</p>}
        <label htmlFor="username">用户名</label>
        <input id="username" name="username" autoComplete="username" required />
        <label htmlFor="password">口令</label>
        <input id="password" name="password" type="password" autoComplete="current-password" required />
        <button className="btn" type="submit">登录</button>
        <p className="hint">
          没有账户?在你的 harness 里装 <span className="mono">skills/</span> 技能包,
          说一句"帮我清洗一批数据"即可自助注册——密码只在那一刻生成,交给你保管,永不经过智能体。
          已有 agent 会话想直开看板?让它跑一遍 onboard 剧本的"打开看板"一步即可,不必手输密码。
        </p>
        <p className="hint">
          智能体请求登录批准?去 <Link href="/device">/device</Link>。
        </p>
      </form>
    </main>
  );
}
