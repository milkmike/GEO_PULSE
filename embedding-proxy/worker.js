/**
 * Cloudflare Worker: Embedding API Proxy
 * 
 * Proxies embedding requests to Jina AI / OpenAI from a non-restricted region.
 * Deploy to Cloudflare Workers (free tier: 100k requests/day).
 * 
 * Environment variables (set in CF dashboard):
 * - PROXY_SECRET: shared secret for authentication
 */

export default {
  async fetch(request, env) {
    // CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "POST",
          "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Proxy-Secret",
        },
      });
    }

    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405 });
    }

    // Verify proxy secret
    const proxySecret = request.headers.get("X-Proxy-Secret");
    if (env.PROXY_SECRET && proxySecret !== env.PROXY_SECRET) {
      return new Response("Unauthorized", { status: 401 });
    }

    try {
      const body = await request.json();
      const targetUrl = body._target_url || "https://api.jina.ai/v1/embeddings";
      const targetAuth = body._target_auth || "";

      // Remove proxy-specific fields
      delete body._target_url;
      delete body._target_auth;

      const response = await fetch(targetUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": targetAuth,
        },
        body: JSON.stringify(body),
      });

      const data = await response.text();

      return new Response(data, {
        status: response.status,
        headers: {
          "Content-Type": "application/json",
          "Access-Control-Allow-Origin": "*",
        },
      });
    } catch (e) {
      return new Response(JSON.stringify({ error: e.message }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      });
    }
  },
};
