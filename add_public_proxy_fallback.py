#!/usr/bin/env python3
"""
Add public proxy fallback option to swimcloud_client.py
"""
import os

# Read the current swimcloud_client.py
with open('swimcloud_client.py', 'r') as f:
    content = f.read()

# Find the _get function
if 'def _get(' in content:
    # Add public proxy fallback option
    new_content = content.replace(
        'def _get(url: str, params: dict = None, timeout: int = 12) -> requests.Response:',
        '''def _get(url: str, params: dict = None, timeout: int = 12) -> requests.Response:
    # Check for proxy URL (for Render deployment where SwimCloud blocks datacenter IPs)
    proxy_url = os.environ.get('SWIMCLOUD_PROXY_URL')
    
    # Public CORS proxy fallback (slower but works)
    if not proxy_url and os.environ.get('USE_PUBLIC_PROXY', '').lower() == 'true':
        proxy_url = 'https://corsproxy.io/'
    
    if proxy_url and 'swimcloud.com' in url:'''
    )
    
    with open('swimcloud_client.py', 'w') as f:
        f.write(new_content)
    
    print("✅ Added public proxy fallback option")
    print("")
    print("Now you can also set USE_PUBLIC_PROXY=true in Render")
    print("as an alternative to Cloudflare Worker")
    print("")
    print("Note: corsproxy.io is a free public CORS proxy")
    print("It's slower but can work as a temporary solution")
else:
    print("❌ Could not find _get function")
