# -*- coding: utf-8 -*-
"""
扫码登录会话状态机（全内存，带 TTL 自动清理）
=============================================

一次扫码登录的生命周期：

  PENDING   浏览器打开 /authorize，生成 ticket 与二维码，等待手机扫码
     │  手机扫码读到 ticket，调用 /api/scan/preview（只读，展示"要登录到 X 吗"）
     ▼
  SCANNED   手机已扫到码但用户还没点确认（浏览器提示"已扫码，请在手机上确认"）
     │  手机调用 /api/scan/approve，附带自己的 authentik 令牌
     ▼
  APPROVED  已确认并持有 authentik 身份；浏览器轮询取回 authorization code
     │  authentik 用 code 换 token
     ▼
  CONSUMED  code 已被使用（一次性），后续重放一律拒绝

任一环节可进入 DENIED（用户在手机上点了拒绝），或直接过期消失。

为什么用内存而非 Redis：单实例部署足够，且会话寿命只有 3 分钟，
重启丢失只会让正在扫码的用户重试一次。多实例横向扩展需替换此模块。
"""
import secrets
import threading
import time

PENDING = "pending"
SCANNED = "scanned"
APPROVED = "approved"
CONSUMED = "consumed"
DENIED = "denied"


class LoginSession:
    __slots__ = ("ticket", "poll_secret", "state", "created", "expires",
                 "client_id", "redirect_uri", "state_param", "nonce",
                 "scope", "code_challenge", "code_challenge_method",
                 "code", "code_expires", "identity",
                 "device_name", "scan_ip")

    def __init__(self, ttl: int):
        self.ticket = secrets.token_urlsafe(32)
        # 浏览器轮询凭据。**不放进二维码**，只嵌在页面里 ——
        # 这样即使有人拍下 / 截屏了二维码，也拿不到授权码：
        # 二维码只够"确认登录"，取码必须是发起登录的那个浏览器。
        self.poll_secret = secrets.token_urlsafe(24)
        self.state = PENDING
        self.created = time.time()
        self.expires = self.created + ttl
        # 下游（authentik）发起授权请求时携带的参数，回调时需原样奉还
        self.client_id = ""
        self.redirect_uri = ""
        self.state_param = ""
        self.nonce = ""
        self.scope = "openid profile email"
        self.code_challenge = ""
        self.code_challenge_method = ""
        # 授权码
        self.code = ""
        self.code_expires = 0.0
        # 手机确认后写入的 authentik 身份
        self.identity = None
        self.device_name = ""
        self.scan_ip = ""

    @property
    def alive(self) -> bool:
        return time.time() < self.expires

    def public(self) -> dict:
        """给浏览器轮询用的状态视图"""
        d = {"state": self.state,
             "expires_in": max(0, int(self.expires - time.time()))}
        if self.state == SCANNED and self.device_name:
            d["device"] = self.device_name
        if self.state == APPROVED:
            d["code"] = self.code
            d["redirect_uri"] = self.redirect_uri
            d["state_param"] = self.state_param
            if self.identity:
                d["user"] = (self.identity.get("preferred_username")
                             or self.identity.get("email") or "")
        return d


class Store:
    def __init__(self, login_ttl: int, code_ttl: int, token_ttl: int,
                 max_sessions: int = 500):
        self.login_ttl = login_ttl
        self.code_ttl = code_ttl
        self.token_ttl = token_ttl
        self.max_sessions = max_sessions
        self._lock = threading.RLock()
        self._by_ticket = {}
        self._by_code = {}
        self._tokens = {}      # access_token -> {"identity":…, "exp":…, "client_id":…}
        self._last_gc = 0.0

    # ---------- 授权请求 ----------

    def create(self, **kw) -> LoginSession:
        s = LoginSession(self.login_ttl)
        for k, v in kw.items():
            setattr(s, k, v)
        with self._lock:
            # 常规按 20 秒节流清理；接近上限时强制清一次，
            # 因为过期会话很可能占了大头，清完就不必淘汰活跃会话。
            near_cap = len(self._by_ticket) >= self.max_sessions
            self._gc_locked(force=near_cap)
            if len(self._by_ticket) >= self.max_sessions:
                # 仍然触顶：淘汰最早创建的 10%（至少 1 个）。会话寿命只有几分钟，
                # 正常使用绝不会触顶，触顶说明有人在刷 /authorize。
                drop = max(1, self.max_sessions // 10)
                for t in sorted(self._by_ticket,
                                key=lambda k2: self._by_ticket[k2].created)[:drop]:
                    self._by_ticket.pop(t, None)
            self._by_ticket[s.ticket] = s
        return s

    def get(self, ticket: str):
        with self._lock:
            s = self._by_ticket.get(ticket or "")
            if s is None:
                return None
            if not s.alive:
                self._by_ticket.pop(s.ticket, None)
                return None
            return s

    # ---------- 手机侧动作 ----------

    def mark_scanned(self, ticket: str, device_name: str, ip: str):
        with self._lock:
            s = self.get(ticket)
            if s is None or s.state != PENDING:
                return s
            s.state = SCANNED
            s.device_name = (device_name or "")[:40]
            s.scan_ip = ip
            return s

    def approve(self, ticket: str, identity: dict):
        """写入身份并生成一次性授权码"""
        with self._lock:
            s = self.get(ticket)
            if s is None or s.state not in (PENDING, SCANNED):
                return None
            s.identity = identity
            s.code = secrets.token_urlsafe(36)
            s.code_expires = time.time() + self.code_ttl
            s.state = APPROVED
            self._by_code[s.code] = s
            return s

    def deny(self, ticket: str):
        with self._lock:
            s = self.get(ticket)
            if s is None or s.state in (APPROVED, CONSUMED):
                return None
            s.state = DENIED
            return s

    # ---------- 授权码兑换 ----------

    def consume_code(self, code: str):
        """成功返回 (session, None)；失败返回 (None, 原因)"""
        with self._lock:
            s = self._by_code.pop(code or "", None)
            if s is None:
                return None, "授权码无效或已被使用"
            if s.state != APPROVED:
                return None, "授权码状态异常（%s）" % s.state
            if time.time() > s.code_expires:
                s.state = CONSUMED
                return None, "授权码已过期"
            s.state = CONSUMED
            return s, None

    # ---------- 本服务签发的 access_token ----------

    def issue_token(self, identity: dict, client_id: str) -> str:
        tok = secrets.token_urlsafe(40)
        with self._lock:
            self._tokens[tok] = {"identity": identity,
                                 "client_id": client_id,
                                 "exp": time.time() + self.token_ttl}
        return tok

    def token_identity(self, token: str):
        with self._lock:
            rec = self._tokens.get(token or "")
            if rec is None:
                return None
            if time.time() > rec["exp"]:
                self._tokens.pop(token, None)
                return None
            return rec["identity"]

    # ---------- 清理 ----------

    def _gc_locked(self, force: bool = False):
        now = time.time()
        if not force and now - self._last_gc < 20:
            return
        self._last_gc = now
        for t, s in list(self._by_ticket.items()):
            if not s.alive:
                self._by_ticket.pop(t, None)
        for c, s in list(self._by_code.items()):
            if now > s.code_expires:
                self._by_code.pop(c, None)
        for k, rec in list(self._tokens.items()):
            if now > rec["exp"]:
                self._tokens.pop(k, None)

    def stats(self) -> dict:
        with self._lock:
            return {"sessions": len(self._by_ticket),
                    "codes": len(self._by_code),
                    "tokens": len(self._tokens)}
