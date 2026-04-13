/**
 * Cloudflare Worker — SwimCloud proxy for Lane4
 *
 * Routes SwimCloud API requests from Render (whose datacenter IPs are hard-blocked
 * by SwimCloud's Cloudflare WAF) through CF's own network, which is not blocked.
 *
 * Deploy:
 *   1. Go to https://dash.cloudflare.com → Workers & Pages → Create Worker
 *   2. Paste this file, click Save & Deploy
 *   3. Copy the worker URL (e.g. https://swimcloud-proxy.YOUR-SUBDOMAIN.workers.dev)
 *   4. In Render → Environment → add:  SWIMCLOUD_PROXY_URL = <that URL>
 *
 * Usage: GET /proxy?url=https%3A%2F%2Fwww.swimcloud.com%2Fapi%2F...
 */

const ALLOWED_HOST = "www.swimcloud.com";

export default {
  async fetch(request) {
    const incoming = new URL(request.url);

    if (incoming.pathname !== "/proxy") {
      return new Response("Not found", { status: 404 });
    }

    const target = incoming.searchParams.get("url");
    if (!target) {
      return new Response("Missing ?url= parameter", { status: 400 });
    }

    let targetUrl;
    try {
      targetUrl = new URL(target);
    } catch {
      return new Response("Invalid URL", { status: 400 });
    }

    if (targetUrl.hostname !== ALLOWED_HOST) {
      return new Response("Forbidden host", { status: 403 });
    }

    const upstream = await fetch(targetUrl.toString(), {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        Accept: "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        Referer: "https://www.swimcloud.com/",
      },
    });

    const body = await upstream.arrayBuffer();

    return new Response(body, {
      status: upstream.status,
      headers: {
        "Content-Type":
          upstream.headers.get("Content-Type") || "application/json",
        "Cache-Control": "no-store",
      },
    });
  },
};
