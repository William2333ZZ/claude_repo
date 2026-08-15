import type { ReactNode } from "react";

// 展示层原件(docs/30 §3):无状态,props 进类型出,不拿 fetch——数据永远从页面注入。
export function Card({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <div className="tile">
      {title && <h4>{title}</h4>}
      {children}
    </div>
  );
}
