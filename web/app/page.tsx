// 公开区(四区制之 Public):极简橱窗——铁证数字 + 两个转化位。
// 完整公开区(样例尸检/定价/装技能)按 #30-D 排期。
import Link from "next/link";

export default function Landing() {
  return (
    <main>
      <div className="appbar">
        <span className="brand"><i />DataFoundry</span>
        <span className="who"><Link href="/login">登录</Link></span>
      </div>
      <div className="tile">
        <h4>训练数据的质量裁决与归因层</h4>
        <div className="big">4.04%</div>
        <p className="headline">
          公开数据集 BELLE 的 19,999 条数学样本中,<b>807 条解答自己把算术算错</b>——
          复算必错、惯例已豁免的铁证假算式。每一条被杀的数据都有可辩护的死因,任何一条都可复核。
        </p>
        <p className="hint">seed + sha256 + 运行 id 全公开,可第三方复现(issue #24 战役报告)。</p>
      </div>
      <div className="tile">
        <h4>怎么用</h4>
        <p className="headline">
          在你的 harness(Claude Code / dsh / Codex)里装 <span className="mono">skills/</span> 技能包,
          说一句"帮我清洗一批数据"——剧本会带你注册、估算(免费)、人点头才扣费、跑完读尸检。
          看板(本站)负责看账:死因分布、余额流水、交付下载。
        </p>
        <p className="hint">估算永远免费;支付永远由人扫码;失败自动全额退款。</p>
      </div>
      <footer className="brandline">DataFoundry · 原生的是认知,确定的是裁决与账本</footer>
    </main>
  );
}
