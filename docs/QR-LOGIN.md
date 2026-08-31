# 扫码登录部署指南

让所有已接入 authentik 的项目获得「手机扫码登录」，无需逐个改造。

## 原理

`auth-server` 是一个**上游 OIDC Provider**，注册进 authentik 后成为一个登录源
（Source）。authentik 会自动把它作为按钮显示在登录页上，而下游项目仍然只跟
authentik 打交道 —— 它们完全不知道扫码这件事存在。

```
浏览器            auth-server              authentik          手机 App
  │ 点“扫码登录”      │                       │                  │
  │ ─────────────────────────────────────────►│                  │
  │                  │◄── 重定向 /authorize ──┤                  │
  │◄── 二维码页面 ───┤                       │                  │
  │                  │◄──── 扫码 + 确认（附 authentik 令牌）─────┤
  │                  │── 用令牌查 userinfo ──►│                  │
  │◄── 轮询到 code ──┤                       │                  │
  │ ── code 回调 ────────────────────────────►│                  │
  │                  │◄── /token 换 id_token ─┤                  │
  │◄──────────── 登录完成，回到原项目 ─────────┤                  │
```

信任链的关键：**auth-server 从不自行判断用户身份**。手机递上 authentik 的
access_token，auth-server 拿它去问 authentik「这是谁」。伪造令牌换不出
userinfo，令牌被吊销立刻失效。签发的 `sub` 直接沿用 authentik 的 `sub`，
因此回连时能匹配到同一个用户，不会重复建号。

## 一、部署 auth-server

```bash
cd unilink/auth-server
pip install -r requirements.txt
cp config.example.json config.json
```

生成一个客户端密钥：

```bash
python -c "import secrets;print(secrets.token_urlsafe(48))"
```

编辑 `config.json`：

| 字段 | 说明 |
|------|------|
| `base_url` | 本服务对外地址，**必须 https**，如 `https://qr.example.com` |
| `authentik_url` | authentik 根地址，如 `https://auth.example.com` |
| `clients[0].client_secret` | 填上一步生成的随机串 |
| `clients[0].redirect_uris` | `https://auth.example.com/source/oauth/callback/unilink-qr/`（`unilink-qr` 为第二步里 Source 的 slug） |
| `allowed_groups` | 可选。限制只有某些 authentik 组能扫码登录 |

启动：

```bash
python app.py
```

首次启动会在 `keys/oidc-rsa.pem` 生成签名私钥（权限 0600）。**这个文件不要删也不要提交**
—— 删了等于换发卡机构，authentik 缓存的公钥会验签失败。

反代示例（Caddy 一行）：

```
qr.example.com {
    reverse_proxy 127.0.0.1:8790
}
```

> ⚠️ 必须走 https。授权码与 authentik 令牌都经过这条链路，明文 http 下
> 同网段的人可以直接抓到令牌冒充你登录任何项目。App 端也会拒绝把令牌
> 发往公网 http 地址。

验证：`curl https://qr.example.com/.well-known/openid-configuration`

## 二、在 authentik 里加为登录源

**Directory → Federation & Social login → Create → OpenID Connect OAuth Source**

| 字段 | 值 |
|------|-----|
| Name | `手机扫码登录`（会显示在登录页按钮上） |
| Slug | `unilink-qr` ← 必须与 `redirect_uris` 里的一致 |
| Consumer key | `unilink-qr`（config.json 的 `client_id`） |
| Consumer secret | config.json 的 `client_secret` |
| OIDC Well-known URL | `https://qr.example.com/.well-known/openid-configuration` |
| Scopes | `openid profile email` |

保存后 authentik 会自动抓取发现文档填好各端点。若报错，先确认这个 URL
在 authentik 容器内部可访问（Docker 部署时 `qr.example.com` 需能解析）。

**把源挂到登录流程上**：Flows → `default-authentication-flow` → Stage Bindings
→ 找到 `default-authentication-identification` → Edit → 在 **Sources** 里勾选刚建的源。

现在 authentik 登录页上就会出现「手机扫码登录」按钮了，所有下游项目通用。

## 三、为手机 App 建一个 OAuth2 Provider

App 自身也要登录 authentik（拿到那张"身份凭证"），走标准的
Authorization Code + PKCE。

**Applications → Providers → Create → OAuth2/OpenID Provider**

| 字段 | 值 |
|------|-----|
| Name | `UniLink 手机端` |
| Client type | **Public** ← 手机 App 无法保管密钥，必须是 public + PKCE |
| Client ID | `unilink-mobile`（与 config.json 的 `app_client_id` 一致） |
| Redirect URIs | `unilink://auth/callback` |
| Scopes | `openid`、`profile`、`email`、`offline_access` |

`offline_access` 用于签发 refresh_token，让 App 能静默续期而不必反复登录。

再建一个 Application 关联这个 Provider（slug 随意，如 `unilink`），
并按需绑定策略限制谁能用。

> 注意：Redirect URI 用的是自定义 scheme。Android 上自定义 scheme 可被
> 恶意应用抢注，因此 PKCE 是**必需**而非可选 —— 即使授权码被截获，
> 没有 code_verifier 也换不出令牌。App 端已强制 S256。

## 四、手机端使用

1. 在 UniLink 主界面「authentik 账号」区域填入 `https://qr.example.com`
2. 点 **登录 authentik** → 系统浏览器打开授权页 → 完成登录后自动回到 App
3. 之后任意时刻点 **扫码登录**，对准电脑上的二维码
4. 手机弹出确认框，显示"将以【谁】的身份登录【哪个应用】"
5. 点确认 → 电脑上自动完成登录

登录状态加密保存在本地（Android Keystore 硬件密钥 + AES-GCM），
重启 App 仍然有效。点「退出」清除本地令牌。

## 排障

| 现象 | 处理 |
|------|------|
| authentik 保存 Source 时报无法获取 well-known | authentik 容器内无法解析/访问 `base_url`。Docker 部署时检查 DNS 或用内网地址 |
| 登录页没有扫码按钮 | Source 没绑定到 identification stage（见第二步最后一段） |
| 扫码后 App 提示"不是 UniLink 登录码" | 二维码里的 `srv` 不是 https。生产环境请配好 TLS |
| App 提示"服务器不一致" | 二维码指向的服务与 App 登录时用的不是同一个 —— 正常情况下这是钓鱼拦截 |
| 确认时提示"令牌已失效" | authentik 侧 refresh_token 过期或被吊销，重新登录即可。若频繁出现，检查 Provider 是否给了 `offline_access` |
| `invalid_client` | config.json 的 `client_secret` 与 authentik Source 里的 Consumer secret 不一致 |
| 二维码总是过期 | 默认 180 秒。若网络慢可调大 `config.json` 的 `login_ttl` |
| 换机后无法登录 | Keystore 密钥不随备份迁移，属预期行为，重新登录即可 |

## 安全边界

* auth-server 只是**中转身份**，不存密码、不存长期令牌（内存会话 3 分钟即过期）。
* 二维码 ticket 是 32 字节随机串，授权码一次性且 60 秒过期。
* **授权码只交给发起登录的那个浏览器**：页面里另有一个不进二维码的
  `poll_secret`，轮询取码必须出示它。因此别人拍下或截屏二维码也拿不到 code ——
  二维码本身只够用来"确认"，不足以"取码"。
* 扫码必须经手机上的人工确认 —— 这是防"把二维码摆到别人面前"的唯一有效手段。
* 手机端拒绝向公网 http 地址发送令牌，也拒绝指向非登录服务器的二维码。
* 手机端令牌用 Android Keystore 硬件密钥加密后落盘，并已从云备份中排除
  （密钥不参与备份，备份出去也解不开）。
* 同时存活的扫码会话有上限（默认 500，见 `max_sessions`），防止刷 `/authorize` 打爆内存。
* 单实例内存存储：重启会让正在扫码的用户重试一次。要多实例横向扩展需把
  `store.py` 换成 Redis 实现。
* `keys/` 与 `config.json` 已在 `.gitignore` 中，切勿提交。

## 附录：二维码与接口约定

二维码内容是一段紧凑 JSON（**不含**浏览器轮询密钥）：

```json
{"v":1,"typ":"unilink-login","srv":"https://qr.example.com","tk":"<32字节随机串>"}
```

手机 App 侧接口（都在 `auth-server` 上）：

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/api/app/config` | 取 authentik 端点与 App 的 client_id |
| POST | `/api/scan/preview` | `{ticket, device}` → 返回将登录的应用名，会话转为 scanned |
| POST | `/api/scan/approve` | `{ticket, access_token}` → 校验身份并生成授权码 |
| POST | `/api/scan/deny` | `{ticket}` → 拒绝本次登录 |

浏览器侧：

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/authorize` | authentik 重定向入口，返回二维码页面 |
| GET | `/api/session/{ticket}?k=<轮询密钥>` | 轮询状态，approved 时返回 code |

标准 OIDC 端点：`/.well-known/openid-configuration`、`/token`、`/userinfo`、`/jwks`。
`/healthz` 返回当前会话数，可用于监控。

## 测试

```bash
cd unilink/auth-server
python -m unittest test_flow -v     # 31 项，含完整流程与各类攻击场景
```

手机端的二维码解析与授权 URL 构造也有 JVM 单元测试（无需设备）：

```bash
cd unilink/android
gradle testDebugUnitTest
```

两者都已接入 GitHub Actions，见 `.github/workflows/`。
