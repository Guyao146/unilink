#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UniLink 扫码登录服务（OIDC Provider）
=====================================
本服务对 authentik 而言是一个**上游 OIDC 身份源**（authentik 里叫 OAuth Source）。
把它加为 Source 之后，所有已接入 authentik 的下游项目在登录页上都会多出
一个「手机扫码登录」入口，无需逐个改造。

    浏览器                本服务                    authentik            手机 App
      │ 点“扫码登录”         │                          │                    │
      │ ──────────────────► authentik 登录页            │                    │
      │                     │ ◄── 重定向到 /authorize ──┤                    │
      │ ◄── 二维码页面 ─────┤                          │                    │
      │                     │ ◄──────── 扫码 + 确认（带 authentik 令牌）─────┤
      │                     │ ── 用令牌查 userinfo ───► │                    │
      │ ◄── 轮询到 code ────┤                          │                    │
      │ ── code 回调 ──────────────────────────────────► │                    │
      │                     │ ◄── /token 换 id_token ──┤                    │
      │ ◄──────────────── 登录完成，回到原项目 ──────────┤                    │

运行:
    pip install -r requirements.txt
    python app.py
"""
import json
import logging
import secrets
import sys
import time
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

import config
import identity as ident
import store as st
from jwtutil import Signer
from pages import render_scan_page, render_error_page
from qr import qr_svg

log = logging.getLogger("unilink.auth")

ROUTES = web.RouteTableDef()


# ======================================================================
# 工具
# ======================================================================

def _json(data, status=200):
    return web.json_response(data, status=status,
                             headers={"Cache-Control": "no-store"})


def _err(request, msg, status=400):
    """按 Accept 头决定返回 JSON 还是 HTML 错误页"""
    accept = request.headers.get("Accept", "")
    if "text/html" in accept:
        return web.Response(text=render_error_page(msg), status=status,
                            content_type="text/html", charset="utf-8")
    return _json({"error": "invalid_request", "error_description": msg}, status)


def _client_ip(request) -> str:
    # 反代场景下取 X-Forwarded-For 的第一跳
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote or "?"


def _basic_auth(request):
    """解析 client_secret_basic；返回 (client_id, secret) 或 (None, None)"""
    import base64
    h = request.headers.get("Authorization", "")
    if not h.startswith("Basic "):
        return None, None
    try:
        raw = base64.b64decode(h[6:]).decode("utf-8")
        cid, _, sec = raw.partition(":")
        return cid, sec
    except Exception:
        return None, None


# ======================================================================
# OIDC 发现
# ======================================================================

@ROUTES.get("/.well-known/openid-configuration")
async def discovery(request):
    cfg = request.app["cfg"]
    return _json({
        "issuer": cfg.issuer,
        "authorization_endpoint": cfg.base_url + "/authorize",
        "token_endpoint": cfg.base_url + "/token",
        "userinfo_endpoint": cfg.base_url + "/userinfo",
        "jwks_uri": cfg.base_url + "/jwks",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "profile", "email"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_basic", "client_secret_post"],
        "code_challenge_methods_supported": ["S256", "plain"],
        "claims_supported": [
            "sub", "iss", "aud", "exp", "iat", "email", "email_verified",
            "name", "preferred_username", "groups"],
    })


@ROUTES.get("/jwks")
async def jwks(request):
    return _json(request.app["signer"].jwks())


@ROUTES.get("/healthz")
async def healthz(request):
    return _json({"ok": True, **request.app["store"].stats()})


# ======================================================================
# 授权端点：浏览器进入，显示二维码
# ======================================================================

@ROUTES.get("/authorize")
async def authorize(request):
    cfg = request.app["cfg"]
    q = request.query

    client_id = q.get("client_id", "")
    redirect_uri = q.get("redirect_uri", "")
    response_type = q.get("response_type", "")
    state_param = q.get("state", "")

    client = cfg.client(client_id)
    if client is None:
        return _err(request, "未注册的 client_id：%s" % client_id, 400)
    # redirect_uri 必须精确匹配白名单，否则拒绝 —— 防开放重定向
    if not client.allows(redirect_uri):
        return _err(request, "redirect_uri 未在该客户端的白名单内：%s" % redirect_uri, 400)

    # 到这一步 redirect_uri 已可信，参数类错误改为按 OAuth 规范重定向回报
    def back(error, desc):
        p = {"error": error, "error_description": desc}
        if state_param:
            p["state"] = state_param
        sep = "&" if "?" in redirect_uri else "?"
        raise web.HTTPFound(redirect_uri + sep + urlencode(p))

    if response_type != "code":
        back("unsupported_response_type", "仅支持 response_type=code")

    scope = q.get("scope", "openid profile email")
    if "openid" not in scope.split():
        back("invalid_scope", "scope 必须包含 openid")

    s = request.app["store"].create(
        client_id=client_id,
        redirect_uri=redirect_uri,
        state_param=state_param,
        nonce=q.get("nonce", ""),
        scope=scope,
        code_challenge=q.get("code_challenge", ""),
        code_challenge_method=q.get("code_challenge_method", ""),
    )
    log.info("新的扫码登录会话 ticket=%s… client=%s ip=%s",
             s.ticket[:8], client_id, _client_ip(request))

    # 二维码内容：手机 App 扫到后据此定位本服务与 ticket
    payload = json.dumps({"v": 1, "typ": "unilink-login",
                          "srv": cfg.base_url, "tk": s.ticket},
                         separators=(",", ":"), ensure_ascii=False)
    html = render_scan_page(
        ticket=s.ticket,
        poll_key=s.poll_secret,
        qr=qr_svg(payload),
        app_name=client.name,
        ttl=cfg.login_ttl,
        base_url=cfg.base_url,
    )
    return web.Response(text=html, content_type="text/html", charset="utf-8",
                        headers={"Cache-Control": "no-store"})


@ROUTES.get("/api/session/{ticket}")
async def session_poll(request):
    """浏览器轮询会话状态；变为 approved 时拿到 code 自行跳转回 authentik。

    必须带上 /authorize 页面里下发的 poll_secret：授权码只能交给发起本次
    登录的那个浏览器。否则拍到二维码的人也能轮询同一 ticket 抢走 code。
    """
    store = request.app["store"]
    s = store.get(request.match_info["ticket"])
    if s is None:
        return _json({"state": "expired"})
    given = request.query.get("k", "")
    if not secrets.compare_digest(given, s.poll_secret):
        # 不暴露"ticket 存在但密钥错"这一信息，统一按不存在处理
        return _json({"state": "expired"})
    return _json(s.public())


# ======================================================================
# 令牌端点：authentik 用 code 换 id_token
# ======================================================================

def _verify_pkce(challenge: str, method: str, verifier: str) -> bool:
    import hashlib
    import secrets as _s
    from jwtutil import b64u
    if not verifier:
        return False
    if (method or "plain").upper() == "S256":
        calc = b64u(hashlib.sha256(verifier.encode("ascii")).digest())
    else:
        calc = verifier
    return _s.compare_digest(calc, challenge)


@ROUTES.post("/token")
async def token(request):
    cfg = request.app["cfg"]
    data = await request.post()

    cid, sec = _basic_auth(request)
    if cid is None:
        cid = data.get("client_id", "")
        sec = data.get("client_secret", "")

    client = cfg.client(cid)
    if client is None or not client.check_secret(sec):
        # 统一错误信息，不区分"客户端不存在"与"密钥错误"，避免探测
        return _json({"error": "invalid_client",
                      "error_description": "客户端认证失败"}, 401)

    if data.get("grant_type") != "authorization_code":
        return _json({"error": "unsupported_grant_type",
                      "error_description": "仅支持 authorization_code"}, 400)

    s, why = request.app["store"].consume_code(data.get("code", ""))
    if s is None:
        return _json({"error": "invalid_grant", "error_description": why}, 400)

    # code 必须由同一个客户端兑换，且 redirect_uri 前后一致
    if s.client_id != cid:
        return _json({"error": "invalid_grant",
                      "error_description": "该授权码不属于当前客户端"}, 400)
    ru = data.get("redirect_uri", "")
    if ru and ru != s.redirect_uri:
        return _json({"error": "invalid_grant",
                      "error_description": "redirect_uri 与授权请求不一致"}, 400)

    # PKCE 校验（authentik 作为 RP 通常会带上）
    if s.code_challenge:
        if not _verify_pkce(s.code_challenge, s.code_challenge_method,
                            data.get("code_verifier", "")):
            return _json({"error": "invalid_grant",
                          "error_description": "code_verifier 校验失败"}, 400)

    claims = ident.build_claims(s.identity)
    scopes = set(s.scope.split())
    extra = {}
    if "email" in scopes:
        for k in ("email", "email_verified"):
            if k in claims:
                extra[k] = claims[k]
    if "profile" in scopes:
        for k in ("name", "given_name", "family_name",
                  "preferred_username", "nickname", "groups"):
            if k in claims:
                extra[k] = claims[k]

    id_token = request.app["signer"].id_token(
        issuer=cfg.issuer, client_id=cid, sub=claims["sub"],
        ttl=cfg.id_token_ttl, extra=extra, nonce=s.nonce)
    access_token = request.app["store"].issue_token(claims, cid)

    log.info("已签发令牌 client=%s sub=%s", cid, claims["sub"])
    return _json({"access_token": access_token,
                  "token_type": "Bearer",
                  "expires_in": cfg.token_ttl,
                  "id_token": id_token,
                  "scope": s.scope})


@ROUTES.get("/userinfo")
async def userinfo(request):
    h = request.headers.get("Authorization", "")
    if not h.startswith("Bearer "):
        return _json({"error": "invalid_token",
                      "error_description": "缺少 Bearer 令牌"}, 401)
    info = request.app["store"].token_identity(h[7:])
    if info is None:
        return _json({"error": "invalid_token",
                      "error_description": "令牌无效或已过期"}, 401)
    return _json(info)


# ======================================================================
# 手机 App 接口
# ======================================================================

@ROUTES.get("/api/app/config")
async def app_config(request):
    """App 首次配置时拉取 authentik 端点，免得在手机上手输一堆 URL"""
    cfg = request.app["cfg"]
    return _json({
        "authentik_url": cfg.authentik_url,
        "authorize_url": cfg.authentik_url + "/application/o/authorize/",
        "token_url": cfg.ak_token,
        "userinfo_url": cfg.ak_userinfo,
        "client_id": cfg.app_client_id,
        "redirect_uri": cfg.app_redirect_uri,
        "scopes": cfg.app_scopes,
    })


async def _load_session(request, ticket):
    s = request.app["store"].get(ticket)
    if s is None:
        raise web.HTTPNotFound(
            text=json.dumps({"error": "expired",
                             "error_description": "二维码已过期，请在电脑上刷新后重扫"},
                            ensure_ascii=False),
            content_type="application/json")
    return s


@ROUTES.post("/api/scan/preview")
async def scan_preview(request):
    """手机扫到码后先调这里：确认 ticket 有效并取回"要登录到哪里"的描述。
    此端点只读，不需要 authentik 令牌，也不泄露任何用户信息。"""
    cfg = request.app["cfg"]
    body = await request.json()
    s = await _load_session(request, body.get("ticket", ""))
    st_ = request.app["store"].mark_scanned(
        s.ticket, body.get("device", ""), _client_ip(request))
    client = cfg.client(s.client_id)
    return _json({
        "ok": True,
        "app": client.name if client else s.client_id,
        "scope": s.scope,
        "state": (st_ or s).state,
        "expires_in": max(0, int(s.expires - time.time())),
    })


@ROUTES.post("/api/scan/approve")
async def scan_approve(request):
    """手机上点了"确认登录"：校验 authentik 令牌 → 生成授权码"""
    cfg = request.app["cfg"]
    body = await request.json()
    s = await _load_session(request, body.get("ticket", ""))

    if s.state in (st.APPROVED, st.CONSUMED):
        return _json({"error": "already_used",
                      "error_description": "该二维码已被确认过"}, 409)

    try:
        info = await ident.fetch_identity(
            request.app["http"], cfg.ak_userinfo, body.get("access_token", ""))
    except ident.IdentityError as e:
        log.warning("身份校验失败 ticket=%s…: %s", s.ticket[:8], e)
        return _json({"error": "invalid_identity",
                      "error_description": str(e)}, e.status)

    why = ident.check_allowed(info, cfg.allowed_subs, cfg.allowed_groups)
    if why:
        log.warning("拒绝登录 sub=%s: %s", info.get("sub"), why)
        return _json({"error": "forbidden", "error_description": why}, 403)

    claims = ident.build_claims(info)
    if request.app["store"].approve(s.ticket, claims) is None:
        return _json({"error": "bad_state",
                      "error_description": "会话状态已变化，请重新扫码"}, 409)

    log.info("扫码登录已确认 ticket=%s… sub=%s device=%s",
             s.ticket[:8], claims["sub"], s.device_name or "?")
    return _json({"ok": True,
                  "user": claims.get("preferred_username") or claims.get("email", ""),
                  "app": (cfg.client(s.client_id).name
                          if cfg.client(s.client_id) else s.client_id)})


@ROUTES.post("/api/scan/deny")
async def scan_deny(request):
    body = await request.json()
    s = await _load_session(request, body.get("ticket", ""))
    request.app["store"].deny(s.ticket)
    log.info("用户拒绝了扫码登录 ticket=%s…", s.ticket[:8])
    return _json({"ok": True})


# ======================================================================
# 启动
# ======================================================================

async def _on_startup(app):
    app["http"] = aiohttp.ClientSession()


async def _on_cleanup(app):
    await app["http"].close()


def build_app(cfg, key_path: str = None) -> web.Application:
    app = web.Application()
    app["cfg"] = cfg
    app["store"] = st.Store(cfg.login_ttl, cfg.code_ttl, cfg.token_ttl,
                            cfg.max_sessions)
    app["signer"] = Signer(key_path) if key_path else Signer()
    app.add_routes(ROUTES)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    try:
        cfg = config.load()
    except config.ConfigError as e:
        print("[配置错误] %s" % e)
        print("请复制 config.example.json 为 config.json 并填写，"
              "或设置对应环境变量。")
        sys.exit(1)

    app = build_app(cfg)
    log.info("UniLink 扫码登录服务已启动")
    log.info("  issuer            : %s", cfg.issuer)
    log.info("  authentik         : %s", cfg.authentik_url)
    log.info("  已注册客户端      : %s", "、".join(cfg.clients))
    log.info("  签名 kid          : %s", app["signer"].kid)
    log.info("  发现文档          : %s/.well-known/openid-configuration", cfg.base_url)
    web.run_app(app, host=cfg.host, port=cfg.port, print=None)


if __name__ == "__main__":
    main()

