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
  <p class="hint">若你是管理员，请检查 authentik 中该 OAuth Source 的
  Consumer key 与回调地址是否与 auth-server 的 config.json 一致。</p>
</div>
</body></html>""" % {"css": _CSS, "msg": _esc(msg)}
