# UniLink —— 手机 ⇄ 电脑 互联助手

一套**手机与电脑互通消息**的完整方案：配套 PC 端与 Android 端软件，
可把**状态栏的所有通知**在两台设备间互相同步，还支持文字消息、剪贴板同步和文件互传。

```
┌──────────────┐   WebSocket   ┌────────────┐   WebSocket   ┌──────────────┐
│  Android 手机 │ ◄──────────► │ 中继服务器   │ ◄──────────► │   Windows PC │
│ Notification │   (AES-GCM    │ server.py  │   (AES-GCM    │  main.py GUI │
│ Listener 抓取 │   端到端加密)  │  只转发不解密│   端到端加密)  │  Tkinter     │
│ 状态栏全部通知 │               └────────────┘               └──────────────┘
└──────────────┘
```

## 功能总览

| 功能 | 手机 → 电脑 | 电脑 → 手机 |
|------|:---:|:---:|
| 文字消息 | ✅ | ✅ |
| **状态栏/系统通知镜像** | ✅（NotificationListenerService） | ✅（WinRT UserNotificationListener） |
| **在电脑上直接回复手机通知** | — | ✅（无障碍自动填写发送，失败自动回落） |
| 剪贴板同步 | ✅ | ✅ |
| 文件传输 | ✅ 接收 | ✅ 发送 |
| 端到端加密 AES-256-GCM | ✅ | ✅ |
| **扫码登录 authentik 项目** | ✅（手机扫码授权） | — |

> 🔐 **扫码登录**：手机登录一次 authentik 后，之后在任意接入 authentik OIDC 的
> 项目登录页上点「手机扫码登录」，用 UniLink 扫码确认即可完成登录 ——
> 无需为每个项目单独改造。部署见 **[docs/QR-LOGIN.md](docs/QR-LOGIN.md)**。

## 目录结构

```
unilink/
├─ README.md
├─ docs/PROTOCOL.md          # 通信协议与加密算法说明
├─ docs/QR-LOGIN.md          # ★ 扫码登录部署指南（authentik 接入）
├─ server/server.py          # 中继服务器（Python）
├─ auth-server/              # ★ 扫码登录服务（OIDC Provider，authentik 的上游源）
│  ├─ app.py  config.py  store.py  identity.py
│  ├─ jwtutil.py  qr.py  pages.py  test_flow.py
├─ pc-client/                # Windows PC 客户端（Python + Tkinter）
│  ├─ main.py  net.py  proto.py  cryptobox.py
│  ├─ toasts.py  win_notifs.py  requirements.txt
├─ android/                  # Android 客户端（Kotlin，Android Studio 工程）
│  └─ app/src/main/java/com/unilink/app/
│     ├─ MainActivity.kt     # 界面与权限引导
│     ├─ LinkService.kt      # 前台服务：WebSocket 收发 / 弹通知 / 存文件
│     ├─ NotifCaptureService.kt  # ★ 抓取状态栏所有通知
│     ├─ Hub.kt  Proto.kt(内含)  CryptoBox.kt  Prefs.kt
│     └─ auth/               # ★ 扫码登录：authentik OIDC + 本地会话
│        ├─ LoginActivity.kt  ScanLoginActivity.kt
│        ├─ AuthClient.kt  AuthSession.kt  SecureStore.kt  QrTicket.kt
└─ tools/test_client.html    # 浏览器联调页
```

## 快速开始（3 步）

### 第 1 步：启动服务器（跑在哪台机器都行，通常就是你的电脑）

```bash
cd unilink/server
pip install -r requirements.txt
python server.py --port 8765 --token 换成你的口令
```

查看电脑局域网 IP（Windows：`ipconfig`，如 `192.168.1.100`）。
手机与电脑需能访问该地址（同一 Wi-Fi 或服务器有公网 IP）。

> 公网部署：建议用 Caddy/Nginx 反代为 `wss`。Caddy 一行配置：
> `unilink.example.com { reverse_proxy 127.0.0.1:8765 }`

### 第 2 步：运行电脑客户端

**推荐直接双击 `pc-client\run.bat`**（自动用同一个解释器装依赖并启动）。

或手动执行：

```bash
cd unilink/pc-client
py -m pip install -r requirements.txt
py main.py
```

> ⚠️ 注意：`pip install ...` 和 `py main.py` 必须是**同一个解释器**。
> 若电脑装有多个 Python，`pip` 与 `py` 可能指向不同版本，导致
> "已安装却 ModuleNotFoundError"。请始终成对使用 `py -m pip install ...`
> 与 `py main.py`；可用 `py -0p` 查看所有已注册的解释器。

界面中填写：
* **服务器** `ws://192.168.1.100:8765/ws`
* **房间码** 两端一致即可（4-32 位）
* **令牌** 与服务器 `--token` 相同

点「连接」。状态栏出现 `已连接 · 🔐 AES-GCM` 即成功。

> 💡 可选增强：想让**电脑自己的系统通知**也同步到手机？
> 见下方[常见问题](#常见问题)中「电脑自己的通知没转发给手机」条目，
> 按你的 Python 版本安装对应依赖即可；不装不影响其它所有功能。

### 第 3 步：安装 Android 客户端

**方式 A（推荐，无需本地环境）—— GitHub Actions 云端构建：**

> 🚀 一键准备：双击根目录 **`git-init.bat`**，按提示粘贴你新建的空仓库地址，
> 自动完成 git 初始化、提交与推送（需已安装 [Git](https://git-scm.com/download/win)）。

1. 把 `unilink/` 推到你的 GitHub 仓库（建议 Private；`.gitignore` 已排除
   `config.json` 等含令牌的本地文件）
2. 仓库页 → **Actions** → 若提示启用 workflow 则点确认 →
   **Build Android APK** → **Run workflow**
3. 构建完成后在该次运行底部 **Artifacts** 下载 `UniLink-debug-apk.zip`，
   解压得到 `app-debug.apk`，传到手机安装
（流水线配置见 `.github/workflows/android-build.yml`）

**方式 B —— 本地 Android Studio：**
用 **Android Studio（Hedgehog 2023.1.1+）打开 `unilink/android`**，连上手机点 Run ▶；
或命令行：`cd android && gradle wrapper && ./gradlew assembleDebug`，
APK 位于 `app/build/outputs/apk/debug/`。

App 内：
1. 填服务器地址 / 房间码 / 令牌 → 点 **「连接并保持后台」**
2. 点 **「授予通知使用权」**，在系统设置里允许 UniLink（★ 同步状态栏消息的关键授权）
3. 点 **「允许弹出通知」**（Android 13+ 需要）
4. （可选，推荐）点 **「开启无障碍」** 并允许「UniLink 自动回复」——
   这样在电脑上回复手机通知时可全自动发送；不开启则退化为“复制+打开应用”

之后手机收到的每条状态栏通知都会实时出现在电脑上；电脑端输入框可直接回信。

### 第 4 步（可选）：启用扫码登录

想让手机变成「扫码登录所有 authentik 项目」的钥匙？部署 `auth-server` 并把它
注册为 authentik 的一个登录源即可，下游项目**零改造**。

镜像已发布到 GHCR，服务器上两个文件就能跑：

```bash
mkdir -p /opt/unilink-auth && cd /opt/unilink-auth
curl -O  https://raw.githubusercontent.com/Guyao146/unilink/main/deploy/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/Guyao146/unilink/main/deploy/.env.example
vi .env          # 填客户端密钥与两个 URL
docker compose up -d
```

再配一层反代（Caddy 一行 `gateway.example.com { reverse_proxy 127.0.0.1:8790 }`），
以及 authentik 里的两处 Web 配置。完整步骤（含 systemd / 源码构建等其它部署方式）见
**[docs/QR-LOGIN.md](docs/QR-LOGIN.md)**，一键部署速查见 **[deploy/README.md](deploy/README.md)**。

## 各功能使用说明

* **发消息**：PC 底部输入框回车发送；手机收到后弹系统通知。反向同理。
* **通知镜像**：默认开启双向开关。手机每条通知 → 电脑弹 **真实 Windows 系统 Toast**
  （右下角弹出、进操作中心；MyDockFinder 等 dock 软件不会拦截系统通知）+ 日志；
  电脑每条系统通知 → 手机状态栏（标题带 💻 前缀）。自动过滤自身回环与重复内容。
  > 首次弹 Toast 时会自动在开始菜单创建带 AUMID 的 `UniLink.lnk`
  > （未打包应用发系统通知的前置条件），仅执行一次。
* **回复手机通知**：PC 点「回复通知…」，选中一条手机通知并输入内容：
  * 弹窗中每条通知带 **⟨适配模式⟩ 徽标**（微信模式/QQ模式/Telegram模式/通用模式），
    选中后下方显示手机端将采用的回复方式说明与降级警告；
    模式来自**手机端动态同步的能力表**——在 `AppRules.kt` 里新增规则后无需改 PC 端；
  * 手机无障碍已开启 → 全自动：拉下通知栏、点“回复”、填入文本、点“发送”，结果回报 PC；
    发送完成后**自动收起通知栏并回到原来的应用**；
  * 内置 **微信 / QQ·TIM / Telegram** 专属适配规则（`AppRules.kt`）：
    微信无回复按钮会自动点开通知进会话、语音模式自动切回键盘；QQ 走“快速回复”动作；
    Telegram 走内联回复（图标按钮靠 contentDescription 匹配）；
  * 未开启 / 屏幕锁定 / 控件识别失败 → 回复自动复制到手机剪贴板并打开来源 App，
    手动粘贴即可（PC 会收到失败原因提示）。
* **剪贴板**：勾选「剪贴板自动同步」后复制即传；也可手动点「发剪贴板」按钮。
* **文件**：PC 点「发文件…」，手机自动保存到 `下载/UniLink` 并弹完成通知（≤200MB）。
* **扫码登录**：手机端主界面「authentik 账号」区域填入扫码服务地址 → 点
  「登录 authentik」（系统浏览器完成 OIDC + PKCE，登录态加密存本地）→
  之后在任意接入 authentik 的项目登录页点「手机扫码登录」，App 点「扫码登录」
  对准二维码，手机上确认后电脑自动完成登录。
  完整部署步骤见 [docs/QR-LOGIN.md](docs/QR-LOGIN.md)。

## 安全模型

* 房间码 + 访问令牌共同派生 32 字节密钥（PBKDF2-HMAC-SHA256 ×120000），
  所有业务 payload 用 **AES-256-GCM** 加密——服务器只转发密文。
* 令牌即密码：请使用强口令；浏览器测试页无加密能力，加入会强制房间降级为明文，
  正式使用时不要让 Web 页面进入房间。
* 详细协议见 [docs/PROTOCOL.md](docs/PROTOCOL.md)。

**扫码登录部分**（独立于上面的消息互通，两者互不影响）：

* auth-server 只**中转身份**：手机递上 authentik 令牌，服务端拿它去问 authentik
  「这是谁」。伪造令牌换不出身份，令牌被吊销立刻失效。服务端不存密码、不存长期令牌。
* 签发的 `sub` 沿用 authentik 的 `sub`，因此回连时匹配到同一用户，不会重复建号。
* 二维码 ticket 为 32 字节随机串（3 分钟过期），授权码一次性且 60 秒过期，
  支持并强制校验下游传来的 PKCE。
* **授权码只交给发起登录的那个浏览器**：页面里另有一个不进二维码的轮询密钥，
  因此别人拍下或截屏二维码也拿不到 code。
* **扫码后必须在手机上人工确认**，确认框显示"以谁的身份登录到哪个应用"——
  这是防"把二维码摆到别人面前"的唯一有效手段。
* 手机端令牌用 Android Keystore 硬件密钥加密后落盘；拒绝向公网 http 发送令牌；
  拒绝指向非本机登录服务器的二维码。
* App 是 public client，**强制 PKCE S256** —— Android 自定义 scheme 可被抢注，
  没有 PKCE 时授权码被截获即等于账号失守。
* 详见 [docs/QR-LOGIN.md](docs/QR-LOGIN.md) 的「安全边界」一节。

## 常见问题

| 问题 | 处理 |
|------|------|
| **已安装包仍报 ModuleNotFoundError** | `pip` 与 `py` 指向了不同解释器。成对执行：`py -m pip install -r requirements.txt` + `py main.py`；用 `py -0p` 查看全部解释器；或直接运行 `run.bat` |
| 连不上服务器 | 防火墙放行 8765/TCP；确认 IP、端口、令牌正确；手机与电脑同网段 |
| 手机收不到任何电脑通知 | 「允许弹出通知」未授予；或 App 被电池优化杀死 → 加入电池优化白名单 |
| 电脑收不到手机通知 | 「授予通知使用权」被系统回收，重新开启；部分厂商需再关掉 UniLink 的省电限制 |
| 电脑自己的通知没转发给手机 | 需安装通知捕获依赖：Python 3.7~3.11 执行 `pip install winsdk==1.0.0b10`；3.12+ 见 `pc-client/requirements.txt` 内的 winrt-* 说明。安装后还需在 设置→隐私和安全性→通知 中允许访问；若系统拒绝未打包应用，此单向功能自动停用（不影响其它方向） |
| 自动回复失败 / 找不到回复按钮 | 1) 确认已开启「无障碍」且 UniLink 在无障碍列表中处于开启状态；2) 手机需亮屏解锁；3) 目标通知必须还留在通知栏里；4) 个别应用/厂商的控件文案不在中英文匹配范围内，会自动回落为“复制+打开应用”，粘贴发送即可 |
| 显示"明文模式" | 未装 `cryptography`，或房间里混入了浏览器测试页 |
| 电脑弹窗不是系统通知/想接入 MyDockFinder | v1.1 起已默认发送**真实 Windows 系统 Toast**（右下角弹出、进操作中心）。MyDockFinder 没有公开的通知接入 API，但它不会拦截系统通知，Toast 在 dock 环境下照常显示。若首次弹不出：删除 `%APPDATA%\Microsoft\Windows\Start Menu\Programs\UniLink.lnk` 和 `%LOCALAPPDATA%\UniLink\aumid.ok` 后重试（会重建 AUMID 快捷方式） |
| iOS 支持？ | iOS 系统不允许第三方读取通知中心，无法实现同等级功能 |
| **扫码登录：登录页没有扫码按钮** | authentik 里的 OAuth Source 没绑定到 identification stage，见 [QR-LOGIN.md](docs/QR-LOGIN.md) 第二步 |
| 扫码后提示"不是 UniLink 登录码" | 二维码里的服务地址不是 https。出于安全考虑 App 拒绝把账号令牌发往公网 http |
| 扫码后提示"服务器不一致" | 二维码指向的服务与 App 登录时用的不是同一个 —— 正常情况下这是钓鱼拦截，属预期行为 |
| 确认时提示"令牌已失效" | authentik 侧 refresh_token 过期或被吊销，重新登录即可；若频繁出现，检查 Provider 是否授予了 `offline_access` |
| 换手机后无法扫码登录 | 令牌加密密钥存在 Android Keystore 中，不随备份迁移，属预期行为，重新登录即可 |
| 扫码时相机没反应 | 首次使用会请求相机权限；若之前拒绝过，到 系统设置→应用→UniLink→权限 中手动开启 |

## 已知限制与后续计划

- [ ] 通知图片/附件提取
- [x] v1：文字 / 通知 / 剪贴板 / 文件 / E2E 加密
- [x] v1.1：在电脑上直接回复手机通知（无障碍自动化 + 复制回落）
- [x] PC 端真实 Windows 系统 Toast（自动 AUMID 引导）
- [x] GitHub Actions 云端自动构建 APK
- [x] v1.2：手机扫码登录所有接入 authentik OIDC 的项目
- [ ] PyInstaller 单文件 exe 打包
- [ ] 局域网 mDNS 自动发现服务器（免手填 IP）
- [ ] auth-server 多实例部署（现为单实例内存会话）

> **自动回复的实现边界**：Android 禁止第三方直接触发他应用通知里的 RemoteInput 动作，
> 因此全自动回复依赖无障碍服务模拟点击（与 Pushbullet 同方案）。
> 匹配关键词覆盖中/英文常见文案（回复/答复/Reply、发送/Send），个别定制 UI 可能识别失败，
> 此时自动回落为“复制内容并打开来源 App”。
>
> **应用专属适配表**：位于 `android/.../AppRules.kt`，已内置微信（点通知本体进会话 +
> 语音模式自动切键盘）、QQ/TIM（快速回复动作）、Telegram 及常见分支（内联回复）。
> 发送完成后手机会自动收起通知栏并通过包名校验回到触发前的原应用；
> 新增适配只需向 `AppRules.rules` 追加一条 `AppRule`。

---
UniLink v1.2 · 协议 v1 · 扫码登录基于 OIDC · 仅供学习与个人使用
