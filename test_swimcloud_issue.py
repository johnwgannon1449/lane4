#!/usr/bin/env python3
"""
Test to verify SwimCloud API issue and solution.
"""
import os
import sys
import requests
import json

def test_direct_swimcloud():
    """Test direct request to SwimCloud."""
    print("1. Testing direct request to SwimCloud...")
    url = "https://www.swimcloud.com/api/search/"
    params = {"q": "Joseph Gannon", "type": "swimmer"}
    
    try:
        response = requests.get(url, params=params, timeout=10)
        print(f"   Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"   Results found: {len(data) if isinstance(data, list) else 'unknown'}")
            return True
        else:
            print(f"   Response: {response.text[:100]}...")
            return False
    except Exception as e:
        print(f"   Error: {e}")
        return False

def test_server_endpoint():
    """Test the server endpoint."""
    print("\n2. Testing server endpoint...")
    url = "http://localhost:5000/api/public/swimcloud/search"
    params = {"q": "Joseph Gannon"}
    
    try:
        response = requests.get(url, params=params, timeout=10)
        print(f"   Status: {response.status_code}")
        data = response.json()
        print(f"   Response keys: {list(data.keys())}")
        
        if 'error' in data:
            print(f"   Error: {data.get('error')}")
            print(f"   Detail: {data.get('detail', 'No detail')}")
            return False
        elif 'results' in data:
            print(f"   Results: {len(data['results'])}")
            return True
        else:
            print(f"   Unexpected response: {data}")
            return False
    except Exception as e:
        print(f"   Error: {e}")
        return False

def test_proxy_implementation():
    """Test if proxy implementation is correct."""
    print("\n3. Checking proxy implementation...")
    
    # Check swimcloud_client.py
    with open('swimcloud_client.py', 'r') as f:
        content = f.read()
    
    checks = [
        ('SWIMCLOUD_PROXY_URL check', 'SWIMCLOUD_PROXY_URL' in content),
        ('os.environ.get', 'os.environ.get' in content),
        ('proxy_url logic', 'proxy_url and' in content),
        ('json.dumps for params', 'json.dumps(params)' in content),
    ]
    
    all_pass = True
    for check_name, passed in checks:
        status = "✅" if passed else "❌"
        print(f"   {status} {check_name}")
        if not passed:
            all_pass = False
    
    return all_pass

def main():
    print("SwimCloud API Issue Diagnosis")
    print("=" * 60)
    
    # Check if server is running
    print("\nNote: Make sure the Flask app is running (python main.py)")
    print("=" * 60)
    
    # Run tests
    direct_ok = test_direct_swimcloud()
    server_ok = test_server_endpoint()
    proxy_ok = test_proxy_implementation()
    
    print("\n" + "=" * 60)
    print("DIAGNOSIS:")
    
    if not direct_ok:
        print("❌ Direct requests to SwimCloud are blocked (403)")
        print("   This confirms SwimCloud is blocking Render's IPs")
    
    if not server_ok:
        print("❌ Server endpoint is failing")
        print("   The server can't reach SwimCloud due to IP blocking")
    
    if proxy_ok:
        print("✅ Proxy implementation is ready")
        print("   Solution: Deploy Cloudflare Worker and set SWIMCLOUD_PROXY_URL")
    else:
        print("❌ Proxy implementation is incomplete")
        print("   Need to fix swimcloud_client.py")
    
    print("\n" + "=" * 60)
    print("SOLUTION:")
    print("1. Deploy Cloudflare Worker:")
    print("   - Go to Cloudflare Dashboard → Workers & Pages")
    print("   - Create new Worker")
    print("   - Paste code from cloudflare_worker/swimcloud_proxy.js")
    print("   - Deploy and note the Worker URL")
    print("\n2. Configure Render:")
    print("   - In Render dashboard, go to Environment")
    print("   - Add variable: SWIMCLOUD_PROXY_URL = https://your-worker.workers.dev")
    print("   - Redeploy app")
    print("\n3. Test:")
    print("   - Search for 'Joseph Gannon' in onboarding")
    print("   - Should now find swimmer profiles")
    
    return direct_ok and server_ok and proxy_ok

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
