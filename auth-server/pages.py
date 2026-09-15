# -*- coding: utf-8 -*-
"""
扫码登录页面（无前端框架，单文件内联）
======================================
页面职责很窄：显示二维码 + 轮询会话状态 + 拿到 code 后跳回 authentik。
所有动态值都经 _esc 转义后注入，ticket 只放进 JS 字符串常量。
"""
import html as _html
import json


def _esc(s) -> str:
    return _html.escape(str(s or ""), quote=True)


_CSS = """
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;
 justify-content:center;background:#0f1420;color:#e5e7eb;
 font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif}
.card{background:#171d2b;border:1px solid #263041;border-radius:16px;
 padding:32px 36px;width:min(92vw,400px);text-align:center;
 box-shadow:0 12px 40px rgba(0,0,0,.45)}
h1{font-size:19px;margin:0 0 4px}
.sub{font-size:13px;color:#94a3b8;margin:0 0 22px}
.qr{background:#fff;padding:12px;border-radius:12px;display:inline-block;
 line-height:0;transition:opacity .2s}
.qr.dim{opacity:.25}
.state{margin:18px 0 4px;font-size:14px;min-height:22px}
.state b{color:#fff}
.hint{font-size:12px;color:#8b95a7;margin:0}
.err{color:#fca5a5}
.ok{color:#86efac}
.warn{color:#fcd34d}
button{margin-top:16px;padding:9px 22px;font-size:14px;cursor:pointer;
 background:#2563eb;color:#fff;border:0;border-radius:8px}
button:hover{background:#1d4ed8}
button[hidden]{display:none}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;
 background:#60a5fa;margin-right:6px;animation:p 1.2s infinite}
@keyframes p{0%,100%{opacity:.3}50%{opacity:1}}

/* 后台配置表单 */
.fld{margin:14px 0;text-align:left}
.fld label{display:block;font-size:12px;color:#94a3b8;margin:0 0 5px}
.fld input{width:100%;padding:9px 11px;font-size:14px;color:#e5e7eb;
 background:#0f1420;border:1px solid #263041;border-radius:8px}
.fld input:focus{outline:0;border-color:#2563eb}
.fld .h{font-size:11px;color:#8b95a7;margin:4px 0 0}
.grp{border-top:1px solid #263041;margin:20px 0 4px;padding-top:4px}
.grp b{display:block;font-size:12px;color:#8b95a7;margin:10px 0 4px;letter-spacing:.5px}
.msg{font-size:13px;color:#fca5a5;margin:0 0 12px;text-align:left}
.note{font-size:12px;color:#8b95a7;text-align:left;margin:0 0 16px;line-height:1.7}
.notice{font-size:13px;color:#86efac;margin:0 0 14px;text-align:left;
 line-height:1.7;background:#0f2b1a;border:1px solid #1f4d33;
 border-radius:8px;padding:10px 12px}
.notice b{color:#bbf7d0}
"""

_JS = """
(function(){
 var TK=%(ticket)s, KEY=%(key)s, BASE=%(base)s, TTL=%(ttl)d;
 var elState=document.getElementById('state'),
     elHint=document.getElementById('hint'),
     elQr=document.getElementById('qr'),
     elBtn=document.getElementById('again');
 var left=TTL, done=false, timer=null, tick=null;

 function say(html,cls){elState.className='state '+(cls||'');elState.innerHTML=html;}
 function hint(t){elHint.textContent=t||'';}
 function stop(){done=true;if(timer)clearInterval(timer);if(tick)clearInterval(tick);}

 tick=setInterval(function(){
   if(done)return;
   left--;
   if(left<=0){stop();expire();return;}
   if(left<=30)hint('二维码将在 '+left+' 秒后过期');
 },1000);

 function expire(){
   elQr.classList.add('dim');
   say('二维码已过期','warn');hint('');
   elBtn.hidden=false;
 }

 function go(d){
   stop();
   say('<b>'+(d.user?d.user:'')+'</b> 登录成功，正在跳转…','ok');
   var u=d.redirect_uri+(d.redirect_uri.indexOf('?')>=0?'&':'?')+
         'code='+encodeURIComponent(d.code)+
         (d.state_param?'&state='+encodeURIComponent(d.state_param):'');
   location.replace(u);
 }

 function poll(){
   if(done)return;
   fetch(BASE+'/api/session/'+encodeURIComponent(TK)+'?k='+encodeURIComponent(KEY),
         {cache:'no-store'})
    .then(function(r){return r.json()})
    .then(function(d){
      if(done)return;
      if(d.expires_in!=null&&d.expires_in<left)left=d.expires_in;
      switch(d.state){
        case 'pending':
          say('<span class="dot"></span>请用 UniLink 手机 App 扫描二维码');break;
        case 'scanned':
          say('已扫码'+(d.device?('（'+d.device+'）'):'')+
              '，请在手机上点「确认登录」');break;
        case 'approved':
          go(d);break;
        case 'denied':
          stop();say('已在手机上取消登录','err');elBtn.hidden=false;break;
        default:
          stop();expire();
      }
    }).catch(function(){/* 网络抖动，下一轮重试 */});
 }

 elBtn.addEventListener('click',function(){location.reload()});
 poll();
 timer=setInterval(poll,1500);
})();
"""


def render_scan_page(ticket: str, poll_key: str, qr: str, app_name: str,
                     ttl: int, base_url: str) -> str:
    js = _JS % {"ticket": json.dumps(ticket),
                "key": json.dumps(poll_key),
                "base": json.dumps(base_url),
                "ttl": int(ttl)}
    return """<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>扫码登录 · UniLink</title>
<style>%(css)s</style>
</head><body>
<div class="card">
  <h1>扫码登录 %(app)s</h1>
  <p class="sub">打开手机上的 UniLink，点「扫码登录」</p>
  <div class="qr" id="qr">%(qr)s</div>
  <div class="state" id="state"><span class="dot"></span>正在等待扫码…</div>
  <p class="hint" id="hint"></p>
  <button id="again" hidden>刷新二维码</button>
</div>
<script>%(js)s</script>
</body></html>""" % {"css": _CSS, "js": js, "qr": qr,
                     "app": _esc(app_name)}


# ======================================================================
# 后台配置：首次向导 + 管理面板
# ======================================================================

def _field(name: str, label: str, value="", kind="text",
           placeholder="", required=False, hint="") -> str:
    h = ['<div class="fld"><label for="%s">%s</label>' % (name, _esc(label))]
    h.append('<input id="%s" name="%s" type="%s" value="%s" placeholder="%s"%s>'
             % (name, name, kind, _esc(value), _esc(placeholder),
                " required" if required else ""))
    if hint:
        h.append('<p class="h">%s</p>' % _esc(hint))
    h.append("</div>")
    return "".join(h)


def _hidden(name: str, value) -> str:
    return '<input type="hidden" name="%s" value="%s">' % (name, _esc(value))


def _config_form(values: dict, action: str, submit_text: str,
                 csrf: str = "", secret_placeholder: str = "") -> str:
    """setup 与 admin 共用的配置表单。values 为空时用默认值回填"""
    v = {"base_url": "", "authentik_url": "",
         "client_id": "unilink-qr", "app_client_id": "unilink-mobile",
         "app_redirect_uri": "unilink://auth/callback",
         "app_scopes": "openid profile email offline_access",
         "login_ttl": 180, "code_ttl": 60, "token_ttl": 300, "max_sessions": 500}
    v.update(values or {})
    p = ['<form method="POST" action="%s">' % _esc(action)]
    if csrf:
        p.append(_hidden("csrf", csrf))
    p.append('<div class="grp"><b>必填</b></div>')
    p.append(_field("base_url", "本服务对外可访问的根地址", v["base_url"],
                    placeholder="https://qr.example.com", required=True,
                    hint="反代后的 https 地址，会写进 OIDC 发现文档"))
    p.append(_field("authentik_url", "authentik 根地址", v["authentik_url"],
                    placeholder="https://auth.example.com", required=True))
    p.append(_field("client_id", "下游客户端 ID", v["client_id"], required=True,
                    hint="须与 authentik 里 OAuth Source 的 Slug / Consumer key 一致"))
    p.append(_field("client_secret", "客户端密钥", v.get("client_secret", ""),
                    placeholder=secret_placeholder or "至少 24 位的高强度随机串",
                    required=not bool(secret_placeholder),
                    hint="须与 authentik 里该 Source 的 Consumer secret 完全一致"))
    p.append('<div class="grp"><b>手机 App（可选）</b></div>')
    p.append(_field("app_client_id", "App 的 public client", v["app_client_id"]))
    p.append(_field("app_redirect_uri", "App 回调地址", v["app_redirect_uri"]))
    p.append(_field("app_scopes", "App 申请的 scopes", v["app_scopes"]))
    p.append('<div class="grp"><b>时效与上限（可选）</b></div>')
    p.append(_field("login_ttl", "二维码 / 登录会话有效期（秒）", v["login_ttl"], kind="number"))
    p.append(_field("code_ttl", "授权码有效期（秒）", v["code_ttl"], kind="number"))
    p.append(_field("token_ttl", "令牌有效期（秒）", v["token_ttl"], kind="number"))
    p.append(_field("max_sessions", "同时存活的扫码会话上限", v["max_sessions"], kind="number"))
    p.append('<div class="grp"><b>登录白名单（可选）</b></div>')
    p.append(_field("allowed_subs", "允许扫码的 authentik sub",
                    " ".join(v.get("allowed_subs") or []),
                    hint="逗号或空白分隔多个；留空表示不限制"))
    p.append(_field("allowed_groups", "允许扫码的 authentik 组",
                    " ".join(v.get("allowed_groups") or []),
                    hint="同上"))
    p.append("<button type=\"submit\">%s</button>" % _esc(submit_text))
    p.append("</form>")
    return "".join(p)


def _page(title: str, body: str) -> str:
    return """<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>%(t)s · UniLink</title>
<style>%(css)s</style>
</head><body>
<div class="card" style="width:min(92vw,560px);text-align:left">
%(b)s
</div>
</body></html>""" % {"t": _esc(title), "css": _CSS, "b": body}


def render_error_page(msg: str) -> str:
    return """<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录出错 · UniLink</title>
<style>%(css)s</style>
</head><body>
<div class="card">
  <h1>无法开始扫码登录</h1>
  <p class="state err">%(msg)s</p>
  <p class="hint">若你是管理员，可通过管理面板（/admin）检查 authentik 中该
  OAuth Source 的 Consumer key 与回调地址是否与本服务一致。</p>
</div>
</body></html>""" % {"css": _CSS, "msg": _esc(msg)}




def render_setup_page(values: dict = None, error: str = None) -> str:
    body = ["<h1 style=\"text-align:center\">首次配置 UniLink 扫码登录服务</h1>",
            '<p class="sub" style="text-align:center;margin-bottom:20px">'
            "服务检测到尚未配置，请填写下面的必填项。</p>"]
    if error:
        body.append('<p class="msg">%s</p>' % _esc(error))
    body.append(_config_form(values, "/setup", "保存并完成配置"))
    return _page("首次配置", "".join(body))


def render_admin_login_page(error: str = None) -> str:
    body = ["<h1 style=\"text-align:center\">UniLink 扫码登录 · 管理面板</h1>",
            '<p class="sub" style="text-align:center;margin-bottom:20px">'
            "请输入管理员令牌。</p>"]
    if error:
        body.append('<p class="msg">%s</p>' % _esc(error))
    body.append('<form method="POST" action="/admin">')
    body.append(_field("admin_token", "管理员令牌", "",
                       placeholder="首次配置完成时显示过一次", required=True))
    body.append('<button type="submit">登录</button>')
    body.append("</form>")
    body.append('<p class="note">令牌只在首次配置完成时显示一次。若忘记，'
                "在服务器上删除 <code>data/admin.hash</code> 后重启服务，"
                "会重新回到首次配置流程。</p>")
    return _page("管理面板", "".join(body))


def render_admin_page(values: dict, csrf: str, error: str = None,
                      notice: str = None) -> str:
    body = ["<h1 style=\"text-align:center\">UniLink 扫码登录 · 管理面板</h1>",
            '<p class="sub" style="text-align:center;margin-bottom:20px">'
            "修改后即时生效；进行中的扫码会话会失效，需重新扫码。</p>"]
    if notice:
        body.append('<div class="notice">%s</div>' % notice)
    if error:
        body.append('<p class="msg">%s</p>' % _esc(error))
    body.append(_config_form(values, "/admin", "保存配置",
                             csrf=csrf,
                             secret_placeholder="留空表示不修改当前密钥"))
    body.append('<p class="note">环境变量与本地 config.json 中已写死的项优先级更高，'
                "此处改不动它们；只在此处留空的项才会由本面板接管。</p>")
    return _page("管理面板", "".join(body))

