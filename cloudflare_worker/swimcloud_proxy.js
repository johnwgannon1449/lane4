// Cloudflare Worker proxy for SwimCloud API
// Deploy this as a Cloudflare Worker to bypass Render IP block
// Set SWIMCLOUD_PROXY_URL in Render to this worker's URL

addEventListener('fetch', event => {
  event.respondWith(handleRequest(event.request))
})

async function handleRequest(request) {
  // Only allow GET requests
  if (request.method !== 'GET') {
    return new Response('Method not allowed', { status: 405 })
  }

  const url = new URL(request.url)
  const targetUrl = url.searchParams.get('url')
  
  // Validate URL - only allow swimcloud.com
  if (!targetUrl || !targetUrl.includes('swimcloud.com')) {
    return new Response('Invalid URL', { status: 400 })
  }

  try {
    // Parse params if provided
    let params = {}
    const paramsStr = url.searchParams.get('params')
    if (paramsStr) {
      try {
        params = JSON.parse(paramsStr)
      } catch (e) {
        // Ignore parse errors, use empty params
      }
    }

    // Build target URL with params
    const target = new URL(targetUrl)
    Object.keys(params).forEach(key => {
      target.searchParams.append(key, params[key])
    })

    // Forward request to SwimCloud
    const response = await fetch(target.toString(), {
      headers: {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': 'https://www.swimcloud.com/',
      },
      cf: {
        // Use Cloudflare's cache
        cacheTtl: 300,
        cacheEverything: true,
      }
    })

    // Return response with CORS headers
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    }

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: {
        ...Object.fromEntries(response.headers),
        ...corsHeaders,
      }
    })
  } catch (error) {
    return new Response(`Error: ${error.message}`, { 
      status: 500,
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Content-Type': 'text/plain'
      }
    })
  }
}

// Handle OPTIONS for CORS preflight
addEventListener('fetch', event => {
  if (event.request.method === 'OPTIONS') {
    event.respondWith(handleOptions(event.request))
  }
})

function handleOptions(request) {
  return new Response(null, {
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
      'Access-Control-Max-Age': '86400',
    }
  })
}
