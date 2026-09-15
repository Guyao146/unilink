# -*- coding: utf-8 -*-
"""
后台配置：首次启动向导 + 常驻管理面板
========================================

两种令牌：

  setup_token  一次性。服务检测到尚未配置时生成，打印到日志并显示在向导页面上；
               用一次即删 —— 配置完成后 /setup 不再可用，无法被用来重置配置。

  admin_token  常驻面板的登录凭据。首次配置完成时生成，**明文只显示一次**，
               磁盘上只存 sha256。忘了就删掉 data/admin.hash 再重启，
               服务会重新回到「未配置」状态走一遍向导。

为什么 admin token 只存哈希：它能改 client_secret，等于掌握整个登录入口，
落盘的东西必须不可逆。即便如此也请把它当密码保管，并确保 /admin 只经
https 反代访问。

网页配置写入 data/state.json，与其它配置来源的优先级是：
    环境变量 > 本地 config.json > 网页后台 state
即 .env 里写死的值永远优先，网页面板改不动它（适合「配置固化在编排文件里」
的场景）；.env 没写的项才由网页面板接管。
"""
import hashlib
import json
import os
import re
import secrets
import threading
import time

import config as cfgmod

SETUP_TOKEN_PATH = os.path.join(cfgmod.STATE_DIR, "setup.token")
ADMIN_HASH_PATH = os.path.join(cfgmod.STATE_DIR, "admin.hash")

# 可调的整数项：键 -> (中文说明, 下限, 上限)
INT_FIELDS = {
    "login_ttl":    ("二维码 / 登录会话有效期（秒）", 30, 86400),
    "code_ttl":     ("授权码有效期（秒）", 10, 3600),
    "token_ttl":    ("本服务签发令牌的有效期（秒）", 60, 86400),
    "max_sessions": ("同时存活的扫码会话上限", 1, 100000),
}

URL_FIELDS = ("base_url", "authentik_url")

# 登录会话 cookie 与 CSRF cookie 的有效期（秒）
SESSION_MAX_AGE = 7200


# ======================================================================
# 令牌
# ======================================================================

def _write_file(path: str, text: str, mode: int = 0o600) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def _read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def setup_token() -> str:
    """返回当前有效的 setup token；不存在时生成并打印到日志（只打印这一次）"""
    tok = _read_file(SETUP_TOKEN_PATH)
    if tok:
        return tok
    tok = secrets.token_urlsafe(32)
    _write_file(SETUP_TOKEN_PATH, tok)
    print("[首次配置] 服务尚未配置完成，请打开反代后的 https 地址的 /setup")
    print("[首次配置] 输入下面这个一次性令牌（也可在 data/setup.token 文件里找到）：")
    print("[首次配置] %s" % tok)
    return tok


def consume_setup_token(given: str) -> bool:
    """校验 setup token，成功后立即删除（一次性）"""
    ok = secrets.compare_digest((given or "").strip(), setup_token())
    if ok:
        try:
            os.remove(SETUP_TOKEN_PATH)
        except OSError:
            pass
    return ok


def issue_admin_token() -> str:
    """生成新的 admin token，返回明文（只此一次）；磁盘上只留 sha256"""
    tok = secrets.token_urlsafe(40)
    _write_file(ADMIN_HASH_PATH, hashlib.sha256(tok.encode("utf-8")).hexdigest())
    return tok


def check_admin_token(given: str) -> bool:
    h = _read_file(ADMIN_HASH_PATH)
    if not h or not given:
        return False
    digest = hashlib.sha256((given or "").encode("utf-8")).hexdigest()
    return secrets.compare_digest(digest, h)


def admin_exists() -> bool:
    return bool(_read_file(ADMIN_HASH_PATH))


# ======================================================================
# 表单校验
# ======================================================================

def _g(form, key: str) -> str:
    """兼容 aiohttp multidict 与普通 dict"""
    try:
        v = form.get(key, "")
    except AttributeError:
        v = form[key]
    return "" if v is None else str(v)


def _split_list(raw: str) -> list:
    """「a@x.com, b@x.com  c@x.com」→ ['a@x.com','b@x.com','c@x.com']"""
    return [t for t in re.split(r"[,\s]+", (raw or "").strip()) if t]


def _optional_fields(form) -> tuple:
    """解析可选项，返回 (dict, [错误])"""
    out, errs = {}, []
    for key in ("app_client_id", "app_redirect_uri", "app_scopes"):
        v = _g(form, key).strip()
        if v:
            out[key] = v
    for key, (label, lo, hi) in INT_FIELDS.items():
        raw = _g(form, key).strip()
        if not raw:
            continue
        try:
            v = int(raw)
        except ValueError:
            errs.append("%s 不是整数" % label)
            continue
        if not (lo <= v <= hi):
            errs.append("%s 需在 %d ~ %d 之间" % (label, lo, hi))
            continue
        out[key] = v
    for key in ("allowed_subs", "allowed_groups"):
        out[key] = _split_list(_g(form, key))
    return out, errs


def _parse_common(form, secret_required: bool, current_secret: str) -> tuple:
    """公共字段。返回 (values, errs)。client_secret 留空 = 沿用现有值"""
    values = {
        "base_url":      _g(form, "base_url").strip().rstrip("/"),
        "authentik_url": _g(form, "authentik_url").strip().rstrip("/"),
        "client_id":     _g(form, "client_id").strip() or "unilink-qr",
    }
    secret = _g(form, "client_secret").strip()
    errs = []
    for key in URL_FIELDS:
        if not values[key]:
            errs.append("缺少 %s" % key)
        elif not values[key].startswith(("http://", "https://")):
            errs.append("%s 必须以 http:// 或 https:// 开头" % key)
    if not values["client_id"]:
        errs.append("缺少 client_id")

    if secret:
        if len(secret) < 24:
            errs.append("client_secret 至少 24 位（用 "
                        "python3 -c \"import secrets;print(secrets.token_urlsafe(48))\" 生成）")
        else:
            values["client_secret"] = secret
    elif current_secret:
        values["client_secret"] = current_secret   # 面板留空 = 不改
    else:
        errs.append("缺少 client_secret（至少 24 位）")
    return values, errs


def validate_setup_form(form) -> tuple:
    """首次配置表单校验。返回 (可保存的 state dict, 错误信息或 None)"""
    values, errs = _parse_common(form, secret_required=True, current_secret="")
    opt, opt_errs = _optional_fields(form)
    errs.extend(opt_errs)
    if errs:
        return None, "；".join(errs)
    values.update(opt)
    return values, None


def validate_admin_form(form, current_secret: str = "") -> tuple:
    """管理面板表单校验。client_secret 留空表示沿用现有值"""
    values, errs = _parse_common(form, secret_required=False,
                                 current_secret=current_secret)
    opt, opt_errs = _optional_fields(form)
    errs.extend(opt_errs)
    if errs:
        return None, "；".join(errs)
    values.update(opt)
    return values, None


def current_state(cfg=None) -> dict:
    """管理面板回填用的「当前生效值」。密钥不回填（不明文显示）"""
    if cfg is None:
        return _read_state()
    first_client = next(iter(cfg.clients.values()), None)
    return {
        "base_url":         cfg.base_url,
        "authentik_url":    cfg.authentik_url,
        "client_id":        (first_client.client_id if first_client else ""),
        "client_secret":    "",
        "app_client_id":    cfg.app_client_id,
        "app_redirect_uri": cfg.app_redirect_uri,
        "app_scopes":       cfg.app_scopes,
        "login_ttl":        cfg.login_ttl,
        "code_ttl":         cfg.code_ttl,
        "token_ttl":        cfg.token_ttl,
        "max_sessions":     cfg.max_sessions,
        "allowed_subs":     sorted(cfg.allowed_subs),
        "allowed_groups":   sorted(cfg.allowed_groups),
    }


def _read_state() -> dict:
    try:
        with open(cfgmod.STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except OSError:
        return {}
    except json.JSONDecodeError:
        return {}


# ======================================================================
# 登录会话（服务端内存，重启即失效）
# ======================================================================

_sessions = {}
_lock = threading.Lock()


def _purge_locked():
    now = time.time()
    for sid in [k for k, v in _sessions.items() if now > v["exp"]]:
        _sessions.pop(sid, None)


def create_session() -> tuple:
    """登录成功时调用，返回 (sid, csrf)。sid 放 HttpOnly cookie，csrf 双重提交防 CSRF"""
    sid = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    with _lock:
        _purge_locked()
        _sessions[sid] = {"csrf": csrf, "exp": time.time() + SESSION_MAX_AGE}
    return sid, csrf


def session_csrf(sid) -> str:
    """返回该会话的 CSRF token；会话无效返回空串"""
    with _lock:
        rec = _sessions.get(sid or "")
        if not rec:
            return ""
        if time.time() > rec["exp"]:
            _sessions.pop(sid, None)
            return ""
        return rec["csrf"]


def drop_session(sid) -> None:
    with _lock:
        _sessions.pop(sid or "", None)

