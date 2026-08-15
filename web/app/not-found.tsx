import Link from "next/link";

// 404 = 确实无此资源(与「会话过期请重登」是两种不同的话,docs/30 §3 的错误导购纪律)。
export default function NotFound() {
  return (
    <main>
      <div className="tile">
        <h4>无此页面</h4>
        <p className="headline">你要找的运行或页面不存在——可能 ID 输错了,或者链接已经过期。</p>
        <Link className="btn" href="/console">
          回到总览
        </Link>
      </div>
    </main>
  );
}
