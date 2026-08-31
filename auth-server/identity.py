# -*- coding: utf-8 -*-
"""
authentik 身份校验
==================
手机 App 在本地保存的是 authentik 颁发的 access_token（PKCE 流程取得）。
扫码确认时 App 把该 token 送到本服务，本服务**不解析也不信任** token 内容，
而是拿它去调 authentik 的 userinfo 端点换取身份 —— 这样：

  * 无需在本服务里配置 authentik 的签名公钥；
  * token 被吊销 / 过期时立即失效（authentik 会返回 401）；
  * 手机端不可能伪造身份（伪造 token 换不出 userinfo）。

代价是每次扫码确认多一次到 authentik 的内网请求，可忽略。
"""
import aiohttp


class IdentityError(RuntimeError):
    def __init__(self, msg, status=401):
        super().__init__(msg)
        self.status = status


async def fetch_identity(session: aiohttp.ClientSession,
                         userinfo_url: str, access_token: str) -> dict:
    """用 authentik access_token 换取用户身份；失败抛 IdentityError"""
    if not access_token:
        raise IdentityError("缺少 authentik 令牌")
    try:
        async with session.get(
                userinfo_url,
                headers={"Authorization": "Bearer " + access_token},
                timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 401:
                raise IdentityError("authentik 令牌已失效，请在手机上重新登录")
            if r.status != 200:
                body = (await r.text())[:200]
                raise IdentityError("authentik userinfo 返回 %d: %s" % (r.status, body),
                                    status=502)
            info = await r.json()
    except aiohttp.ClientError as e:
        raise IdentityError("无法连接 authentik: %s" % e, status=502)

    sub = str(info.get("sub") or "")
    if not sub:
        raise IdentityError("authentik 未返回 sub，无法确定用户身份", status=502)
    return info


def check_allowed(info: dict, allowed_subs: frozenset,
                  allowed_groups: frozenset) -> str:
    """返回 None 表示允许；否则返回拒绝原因。两个白名单都为空 = 不限制。"""
    if allowed_subs and str(info.get("sub")) not in allowed_subs:
        return "该 authentik 账号不在扫码登录白名单中"
    if allowed_groups:
        groups = info.get("groups") or []
        if isinstance(groups, str):
            groups = [groups]
        if not (allowed_groups & set(str(g) for g in groups)):
            return "该账号所属组无扫码登录权限（需属于：%s）" % "、".join(sorted(allowed_groups))
    return None


def build_claims(info: dict) -> dict:
    """把 authentik userinfo 收敛成本服务下发给下游的标准 OIDC claims。

    注意 sub 直接沿用 authentik 的 sub —— 这样 authentik 通过本 Source
    回连时能稳定匹配到同一个用户，不会重复建号。
    """
    out = {"sub": str(info.get("sub"))}
    for k in ("email", "email_verified", "name", "given_name", "family_name",
              "preferred_username", "nickname", "groups"):
        if k in info and info[k] is not None:
            out[k] = info[k]
    return out
