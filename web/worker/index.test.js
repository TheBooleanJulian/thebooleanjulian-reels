// node --test   (from web/worker)
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import worker from './index.js';

const ORIGIN = 'https://reels-editor.thebooleanjulian.dev';
const b64url = (s) => Buffer.from(s).toString('base64url');

// In-memory stand-in for thebooleanjulian_reels.projects, answering the Worker's few queries by their text.
let rows;
function fakeSql(strings, ...v) {
  const q = strings.join('?').replace(/\s+/g, ' ').trim();
  const key = (sub, name) => `${sub}|${name}`;
  if (q.startsWith('select name, kind, size, updated_at, device')) return Promise.resolve([...rows.values()].filter((r) => r.user_sub === v[0]));
  if (q.startsWith('select bundle, updated_at')) return Promise.resolve(rows.has(key(v[0], v[1])) ? [rows.get(key(v[0], v[1]))] : []);
  if (q.startsWith('select updated_at')) return Promise.resolve(rows.has(key(v[0], v[1])) ? [rows.get(key(v[0], v[1]))] : []);
  if (q.startsWith('delete')) { rows.delete(key(v[0], v[1])); return Promise.resolve([]); }
  if (q.startsWith('insert')) {
    const [user_sub, name, kind, bundle, size, device] = v;
    const prev = rows.get(key(user_sub, name));
    const updated_at = new Date(Math.max(Date.now(), prev ? prev.updated_at.getTime() + 1 : 0));
    rows.set(key(user_sub, name), { user_sub, name, kind, bundle, size, device, updated_at });
    return Promise.resolve([{ updated_at }]);
  }
  throw new Error('unexpected query: ' + q);
}

const env = { GOOGLE_CLIENT_ID: 'cid.apps.googleusercontent.com', GOOGLE_CLIENT_SECRET: 'csecret', SESSION_SECRET: 'test-secret', SQL: fakeSql,
  ASSETS: { fetch: () => new Response('<!doctype html>page', { headers: { 'Content-Type': 'text/html' } }) } };
const ctx = { waitUntil() {} };
beforeEach(() => { rows = new Map(); });

const call = (path, init = {}, e = env) => worker.fetch(new Request(ORIGIN + path, init), e, ctx);
const cookieOf = (res, name) => res.headers.getSetCookie().find((c) => c.startsWith(name + '=')).split(';')[0];

async function signIn(sub = '1001', email = 'julian@example.com') {
  const start = await call('/auth/login?next=%23interview/talk');
  const google = new URL(start.headers.get('Location'));
  const state = google.searchParams.get('state');
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    assert.equal(String(url), 'https://oauth2.googleapis.com/token');
    assert.equal(new URLSearchParams(init.body).get('redirect_uri'), ORIGIN + '/auth/callback');
    const claims = { iss: 'https://accounts.google.com', aud: env.GOOGLE_CLIENT_ID, sub, email, email_verified: true, name: 'Julian' };
    return new Response(JSON.stringify({ id_token: `h.${b64url(JSON.stringify(claims))}.s`, access_token: 'x' }));
  };
  try {
    const back = await call(`/auth/callback?code=abc&state=${state}`, { headers: { Cookie: cookieOf(start, 'tbj_reels_state') } });
    return { back, cookie: cookieOf(back, 'tbj_reels') };
  } finally {
    globalThis.fetch = realFetch;
  }
}

test('sign-in round trip: identity scopes only, returns to the page, session cookie', async () => {
  const start = await call('/auth/login');
  const google = new URL(start.headers.get('Location'));
  assert.equal(google.hostname, 'accounts.google.com');
  assert.equal(google.searchParams.get('scope'), 'openid email profile');
  const { back, cookie } = await signIn();
  assert.equal(back.headers.get('Location'), ORIGIN + '/#interview/talk');
  assert.match(back.headers.getSetCookie().join('\n'), /tbj_reels=.*HttpOnly; Secure; SameSite=Lax/);
  const me = await (await call('/auth/me', { headers: { Cookie: cookie } })).json();
  assert.deepEqual(me, { configured: true, user: { email: 'julian@example.com', name: 'Julian' } });
});

test('callback refuses a wrong state or a token for another app', async () => {
  const start = await call('/auth/login');
  const bad = await call('/auth/callback?code=abc&state=nope', { headers: { Cookie: cookieOf(start, 'tbj_reels_state') } });
  assert.match(bad.headers.get('Location'), /signin=failed/);
  assert.equal(bad.headers.getSetCookie().some((c) => c.startsWith('tbj_reels=') && !c.startsWith('tbj_reels=;')), false);
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ id_token: `h.${b64url(JSON.stringify({ iss: 'https://accounts.google.com', aud: 'other', sub: '1' }))}.s` }));
  try {
    const state = new URL(start.headers.get('Location')).searchParams.get('state');
    const res = await call(`/auth/callback?code=abc&state=${state}`, { headers: { Cookie: cookieOf(start, 'tbj_reels_state') } });
    assert.match(res.headers.get('Location'), /signin=failed/);
  } finally { globalThis.fetch = realFetch; }
});

test('not configured: page still served, sign-in hidden, cloud routes 503', async () => {
  const bare = { ASSETS: env.ASSETS };
  assert.deepEqual(await (await call('/auth/me', {}, bare)).json(), { configured: false, user: null });
  assert.equal((await call('/cloud/projects', {}, bare)).status, 503);
  assert.equal(await (await call('/', {}, bare)).text(), '<!doctype html>page');
});

test('projects need a session; a forged cookie is signed out', async () => {
  assert.equal((await call('/cloud/projects')).status, 401);
  assert.equal((await call('/cloud/projects', { headers: { Cookie: 'tbj_reels=AAAA' } })).status, 401);
});

test('save, list, fetch, conflict, force, delete; accounts are separate', async () => {
  const { cookie } = await signIn('1001');
  const put = (name, body, base = '', c = cookie, origin = ORIGIN) => call(`/cloud/projects/${name}?kind=interview&device=PC&base=${encodeURIComponent(base)}`,
    { method: 'PUT', body, headers: { Cookie: c, Origin: origin, 'Content-Type': 'application/json' } });

  const first = await (await put('talk', '{"v":1,"preset":"a"}')).json();
  assert.ok(first.updated_at);
  const list = await (await call('/cloud/projects', { headers: { Cookie: cookie } })).json();
  assert.deepEqual(list.projects.map((p) => [p.name, p.kind, p.device]), [['talk', 'interview', 'PC']]);
  const got = await call('/cloud/projects/talk', { headers: { Cookie: cookie } });
  assert.equal(await got.text(), '{"v":1,"preset":"a"}');
  assert.equal(got.headers.get('X-Updated-At'), first.updated_at);

  const second = await (await put('talk', '{"v":1,"preset":"b"}', first.updated_at)).json();
  const stale = await put('talk', '{"v":1,"preset":"c"}', first.updated_at);
  assert.equal(stale.status, 409);
  assert.equal((await stale.json()).updated_at, second.updated_at);
  assert.equal((await put('talk', '{"v":1,"preset":"c"}', 'force')).status, 200);

  const other = (await signIn('2002', 'client@example.com')).cookie;
  assert.equal((await call('/cloud/projects/talk', { headers: { Cookie: other } })).status, 404);
  assert.deepEqual((await (await call('/cloud/projects', { headers: { Cookie: other } })).json()).projects, []);

  assert.equal((await put('talk', '{}', 'force', cookie, 'https://evil.example')).status, 403);
  const del = await call('/cloud/projects/talk', { method: 'DELETE', headers: { Cookie: cookie, Origin: ORIGIN } });
  assert.equal(del.status, 200);
  assert.equal((await call('/cloud/projects/talk', { headers: { Cookie: cookie } })).status, 404);
});

test('rejects bad names, kinds, non-JSON and oversize bundles', async () => {
  const { cookie } = await signIn();
  const h = { Cookie: cookie, Origin: ORIGIN };
  assert.equal((await call('/cloud/projects/..%2Fx?kind=interview', { method: 'PUT', body: '{}', headers: h })).status, 400);
  assert.equal((await call('/cloud/projects/ok?kind=movie', { method: 'PUT', body: '{}', headers: h })).status, 400);
  assert.equal((await call('/cloud/projects/ok?kind=reel', { method: 'PUT', body: 'hello', headers: h })).status, 400);
  assert.equal((await call('/cloud/projects/ok?kind=reel', { method: 'PUT', body: '{' + 'x'.repeat(4 * 1024 * 1024) + '}', headers: h })).status, 413);
});
