"""计费模块:套餐/订单/额度账本 + 可插拔支付网关(借鉴 TinyShip 的组件选型)。

架构定位(docs/11):内核持有**账本与额度**(事实源);商业外壳(将来的 TinyShip 私有仓库)
只做收银台 UI,经本模块的 API 下单与接收回调。

Provider 层:
  mock      开发/测试全功能:HMAC 签名回调,零外部依赖
  stripe    标准库直连(Checkout Session + 官方 HMAC 回调验签方案),无 SDK 依赖
  wechatpay 薄适配 wechatpayv3(开源 SDK,可选安装;Native 下单出 code_url 二维码)
  alipay    薄适配 alipay-sdk-python(官方 SDK,可选安装)

启用:设 DATAFOUNDRY_BILLING_PROVIDER=mock|stripe|wechatpay|alipay;默认不设=计费关闭,
平台行为与从前完全一致。额度单位=可处理样本数,run 创建时按数据集样本数扣减。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod

PLANS = {
    "starter": {"name": "入门包", "credits": 100_000, "amount_cents": 9_900, "currency": "cny"},
    "pro": {"name": "专业包", "credits": 1_000_000, "amount_cents": 49_900, "currency": "cny"},
    "poc": {"name": "PoC 包", "credits": 10_000_000, "amount_cents": 990_000, "currency": "cny"},
}


def billing_enabled() -> bool:
    return bool(os.environ.get("DATAFOUNDRY_BILLING_PROVIDER"))


class PaymentProvider(ABC):
    name = ""
    ack_response: str | None = None  # 网关要求的通知应答明文(如支付宝的 "success");None=JSON 应答

    @abstractmethod
    def create_payment(self, order: dict) -> dict:
        """返回支付引导信息,如 {"pay_url": ...} 或 {"code_url": ...(二维码内容)}。"""

    @abstractmethod
    def parse_webhook(self, headers: dict, body: bytes) -> dict | None:
        """验签并解析回调;合法且支付成功返回 {"order_id": ...},否则 None。"""


class MockProvider(PaymentProvider):
    """开发与测试:回调体 JSON + X-Mock-Signature: HMAC-SHA256(secret, body)。"""

    name = "mock"

    def __init__(self):
        self.secret = os.environ.get("DATAFOUNDRY_BILLING_SECRET", "mock-secret").encode()

    def create_payment(self, order):
        return {"pay_url": f"mock://pay/{order['id']}", "note": "mock 网关:用签名回调模拟支付成功"}

    def sign(self, body: bytes) -> str:
        return hmac.new(self.secret, body, hashlib.sha256).hexdigest()

    def parse_webhook(self, headers, body):
        sig = headers.get("x-mock-signature", "")
        if not hmac.compare_digest(self.sign(body), sig):
            return None
        event = json.loads(body)
        if event.get("paid") is True and event.get("order_id"):
            return {"order_id": event["order_id"]}
        return None


class StripeProvider(PaymentProvider):
    """标准库直连 Stripe:Checkout Session 下单;回调按官方方案验签
    (Stripe-Signature: t=..,v1=..;HMAC-SHA256(whsec, f"{t}.{body}"))。"""

    name = "stripe"
    API = "https://api.stripe.com/v1/checkout/sessions"

    def __init__(self, tolerance: float = 300.0):
        self.api_key = os.environ.get("STRIPE_API_KEY", "")
        self.webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
        self.success_url = os.environ.get("STRIPE_SUCCESS_URL", "https://example.com/paid")
        self.tolerance = tolerance
        if not self.api_key:
            raise ValueError("StripeProvider 需要 STRIPE_API_KEY / STRIPE_WEBHOOK_SECRET")

    def create_payment(self, order):
        form = {
            "mode": "payment",
            "client_reference_id": order["id"],
            "success_url": self.success_url,
            "cancel_url": self.success_url,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": order["currency"],
            "line_items[0][price_data][unit_amount]": str(order["amount_cents"]),
            "line_items[0][price_data][product_data][name]": f"DataFoundry {order['plan']}",
            "metadata[order_id]": order["id"],
        }
        req = urllib.request.Request(
            self.API,
            data=urllib.parse.urlencode(form).encode(),
            headers={"Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            session = json.loads(resp.read())
        return {"pay_url": session["url"], "provider_ref": session["id"]}

    def verify_signature(self, headers: dict, body: bytes, now: float | None = None) -> bool:
        header = headers.get("stripe-signature", "")
        parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
        t, v1 = parts.get("t", ""), parts.get("v1", "")
        if not t or not v1:
            return False
        if abs((now or time.time()) - float(t)) > self.tolerance:
            return False
        want = hmac.new(self.webhook_secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(want, v1)

    def parse_webhook(self, headers, body):
        if not self.verify_signature(headers, body):
            return None
        event = json.loads(body)
        if event.get("type") == "checkout.session.completed":
            obj = event["data"]["object"]
            order_id = (obj.get("metadata") or {}).get("order_id") or obj.get("client_reference_id")
            if order_id:
                return {"order_id": order_id}
        return None


class WechatPayProvider(PaymentProvider):
    """薄适配开源 SDK wechatpayv3(pip install "datafoundry[billing-wechat]")。
    Native 下单返回 code_url(转二维码扫码支付);回调由 SDK 验签+解密。需真实商户号联调。"""

    name = "wechatpay"

    def __init__(self):
        try:
            from wechatpayv3 import WeChatPay, WeChatPayType
        except ImportError:
            raise ValueError("微信支付需要可选依赖:pip install wechatpayv3(开源,Apache-2.0)") from None
        self._type = WeChatPayType.NATIVE
        self._client = WeChatPay(
            wechatpay_type=self._type,
            mchid=os.environ["WECHATPAY_MCHID"],
            private_key=os.environ["WECHATPAY_PRIVATE_KEY"],
            cert_serial_no=os.environ["WECHATPAY_CERT_SERIAL_NO"],
            apiv3_key=os.environ["WECHATPAY_APIV3_KEY"],
            appid=os.environ["WECHATPAY_APPID"],
            notify_url=os.environ["WECHATPAY_NOTIFY_URL"],
        )

    def create_payment(self, order):
        code, message = self._client.pay(
            description=f"DataFoundry {order['plan']}",
            out_trade_no=order["id"],
            amount={"total": order["amount_cents"], "currency": "CNY"},
        )
        if code != 200:
            raise RuntimeError(f"微信下单失败 {code}: {message}")
        return {"code_url": json.loads(message)["code_url"]}

    def parse_webhook(self, headers, body):
        result = self._client.callback(headers, body)
        if result and result.get("event_type") == "TRANSACTION.SUCCESS":
            return {"order_id": result["resource"]["out_trade_no"]}
        return None


class AlipayProvider(PaymentProvider):
    """当面付路线(pip install "datafoundry[billing-alipay]",即 python-alipay-sdk):
    trade.precreate 预下单出 qr_code(转二维码扫码付),异步通知 RSA2 验签。
    ALIPAY_SANDBOX=1 走沙箱网关(开放平台沙箱环境,无需提审签约即可联调)。
    注意:支付宝要求通知应答明文 "success"(ack_response 机制,webhook 端点会遵守)。"""

    name = "alipay"
    ack_response = "success"

    def __init__(self):
        try:
            from alipay import AliPay
        except ImportError:
            raise ValueError(
                '支付宝需要可选依赖:pip install "datafoundry[billing-alipay]"(python-alipay-sdk,开源)'
            ) from None
        appid = os.environ.get("ALIPAY_APPID", "")
        priv = self._normalize_pem(os.environ.get("ALIPAY_APP_PRIVATE_KEY", ""), "RSA PRIVATE KEY")
        pub = self._normalize_pem(os.environ.get("ALIPAY_PUBLIC_KEY", ""), "PUBLIC KEY")
        if not (appid and priv and pub):
            raise ValueError("支付宝需要 ALIPAY_APPID / ALIPAY_APP_PRIVATE_KEY / ALIPAY_PUBLIC_KEY")
        self._client = AliPay(
            appid=appid,
            app_notify_url=os.environ.get("ALIPAY_NOTIFY_URL", ""),
            app_private_key_string=priv,
            alipay_public_key_string=pub,
            sign_type="RSA2",
            debug=os.environ.get("ALIPAY_SANDBOX", "") == "1",  # 沙箱网关开关
        )

    @staticmethod
    def _normalize_pem(key: str, kind: str) -> str:
        """支付宝控制台展示的是无头尾的单行 base64;SDK 需要 PEM。裸串自动包装,已含头尾则原样。"""
        key = key.strip()
        if not key or "-----BEGIN" in key:
            return key
        body = "".join(key.split())
        lines = "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
        return f"-----BEGIN {kind}-----\n{lines}\n-----END {kind}-----"

    def create_payment(self, order):
        result = self._client.api_alipay_trade_precreate(
            out_trade_no=order["id"],
            total_amount=f"{order['amount_cents'] / 100:.2f}",
            subject=f"DataFoundry {order['plan']}",
        )
        if result.get("code") != "10000":
            raise RuntimeError(
                f"支付宝预下单失败: code={result.get('code')} msg={result.get('msg')} "
                f"sub_code={result.get('sub_code')} sub_msg={result.get('sub_msg')}"
            )
        return {"code_url": result["qr_code"], "note": "转二维码后用支付宝(沙箱钱包)扫码支付"}

    def parse_webhook(self, headers, body):
        data = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode("utf-8")).items()}
        signature = data.pop("sign", "")
        if not signature or not self._client.verify(data, signature):
            return None
        if data.get("trade_status") in ("TRADE_SUCCESS", "TRADE_FINISHED"):
            return {"order_id": data.get("out_trade_no", "")}
        return None


_PROVIDERS = {"mock": MockProvider, "stripe": StripeProvider, "wechatpay": WechatPayProvider, "alipay": AlipayProvider}


def get_provider() -> PaymentProvider:
    name = os.environ.get("DATAFOUNDRY_BILLING_PROVIDER", "")
    if name not in _PROVIDERS:
        raise ValueError(f"未知支付网关 {name!r}(可用: {sorted(_PROVIDERS)})")
    return _PROVIDERS[name]()
