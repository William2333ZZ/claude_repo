/** 看板外壳(docs/28,委托人定 Next.js;四区制见 docs/13 §4.5)。
 *  /df/* 重写到平台 REST 窄腰:浏览器全程同源,会话 cookie 落在看板域,
 *  API 的相对 303(/console)自然落回看板——同一后端兼容直连与代理两种入口。 */
const API = process.env.DF_API_BASE || "https://datafoundry.onrender.com";

/** @type {import('next').NextConfig} */
export default {
  async rewrites() {
    return [{ source: "/df/:path*", destination: `${API}/:path*` }];
  },
};
