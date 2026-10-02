// 无浏览器依赖：执行页面里的真实 JS，模拟慢请求、计时器和页面离开。
const assert = require('node:assert/strict');
const vm = require('node:vm');
const script = require('node:fs').readFileSync(0, 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function page() {
  let id = 0, now = 0;
  const timers = new Map(), requests = [], events = {}, nodes = {};
  const location = {replaced: null, reloaded: false,
    replace(url) {this.replaced = url;}, reload() {this.reloaded = true;}};
  const context = {
    AbortController, location, Date: {now: () => now},
    document: {getElementById(name) {
      return nodes[name] ||= {classList: {add() {}}, hidden: true, addEventListener() {}};
    }},
    window: {addEventListener(name, fn) {events[name] = fn;}},
    setTimeout(fn, ms) {const n = ++id; timers.set(n, {fn, ms}); return n;},
    clearTimeout(n) {timers.delete(n);},
    setInterval(fn, ms) {const n = ++id; timers.set(n, {fn, ms, repeat: true}); return n;},
    clearInterval(n) {timers.delete(n);},
    fetch(url, opts) {
      return new Promise((resolve, reject) => {
        const req = {url, signal: opts.signal, resolve, reject};
        opts.signal.addEventListener('abort', () => reject(new Error('aborted')));
        requests.push(req);
      });
    }
  };
  vm.runInNewContext(script, context);
  return {timers, requests, events, location, nodes,
    clock(value) {now = value;},
    fire(ms) {
      const pair = [...timers].find(([, t]) => t.ms === ms);
      assert.ok(pair, 'expected timer ' + ms);
      if (!pair[1].repeat) timers.delete(pair[0]);
      pair[1].fn();
    },
    respond(data) {requests.at(-1).resolve({ok: true, json: async () => data});}
  };
}

(async () => {
  // 慢请求期间只有倒计时/超时计时器，没有第二条轮询。
  let p = page();
  assert.equal(p.requests.length, 1);
  for (let i = 0; i < 5; i++) p.fire(1000);
  assert.equal(p.requests.length, 1);
  assert.equal([...p.timers.values()].filter(t => t.ms === 1500).length, 0);
  p.respond({state: 'pending', expires_in: 170});
  await flush();
  p.fire(1500);
  assert.equal(p.requests.length, 2);
  p.events.pagehide();
  await flush();
  assert.equal(p.requests[1].signal.aborted, true);
  assert.equal(p.timers.size, 0);
  p.events.pageshow({persisted: true});
  assert.equal(p.location.reloaded, true);

  // 超时先取消旧请求，完成 finally 后才排下一次。
  p = page();
  p.fire(8000);
  await flush();
  assert.equal(p.requests[0].signal.aborted, true);
  p.fire(1500);
  assert.equal(p.requests.length, 2);
  p.events.pagehide();
  await flush();
  assert.equal(p.timers.size, 0);

  // 浏览器休眠后按墙钟过期，迟到的成功响应也不能跳转。
  p = page();
  p.clock(181000);
  p.fire(1000);
  p.respond({state: 'approved', code: 'late', redirect_uri: 'https://auth.test/cb'});
  await flush();
  assert.equal(p.requests[0].signal.aborted, true);
  assert.equal(p.location.replaced, null);
  assert.equal(p.timers.size, 0);

  // 正常成功/拒绝均停止所有计时器。
  for (const state of ['approved', 'denied']) {
    p = page();
    p.respond({state, code: 'code', redirect_uri: 'https://auth.test/cb', state_param: 'csrf'});
    await flush();
    assert.equal(p.timers.size, 0);
    if (state === 'approved') assert.equal(p.location.replaced, 'https://auth.test/cb?code=code&state=csrf');
    else assert.equal(p.nodes.again.hidden, false);
  }
  console.log('Browser polling lifecycle checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
