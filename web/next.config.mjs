/** 看板外壳(docs/28,委托人定 Next.js;四区制见 docs/13 §4.5)。
 *  /df/* 重写到平台 REST 窄腰:浏览器全程同源,会话 cookie 落在看板域,
 *  API 的相对 303(/console)自然落回看板——同一后端兼容直连与代理两种入口。 */
const API = process.env.DF_API_BASE || "https://datafoundry.onrender.com";

/** @type {import('next').NextConfig} */
export default {
  async rewrites() {
    return [
      { source: "/df/:path*", destination: `${API}/:path*` },
      // 设备码授权页是后端的内核兜底页(docs/29 §3.5),同源转发以维持
      // 「浏览器全程同源」原则——否则登录页里的 /device 链接会跳出看板域。
      { source: "/device", destination: `${API}/device` },
      { source: "/device/decide", destination: `${API}/device/decide` },
    ];
  },
};
