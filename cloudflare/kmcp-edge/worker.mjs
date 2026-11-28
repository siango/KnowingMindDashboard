import {handleStripeWebhook} from './stripe-webhook.mjs';
// Stateless edge transport only. Original KMCP remains the SSOT and ACL owner.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/health' && request.method === 'GET') {
      return Response.json({ service: 'kmcp-edge', originChecked: false });
    }
    if (url.pathname === '/stripe/webhook') return handleStripeWebhook(request, env);
    if (url.pathname !== '/mcp') return new Response('Not found', { status: 404 });
    if (!env.EDGE_TOKEN || !env.ORIGIN_AUTHORIZATION || !env.ORIGIN_URL) {
      return new Response('Edge not configured', { status: 503 });
    }
    if (request.headers.get('Authorization') !== `Bearer ${env.EDGE_TOKEN}`) {
      return new Response('Unauthorized', { status: 401 });
    }
    if (!['GET', 'POST', 'DELETE'].includes(request.method)) {
      return new Response('Method not allowed', { status: 405 });
    }
    let target;
    try {
      target = new URL(env.ORIGIN_URL);
      if (target.protocol !== 'https:' || target.username || target.password || target.search || target.hash || target.pathname !== '/mcp') throw Error();
    } catch { return new Response('Invalid origin configuration', { status: 503 }); }
    const headers = new Headers(request.headers);
    for (const name of ['host', 'cookie', 'authorization', 'cf-access-client-id', 'cf-access-client-secret', 'cf-access-jwt-assertion']) headers.delete(name);
    headers.set('Authorization', env.ORIGIN_AUTHORIZATION);
    if (env.CF_ACCESS_CLIENT_ID && env.CF_ACCESS_CLIENT_SECRET) {
      headers.set('CF-Access-Client-Id', env.CF_ACCESS_CLIENT_ID);
      headers.set('CF-Access-Client-Secret', env.CF_ACCESS_CLIENT_SECRET);
    }
    target.search = url.search;
    try {
      const response = await fetch(target, { method: request.method, headers, body: request.body, redirect: 'manual' });
      if (response.status >= 300 && response.status < 400) return new Response('Origin redirected; check Access policy', { status: 502 });
      const out = new Headers(response.headers);
      out.delete('set-cookie');
      out.set('Cache-Control', 'no-store');
      return new Response(response.body, { status: response.status, headers: out });
    } catch { return new Response('Origin unavailable', { status: 502 }); }
  }
};
