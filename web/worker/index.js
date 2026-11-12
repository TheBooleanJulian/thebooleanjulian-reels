// reels-editor.thebooleanjulian.dev: the studio page (static assets, web/dist) plus Google sign-in and the project
// copies kept in your account, so the same projects show up on every device you sign in on.
//
//   GET  /auth/login?next=<#hash>   to Google (identity only: openid email profile; no Google tokens are kept)
//   GET  /auth/callback             back from Google, sets the session cookie
//   GET  /auth/logout               clears it
//   GET  /auth/me                   {configured, user: {email, name} | null}
//   GET  /cloud/projects            [{name, kind, size, updated_at, device}] for the signed-in account
//   GET  /cloud/projects/:name      the project's bundle (JSON text, stored as sent) + X-Updated-At
//   PUT  /cloud/projects/:name?kind=&device=&base=<updated_at seen last>   body: bundle; 409 if someone else
//                                   saved since <base> (send base=force to overwrite)
//   DELETE /cloud/projects/:name
//
// Only the small project files go here (edit, transcript, notes, a list of the media file names and sizes): footage,
// renders and the editing itself stay on each device, in studio.py. Bundles are stored as text and never parsed here,
// so a 40-minute transcript costs no CPU.
//
// Data: schema thebooleanjulian_reels (thebooleanjulian-db) via the HYPERDRIVE binding. Secrets: GOOGLE_CLIENT_ID,
// GOOGLE_CLIENT_SECRET, SESSION_SECRET. Without them, /auth/me says configured:false and the page hides sign-in.
import { withDb } from 'thebooleanjulian-bot-core/db';

const COOKIE = 'tbj_reels';
const STATE_COOKIE = 'tbj_reels_state';
const MAX_AGE = 30 * 24 * 3600;
const MAX_BUNDLE = 4 * 1024 * 1024;
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
const KINDS = new Set(['interview', 'presentation', 'reel']);

export class HttpError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

// Sign-in shows only when it can work end to end: Google client, cookie key and the database binding.
const configured = (env) => Boolean(env.GOOGLE_CLIENT_ID && env.GOOGLE_CLIENT_SECRET && env.SESSION_SECRET && (env.HYPERDRIVE || env.SQL));

// ── cookies (AES-GCM sealed, same scheme as rothko-cal) ──────────────────────────────────────────────────────
const b64url = (bytes) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const unb64url = (s) => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/')), (c) => c.charCodeAt(0));

async function key(env) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(env.SESSION_SECRET));
  return crypto.subtle.importKey('raw', digest, 'AES-GCM', false, ['encrypt', 'decrypt']);
}

async function seal(env, data, maxAge) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const plain = new TextEncoder().encode(JSON.stringify({ d: data, exp: Date.now() + maxAge * 1000 }));
  const sealed = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, await key(env), plain));
  const out = new Uint8Array(12 + sealed.length);
  out.set(iv); out.set(sealed, 12);
  return b64url(out);
}

async function unseal(env, request, name) {
  const raw = (request.headers.get('Cookie') || '').split(/;\s*/).find((c) => c.startsWith(name + '='));
  if (!raw || !env.SESSION_SECRET) return null;
  try {
    const bytes = unb64url(raw.slice(name.length + 1));
    const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: bytes.slice(0, 12) }, await key(env), bytes.slice(12));
    const { d, exp } = JSON.parse(new TextDecoder().decode(plain));
    return exp > Date.now() ? d : null;
  } catch {
    return null; // tampered or rotated secret: signed out
  }
}

const cookie = (name, value, maxAge) => `${name}=${value}; Path=/; Max-Age=${maxAge}; HttpOnly; Secure; SameSite=Lax`;
const clear = (name) => `${name}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`;

// ── responses ────────────────────────────────────────────────────────────────────────────────────────────────
function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...extra } });
}

function redirect(location, ...cookies) {
  const headers = new Headers({ Location: location, 'Cache-Control': 'no-store' });
  for (const c of cookies) headers.append('Set-Cookie', c);
  return new Response(null, { status: 302, headers });
}

const safeNext = (next) => (/^#[A-Za-z0-9/_.%-]{0,200}$/.test(next || '') ? next : '');

// ── Google sign-in (authorization code flow; the ID token comes straight from Google's token endpoint over TLS,
//    which Google documents as trustworthy without checking its signature) ──────────────────────────────────────
async function login(request, env) {
  const url = new URL(request.url);
  const state = b64url(crypto.getRandomValues(new Uint8Array(24)));
  const google = new URL('https://accounts.google.com/o/oauth2/v2/auth');
  google.search = new URLSearchParams({
    response_type: 'code', client_id: env.GOOGLE_CLIENT_ID, redirect_uri: `${url.origin}/auth/callback`,
    scope: 'openid email profile', state, prompt: 'select_account'
  });
  const pending = await seal(env, { state, next: safeNext(url.searchParams.get('next')) }, 600);
  return redirect(google.toString(), cookie(STATE_COOKIE, pending, 600));
}

export function decodeIdToken(idToken) {
  const part = String(idToken || '').split('.')[1];
  if (!part) throw new HttpError(401, 'No ID token from Google');
  return JSON.parse(new TextDecoder().decode(unb64url(part)));
}

async function callback(request, env) {
  const url = new URL(request.url);
  const pending = await unseal(env, request, STATE_COOKIE);
  const back = (q) => redirect(`${url.origin}/${q}${pending?.next || ''}`, clear(STATE_COOKIE));
  if (!pending || url.searchParams.get('error') || !url.searchParams.get('code') || url.searchParams.get('state') !== pending.state) {
    return back('?signin=failed');
  }
  const res = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      client_id: env.GOOGLE_CLIENT_ID, client_secret: env.GOOGLE_CLIENT_SECRET, grant_type: 'authorization_code',
      code: url.searchParams.get('code'), redirect_uri: `${url.origin}/auth/callback`
    })
  });
  const token = await res.json().catch(() => ({}));
  let claims;
  try { claims = decodeIdToken(token.id_token); } catch { return back('?signin=failed'); }
  const issOk = claims.iss === 'https://accounts.google.com' || claims.iss === 'accounts.google.com';
  if (!res.ok || claims.aud !== env.GOOGLE_CLIENT_ID || !issOk || !claims.sub || claims.email_verified === false) {
    return back('?signin=failed');
  }
  const user = { sub: String(claims.sub), email: claims.email || '', name: claims.name || '' };
  return redirect(`${url.origin}/${pending.next || ''}`, clear(STATE_COOKIE), cookie(COOKIE, await seal(env, user, MAX_AGE), MAX_AGE));
}

// ── projects ─────────────────────────────────────────────────────────────────────────────────────────────────
async function projects(request, env, ctx, user, name) {
  if (request.method !== 'GET') {
    // Writes only from this site's own page: a cross-site form can't send JSON with this Origin.
    if (request.headers.get('Origin') !== new URL(request.url).origin) throw new HttpError(403, 'Cross-site request refused');
  }
  if (name === null) {
    if (request.method !== 'GET') throw new HttpError(405, 'Method not allowed');
    const rows = await withDb(env, ctx, (sql) => sql`
      select name, kind, size, updated_at, device from thebooleanjulian_reels.projects where user_sub = ${user.sub} order by updated_at desc`);
    return json({ projects: rows.map((r) => ({ ...r, updated_at: new Date(r.updated_at).toISOString() })) });
  }
  if (!NAME_RE.test(name)) throw new HttpError(400, 'Bad project name');

  if (request.method === 'GET') {
    const rows = await withDb(env, ctx, (sql) => sql`
      select bundle, updated_at from thebooleanjulian_reels.projects where user_sub = ${user.sub} and name = ${name}`);
    if (!rows.length) throw new HttpError(404, 'Not in your account');
    return new Response(rows[0].bundle, { headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store',
      'X-Updated-At': new Date(rows[0].updated_at).toISOString() } });
  }

  if (request.method === 'DELETE') {
    await withDb(env, ctx, (sql) => sql`delete from thebooleanjulian_reels.projects where user_sub = ${user.sub} and name = ${name}`);
    return json({ ok: true });
  }

  if (request.method === 'PUT') {
    const q = new URL(request.url).searchParams;
    const kind = q.get('kind') || '';
    if (!KINDS.has(kind)) throw new HttpError(400, 'Bad kind');
    const device = (q.get('device') || '').slice(0, 60);
    const base = q.get('base') || '';
    const bundle = await request.text();
    const size = new TextEncoder().encode(bundle).length;
    if (size > MAX_BUNDLE) throw new HttpError(413, 'Project too large to keep in your account (over 4 MB)');
    if (!bundle.startsWith('{')) throw new HttpError(400, 'Bundle must be JSON');
    return withDb(env, ctx, async (sql) => {
      const [cur] = await sql`select updated_at from thebooleanjulian_reels.projects where user_sub = ${user.sub} and name = ${name}`;
      const curAt = cur ? new Date(cur.updated_at).toISOString() : '';
      if (cur && base !== 'force' && base !== curAt) {
        throw Object.assign(new HttpError(409, 'Changed on another device since this one last synced'), { updated_at: curAt });
      }
      if (!cur && base && base !== 'force') throw Object.assign(new HttpError(409, 'Removed from your account on another device'), { updated_at: '' });
      const [row] = await sql`
        insert into thebooleanjulian_reels.projects (user_sub, name, kind, bundle, size, device, updated_at)
        values (${user.sub}, ${name}, ${kind}, ${bundle}, ${size}, ${device}, now())
        on conflict (user_sub, name) do update
          set kind = excluded.kind, bundle = excluded.bundle, size = excluded.size, device = excluded.device, updated_at = now()
        returning updated_at`;
      return json({ ok: true, updated_at: new Date(row.updated_at).toISOString() });
    });
  }
  throw new HttpError(405, 'Method not allowed');
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    try {
      if (path === '/auth/me') {
        if (!configured(env)) return json({ configured: false, user: null });
        const u = await unseal(env, request, COOKIE);
        return json({ configured: true, user: u ? { email: u.email, name: u.name } : null });
      }
      if (path.startsWith('/auth/') || path.startsWith('/cloud/')) {
        if (!configured(env)) throw new HttpError(503, 'Sign-in is not set up on this site yet');
      }
      if (path === '/auth/login') return await login(request, env);
      if (path === '/auth/callback') return await callback(request, env);
      if (path === '/auth/logout') return redirect(`${url.origin}/`, clear(COOKIE));
      if (path === '/cloud/projects' || path.startsWith('/cloud/projects/')) {
        const user = await unseal(env, request, COOKIE);
        if (!user) throw new HttpError(401, 'Sign in to sync projects');
        const name = path === '/cloud/projects' ? null : decodeURIComponent(path.slice('/cloud/projects/'.length));
        return await projects(request, env, ctx, user, name);
      }
      if (path.startsWith('/auth/') || path.startsWith('/cloud/')) throw new HttpError(404, 'Not found');
      return env.ASSETS.fetch(request);
    } catch (err) {
      if (err instanceof HttpError) return json({ error: err.message, ...(err.updated_at !== undefined ? { updated_at: err.updated_at } : {}) }, err.status);
      console.error('request failed:', String(err.message).replace(/postgres(ql)?:\/\/\S+/g, '<conn>'));
      return json({ error: 'Server error' }, 500);
    }
  }
};
