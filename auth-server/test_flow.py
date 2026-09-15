# -*- coding: utf-8 -*-
"""
auth-server 端到端测试
======================
用 aiohttp 的 test_utils 起真实 HTTP 服务，并把 authentik 的 userinfo
端点替换成本地假服务，从而在无 authentik 的环境下跑通完整流程。

运行:  py -3.11 -m unittest discover -s auth-server -p "test_*.py" -v
"""
import base64
import hashlib
import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, TestClient, TestServer

import admin as adminmod
import app as srv
import config as cfgmod
from jwtutil import b64u

CLIENT_ID = "unilink-qr"
CLIENT_SECRET = "s" * 40
REDIRECT = "https://auth.test/source/oauth/callback/unilink-qr/"

FAKE_USER = {
    "sub": "ak-sub-0001",
    "email": "zhang@example.com",
    "email_verified": True,
    "name": "张三",
    "preferred_username": "zhangsan",
    "groups": ["authentik Admins", "users"],
}


def make_cfg(base_url, authentik_url, **kw):
    c = cfgmod.Config(
        base_url=base_url.rstrip("/"),
        authentik_url=authentik_url.rstrip("/"),
        clients={CLIENT_ID: cfgmod.Client(
            CLIENT_ID, CLIENT_SECRET, (REDIRECT,), "authentik")},
    )
    for k, v in kw.items():
        setattr(c, k, v)
    return c


class FakeAuthentik:
    """最小 userinfo 服务：只认一个有效令牌"""
    VALID = "ak-access-token-valid"

    def __init__(self):
        self.calls = 0

    def build(self):
        app = web.Application()
        app.router.add_get("/application/o/userinfo/", self.userinfo)
        return app

    async def userinfo(self, request):
        self.calls += 1
        auth = request.headers.get("Authorization", "")
        if auth != "Bearer " + self.VALID:
            return web.json_response({"detail": "invalid token"}, status=401)
        return web.json_response(FAKE_USER)


class FlowTest(AioHTTPTestCase):

    async def get_application(self):
        # 假 authentik 单独起一个 server
        from aiohttp.test_utils import TestServer
        self.ak = FakeAuthentik()
        self.ak_server = TestServer(self.ak.build())
        await self.ak_server.start_server()
        ak_url = str(self.ak_server.make_url("")).rstrip("/")

        # 签名私钥放到临时目录，避免污染仓库
        self._tmp = tempfile.TemporaryDirectory()
        key_path = os.path.join(self._tmp.name, "test-rsa.pem")

        self.cfg = make_cfg("http://127.0.0.1", ak_url)
        return srv.build_app(self.cfg, key_path=key_path)

    async def tearDownAsync(self):
        await self.ak_server.close()
        self._tmp.cleanup()
        await super().tearDownAsync()

    # ---------- 辅助 ----------

    async def start_login(self, **extra):
        params = {"client_id": CLIENT_ID, "redirect_uri": REDIRECT,
                  "response_type": "code", "state": "st-123",
                  "scope": "openid profile email"}
        params.update(extra)
        r = await self.client.get("/authorize", params=params)
        self.assertEqual(r.status, 200)
        html = await r.text()
        self.assertIn("<svg", html)
        # 从页面 JS 里取出 ticket 与轮询密钥
        ticket = self._js_const(html, "var TK=")
        key = self._js_const(html, "KEY=")
        return ticket, key

    @staticmethod
    def _js_const(html: str, marker: str) -> str:
        i = html.index(marker) + len(marker)
        end = min(x for x in (html.find(",", i), html.find(";", i)) if x > 0)
        return json.loads(html[i:end])

    async def poll(self, ticket, key):
        r = await self.client.get("/api/session/" + ticket, params={"k": key})
        return await r.json()

    async def code_of(self, ticket, key):
        return (await self.poll(ticket, key))["code"]

    async def approve(self, ticket, token=FakeAuthentik.VALID):
        return await self.client.post("/api/scan/approve", json={
            "ticket": ticket, "access_token": token})

    # ---------- 发现与 JWKS ----------

    async def test_discovery(self):
        r = await self.client.get("/.well-known/openid-configuration")
        d = await r.json()
        self.assertEqual(d["issuer"], self.cfg.issuer)
        self.assertEqual(d["response_types_supported"], ["code"])
        self.assertIn("S256", d["code_challenge_methods_supported"])

    async def test_jwks_has_rsa_key(self):
        d = await (await self.client.get("/jwks")).json()
        k = d["keys"][0]
        self.assertEqual((k["kty"], k["alg"], k["use"]), ("RSA", "RS256", "sig"))
        self.assertTrue(k["n"] and k["kid"])

    # ---------- 授权端点校验 ----------

    async def test_unknown_client_rejected(self):
        r = await self.client.get("/authorize", params={
            "client_id": "nope", "redirect_uri": REDIRECT,
            "response_type": "code"})
        self.assertEqual(r.status, 400)

    async def test_redirect_uri_must_match_exactly(self):
        r = await self.client.get("/authorize", params={
            "client_id": CLIENT_ID, "response_type": "code",
            "redirect_uri": REDIRECT + "evil"})
        self.assertEqual(r.status, 400)
        self.assertIn("redirect_uri", await r.text())

    async def test_bad_response_type_redirects_with_error(self):
        r = await self.client.get("/authorize", params={
            "client_id": CLIENT_ID, "redirect_uri": REDIRECT,
            "response_type": "token", "state": "st-1"},
            allow_redirects=False)
        self.assertEqual(r.status, 302)
        loc = r.headers["Location"]
        self.assertIn("error=unsupported_response_type", loc)
        self.assertIn("state=st-1", loc)

    # ---------- 完整流程 ----------

    async def test_full_flow_issues_valid_id_token(self):
        ticket, key = await self.start_login()

        # 1) 初始为 pending
        d = await self.poll(ticket, key)
        self.assertEqual(d["state"], "pending")

        # 2) 手机预览 → scanned
        r = await self.client.post("/api/scan/preview", json={
            "ticket": ticket, "device": "Pixel 8"})
        self.assertEqual((await r.json())["app"], "authentik")
        d = await self.poll(ticket, key)
        self.assertEqual(d["state"], "scanned")
        self.assertEqual(d["device"], "Pixel 8")

        # 3) 手机确认 → approved，浏览器可取到 code
        self.assertEqual((await self.approve(ticket)).status, 200)
        d = await self.poll(ticket, key)
        self.assertEqual(d["state"], "approved")
        self.assertEqual(d["user"], "zhangsan")
        code = d["code"]

        # 4) authentik 用 code 换 token
        r = await self.client.post("/token", data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": REDIRECT,
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
        self.assertEqual(r.status, 200)
        tok = await r.json()

        # 5) id_token 的头与载荷正确，sub 沿用 authentik 的 sub
        head_b64, body_b64, sig_b64 = tok["id_token"].split(".")
        pad = lambda s: s + "=" * (-len(s) % 4)
        head = json.loads(base64.urlsafe_b64decode(pad(head_b64)))
        body = json.loads(base64.urlsafe_b64decode(pad(body_b64)))
        self.assertEqual(head["alg"], "RS256")
        self.assertEqual(head["kid"], self.app["signer"].kid)
        self.assertEqual(body["sub"], FAKE_USER["sub"])
        self.assertEqual(body["aud"], CLIENT_ID)
        self.assertEqual(body["iss"], self.cfg.issuer)
        self.assertEqual(body["email"], FAKE_USER["email"])
        self.assertEqual(body["preferred_username"], "zhangsan")
        self.assertGreater(body["exp"], body["iat"])

        # 6) 签名可用 JWKS 公钥验证
        self._verify_sig(head_b64, body_b64, sig_b64)

        # 7) access_token 可查 userinfo
        r = await self.client.get("/userinfo", headers={
            "Authorization": "Bearer " + tok["access_token"]})
        self.assertEqual((await r.json())["sub"], FAKE_USER["sub"])

    def _verify_sig(self, head_b64, body_b64, sig_b64):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        pad = lambda s: s + "=" * (-len(s) % 4)
        pub = self.app["signer"].private_key.public_key()
        pub.verify(base64.urlsafe_b64decode(pad(sig_b64)),
                   (head_b64 + "." + body_b64).encode("ascii"),
                   padding.PKCS1v15(), hashes.SHA256())

    # ---------- 安全性 ----------

    async def test_wrong_poll_key_rejected(self):
        """拍到二维码的人无法凭 ticket 抢走授权码"""
        ticket, key = await self.start_login()
        await self.approve(ticket)
        # 有正确密钥 → 能取到 code
        self.assertIn("code", await self.poll(ticket, key))
        # 密钥错误 / 缺失 → 一律按过期处理，不泄露 code
        for bad in ("", "wrong-key", key[:-1]):
            d = await self.poll(ticket, bad)
            self.assertEqual(d["state"], "expired")
            self.assertNotIn("code", d)

    async def test_invalid_authentik_token_rejected(self):
        """伪造 authentik 令牌换不出身份 —— 这是整个方案的信任根"""
        ticket, key = await self.start_login()
        r = await self.approve(ticket, token="forged-token")
        self.assertEqual(r.status, 401)
        d = await self.poll(ticket, key)
        self.assertNotEqual(d["state"], "approved")

    async def test_code_is_single_use(self):
        ticket, key = await self.start_login()
        await self.approve(ticket)
        code = await self.code_of(ticket, key)
        form = {"grant_type": "authorization_code", "code": code,
                "redirect_uri": REDIRECT,
                "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
        self.assertEqual((await self.client.post("/token", data=form)).status, 200)
        r2 = await self.client.post("/token", data=form)
        self.assertEqual(r2.status, 400)
        self.assertEqual((await r2.json())["error"], "invalid_grant")

    async def test_wrong_client_secret_rejected(self):
        ticket, key = await self.start_login()
        await self.approve(ticket)
        code = await self.code_of(ticket, key)
        r = await self.client.post("/token", data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": REDIRECT,
            "client_id": CLIENT_ID, "client_secret": "wrong"})
        self.assertEqual(r.status, 401)
        self.assertEqual((await r.json())["error"], "invalid_client")

    async def test_basic_auth_accepted(self):
        ticket, key = await self.start_login()
        await self.approve(ticket)
        code = await self.code_of(ticket, key)
        cred = base64.b64encode(
            ("%s:%s" % (CLIENT_ID, CLIENT_SECRET)).encode()).decode()
        r = await self.client.post("/token",
                                   data={"grant_type": "authorization_code",
                                         "code": code, "redirect_uri": REDIRECT},
                                   headers={"Authorization": "Basic " + cred})
        self.assertEqual(r.status, 200)

    async def test_redirect_uri_mismatch_at_token(self):
        ticket, key = await self.start_login()
        await self.approve(ticket)
        code = await self.code_of(ticket, key)
        r = await self.client.post("/token", data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": "https://evil.test/cb",
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
        self.assertEqual(r.status, 400)

    async def test_pkce_s256(self):
        verifier = "a" * 64
        challenge = b64u(hashlib.sha256(verifier.encode()).digest())
        ticket, key = await self.start_login(code_challenge=challenge,
                                           code_challenge_method="S256")
        await self.approve(ticket)
        code = await self.code_of(ticket, key)

        bad = await self.client.post("/token", data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": REDIRECT, "code_verifier": "b" * 64,
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
        self.assertEqual(bad.status, 400)

        # 上一步已消耗 code，重新走一遍验证正确 verifier 能通过
        ticket2, key2 = await self.start_login(code_challenge=challenge,
                                            code_challenge_method="S256")
        await self.approve(ticket2)
        code2 = await self.code_of(ticket2, key2)
        ok = await self.client.post("/token", data={
            "grant_type": "authorization_code", "code": code2,
            "redirect_uri": REDIRECT, "code_verifier": verifier,
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
        self.assertEqual(ok.status, 200)

    async def test_deny_blocks_login(self):
        ticket, key = await self.start_login()
        await self.client.post("/api/scan/preview",
                               json={"ticket": ticket, "device": "手机"})
        await self.client.post("/api/scan/deny", json={"ticket": ticket})
        d = await self.poll(ticket, key)
        self.assertEqual(d["state"], "denied")
        self.assertNotIn("code", d)

    async def test_double_approve_rejected(self):
        ticket, key = await self.start_login()
        self.assertEqual((await self.approve(ticket)).status, 200)
        self.assertEqual((await self.approve(ticket)).status, 409)

    async def test_expired_ticket(self):
        ticket, key = await self.start_login()
        self.app["store"].get(ticket).expires = 0     # 直接判定过期
        d = await self.poll(ticket, key)
        self.assertEqual(d["state"], "expired")
        self.assertEqual((await self.approve(ticket)).status, 404)

    async def test_group_whitelist_blocks(self):
        self.cfg.allowed_groups = frozenset({"仅此组可用"})
        try:
            ticket, key = await self.start_login()
            self.assertEqual((await self.approve(ticket)).status, 403)
        finally:
            self.cfg.allowed_groups = frozenset()

    async def test_sub_whitelist_allows_match(self):
        self.cfg.allowed_subs = frozenset({FAKE_USER["sub"]})
        try:
            ticket, key = await self.start_login()
            self.assertEqual((await self.approve(ticket)).status, 200)
        finally:
            self.cfg.allowed_subs = frozenset()

    async def _id_token_body(self, ticket, key):
        code = await self.code_of(ticket, key)
        tok = await (await self.client.post("/token", data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": REDIRECT,
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})).json()
        raw = tok["id_token"].split(".")[1]
        return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))

    async def test_scope_limits_claims(self):
        """只要 openid 时不应下发 email / profile 字段"""
        ticket, key = await self.start_login(scope="openid")
        await self.approve(ticket)
        body = await self._id_token_body(ticket, key)
        self.assertEqual(body["sub"], FAKE_USER["sub"])
        self.assertNotIn("email", body)
        self.assertNotIn("preferred_username", body)

    async def test_nonce_echoed(self):
        ticket, key = await self.start_login(nonce="n-once-42")
        await self.approve(ticket)
        body = await self._id_token_body(ticket, key)
        self.assertEqual(body["nonce"], "n-once-42")

    async def test_userinfo_requires_token(self):
        self.assertEqual((await self.client.get("/userinfo")).status, 401)
        r = await self.client.get("/userinfo",
                                  headers={"Authorization": "Bearer nope"})
        self.assertEqual(r.status, 401)

    async def test_app_config_endpoint(self):
        d = await (await self.client.get("/api/app/config")).json()
        self.assertEqual(d["client_id"], self.cfg.app_client_id)
        self.assertEqual(d["redirect_uri"], self.cfg.app_redirect_uri)
        self.assertTrue(d["authorize_url"].endswith("/application/o/authorize/"))
        self.assertTrue(d["userinfo_url"].endswith("/application/o/userinfo/"))
        # offline_access 必须在内，否则 App 拿不到 refresh_token
        self.assertIn("offline_access", d["scopes"])

    async def test_session_cap_evicts_oldest(self):
        """会话上限生效，且不会无限增长"""
        cap = self.app["store"].max_sessions
        self.app["store"].max_sessions = 5
        try:
            for _ in range(12):
                await self.start_login()
            self.assertLessEqual(self.app["store"].stats()["sessions"], 5)
        finally:
            self.app["store"].max_sessions = cap


class ConfigTest(unittest.TestCase):
    """配置校验：必须拒绝不安全的默认值"""

    def _load_with(self, data):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f)
            old = cfgmod.CFG_PATH
            cfgmod.CFG_PATH = p
            try:
                return cfgmod.load()
            finally:
                cfgmod.CFG_PATH = old

    def test_weak_secret_rejected(self):
        with self.assertRaises(cfgmod.ConfigError) as ctx:
            self._load_with({
                "base_url": "https://a.test",
                "authentik_url": "https://b.test",
                "clients": [{"client_id": "c", "client_secret": "short",
                             "redirect_uris": ["https://b.test/cb"]}]})
        self.assertIn("client_secret", str(ctx.exception))

    def test_missing_base_url_rejected(self):
        with self.assertRaises(cfgmod.ConfigError):
            self._load_with({"authentik_url": "https://b.test"})

    def test_no_clients_rejected(self):
        with self.assertRaises(cfgmod.ConfigError):
            self._load_with({"base_url": "https://a.test",
                             "authentik_url": "https://b.test",
                             "clients": []})

    def test_valid_config_loads(self):
        cfg = self._load_with({
            "base_url": "https://a.test/",
            "authentik_url": "https://b.test/",
            "clients": [{"client_id": "c", "client_secret": "z" * 40,
                         "redirect_uris": ["https://b.test/cb"]}]})
        self.assertEqual(cfg.base_url, "https://a.test")        # 尾斜杠被规范化
        self.assertEqual(cfg.ak_userinfo, "https://b.test/application/o/userinfo/")
        self.assertTrue(cfg.client("c").allows("https://b.test/cb"))
        self.assertFalse(cfg.client("c").allows("https://b.test/cb2"))


class QrPageTest(unittest.TestCase):
    def test_svg_structure(self):
        from qr import qr_svg
        svg = qr_svg('{"v":1,"typ":"unilink-login","srv":"https://a.test","tk":"x"}')
        self.assertTrue(svg.startswith("<svg"))
        self.assertTrue(svg.endswith("</svg>"))
        self.assertIn("<rect", svg)

    def test_page_escapes_html(self):
        from pages import render_scan_page
        html = render_scan_page(ticket="tk", poll_key="pk", qr="<svg/>",
                                app_name="<script>alert(1)</script>",
                                ttl=180, base_url="https://a.test")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_ticket_json_encoded_in_js(self):
        from pages import render_scan_page
        html = render_scan_page(ticket='a"b', poll_key="pk", qr="",
                                app_name="x", ttl=10, base_url="https://a.test")
        self.assertIn(r'var TK="a\"b"', html)

    def test_poll_secret_only_in_page_not_in_qr(self):
        """轮询密钥必须只出现在页面 JS 里，不能进二维码内容"""
        from pages import render_scan_page
        from qr import qr_svg
        secret = "poll-secret-xyz"
        payload = json.dumps({"v": 1, "typ": "unilink-login",
                              "srv": "https://a.test", "tk": "tk1"})
        self.assertNotIn(secret, payload)
        html = render_scan_page(ticket="tk1", poll_key=secret,
                                qr=qr_svg(payload), app_name="x",
                                ttl=180, base_url="https://a.test")
        self.assertIn(secret, html)


class SetupAndAdminFlowTest(unittest.IsolatedAsyncioTestCase):
    """后台配置：未配置 → 首次向导 → 运行模式 → 面板登录 → 面板修改"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        # 保存原路径，测完恢复，避免污染同进程的其它测试
        self._orig = (cfgmod.STATE_DIR, cfgmod.STATE_PATH,
                      adminmod.SETUP_TOKEN_PATH, adminmod.ADMIN_HASH_PATH)
        cfgmod.STATE_DIR = d
        cfgmod.STATE_PATH = os.path.join(d, "state.json")
        adminmod.SETUP_TOKEN_PATH = os.path.join(d, "setup.token")
        adminmod.ADMIN_HASH_PATH = os.path.join(d, "admin.hash")
        self._env = {k: v for k, v in os.environ.items() if k.startswith("UNILINK_")}
        for k in self._env:
            del os.environ[k]

    def tearDown(self):
        self.tmp.cleanup()
        (cfgmod.STATE_DIR, cfgmod.STATE_PATH,
         adminmod.SETUP_TOKEN_PATH, adminmod.ADMIN_HASH_PATH) = self._orig
        os.environ.update(self._env)

    async def test_full_setup_and_admin(self):
        self.assertIsNone(cfgmod.load_or_none())          # 初始未配置
        a = srv.build_app(None)
        self.assertTrue(a["setup_mode"])

        client = TestClient(TestServer(a))
        await client.start_server()
        try:
            # 1) /setup 可访问
            r = await client.get("/setup")
            self.assertEqual(r.status, 200)
            self.assertIn("setup_token", await r.text())

            # 2) 业务端点被网关拦下：浏览器路径 302 到 /setup
            r = await client.get("/authorize", params={"client_id": "x"},
                                 allow_redirects=False)
            self.assertEqual(r.status, 302)
            self.assertEqual(r.headers["Location"], "/setup")

            # 3) API 返回 503
            r = await client.get("/api/app/config")
            self.assertEqual(r.status, 503)

            # 4) 错误的 setup token 被拒
            r = await client.post("/setup", data={
                "setup_token": "wrong", "base_url": "https://qr.test",
                "authentik_url": "https://auth.test", "client_secret": "s" * 48})
            self.assertIn("无效", await r.text())

            # 5) 正确提交 → 进入运行模式并签发 admin token
            tok = adminmod.setup_token()
            r = await client.post("/setup", data={
                "setup_token": tok,
                "base_url": "https://qr.test/",
                "authentik_url": "https://auth.test",
                "client_id": "unilink-qr",
                "client_secret": "s" * 48})
            self.assertEqual(r.status, 200)
            html = await r.text()
            self.assertIn("首次配置完成", html)
            m = re.search(r'<code[^>]*>([A-Za-z0-9_-]{40,})</code>', html)
            self.assertTrue(m, "页面上找不到 admin token")
            admin_token = m.group(1)
            self.assertFalse(a["setup_mode"])              # 已切换到运行模式
            self.assertFalse(os.path.exists(adminmod.SETUP_TOKEN_PATH))  # 一次性
            r = await client.get("/setup", allow_redirects=False)
            self.assertEqual(r.status, 404)                # 配置完成后 /setup 关闭

            # 6) setup 已建立会话 → 直达管理面板
            r = await client.get("/admin")
            html = await r.text()
            self.assertIn("管理面板", html)
            self.assertIn("https://qr.test", html)

            # 6b) 无 cookie 的客户端 → 登录页；错误令牌被拒；正确登录 302
            anon = TestClient(TestServer(a))
            await anon.start_server()
            try:
                r = await anon.get("/admin")
                self.assertIn("管理员令牌", await r.text())
                r = await anon.post("/admin", data={"admin_token": "nope"})
                self.assertIn("无效", await r.text())
                r = await anon.post("/admin", data={"admin_token": admin_token},
                                    allow_redirects=False)
                self.assertEqual(r.status, 302)
            finally:
                await anon.close()

            # 7) 已登录面板；无 CSRF 提交被拒
            r = await client.get("/admin")
            html = await r.text()
            csrf = re.search(r'name="csrf" value="([^"]+)"', html).group(1)
            r = await client.post("/admin", data={
                "base_url": "https://qr.test", "authentik_url": "https://auth.test"})
            self.assertIn("CSRF", await r.text())

            # 8) 保存：密钥留空 = 沿用，顺带改 TTL 与白名单
            r = await client.post("/admin", data={
                "csrf": csrf,
                "base_url": "https://qr.test",
                "authentik_url": "https://auth.test",
                "client_id": "unilink-qr",
                "client_secret": "",
                "login_ttl": "300",
                "allowed_groups": "staff, admins"})
            self.assertEqual(r.status, 200)
            self.assertIn("已保存", await r.text())

            cfg = cfgmod.load()
            self.assertEqual(cfg.base_url, "https://qr.test")
            self.assertEqual(cfg.login_ttl, 300)
            self.assertEqual(sorted(cfg.allowed_groups), ["admins", "staff"])
            self.assertEqual(cfg.client("unilink-qr").client_secret, "s" * 48)
        finally:
            await client.close()



if __name__ == "__main__":
    unittest.main(verbosity=2)

