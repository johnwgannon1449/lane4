#!/usr/bin/env python3
"""
Fix the proxy handler in swimcloud_client.py to work correctly.
"""
import re

with open('swimcloud_client.py', 'r') as f:
    content = f.read()

# Replace the _get function with a better implementation
new_get_function = '''def _get(url: str, params: dict = None, timeout: int = 12) -> requests.Response:
    """
    Make a GET request to SwimCloud, with proxy support.
    
    Supports:
    1. Direct request (local dev)
    2. Custom proxy (SWIMCLOUD_PROXY_URL)
    3. Public proxy fallback (USE_PUBLIC_PROXY)
    """
    # Check for custom proxy URL
    proxy_url = os.environ.get('SWIMCLOUD_PROXY_URL')
    
    # Public proxy fallback
    if not proxy_url and os.environ.get('USE_PUBLIC_PROXY', '').lower() == 'true':
        # Note: Most public proxies don't work server-side
        # This is mainly for testing
        proxy_url = 'https://api.allorigins.win/raw?url='
    
    if proxy_url and 'swimcloud.com' in url:
        # Build the full target URL with params
        target_url = url
        if params:
            # Convert params to query string
            from urllib.parse import urlencode
            query_string = urlencode(params)
            target_url = f"{url}?{query_string}"
        
        # Different proxy services have different formats
        if 'allorigins.win' in proxy_url:
            # allorigins.win format: https://api.allorigins.win/raw?url=ENCODED_URL
            import urllib.parse
            encoded_url = urllib.parse.quote(target_url, safe='')
            final_url = f"{proxy_url}{encoded_url}"
            proxy_params = None
        else:
            # Assume our custom proxy format (Cloudflare Worker)
            final_url = proxy_url
            proxy_params = {'url': target_url}
        
        s = _get_session()
        r = s.get(final_url, params=proxy_params, timeout=timeout)
        r.raise_for_status()
        return r
    
    # Direct request (for local development)
    s = _get_session()
    r = s.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r'''

# Find and replace the _get function
pattern = r'def _get\(url: str, params: dict = None, timeout: int = 12\) -> requests\.Response:.*?return r'
fixed_content = re.sub(pattern, new_get_function, content, flags=re.DOTALL)

with open('swimcloud_client.py', 'w') as f:
    f.write(fixed_content)

print("Updated _get function with better proxy handling")
