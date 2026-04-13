#!/usr/bin/env python3
"""
Comprehensive test of SwimCloud API fix.
"""
import os
import sys
import re

def check_cors_config():
    print("1. Checking CORS configuration...")
    with open('main.py', 'r') as f:
        content = f.read()
    
    has_import = 'from flask_cors import CORS' in content
    has_init = 'CORS(app)' in content
    
    if has_import and has_init:
        print("   ✅ CORS properly configured")
        return True
    else:
        print(f"   ❌ CORS not configured: import={has_import}, init={has_init}")
        return False

def check_proxy_support():
    print("\n2. Checking proxy support in swimcloud_client.py...")
    with open('swimcloud_client.py', 'r') as f:
        content = f.read()
    
    checks = {
        'SWIMCLOUD_PROXY_URL check': 'SWIMCLOUD_PROXY_URL' in content,
        'os import': 'import os' in content,
        'json import': 'import json' in content,
        'proxy logic': 'proxy_url and' in content and 'swimcloud.com' in content
    }
    
    all_pass = True
    for check_name, passed in checks.items():
        status = "✅" if passed else "❌"
        print(f"   {status} {check_name}")
        if not passed:
            all_pass = False
    
    return all_pass

def check_client_side_fixes():
    print("\n3. Checking client-side fixes in static/index.html...")
    with open('static/index.html', 'r') as f:
        content = f.read()
    
    # Check for direct swimcloud.com fetch calls
    direct_fetches = re.findall(r"fetch\s*\(['\"]https://www\.swimcloud\.com", content)
    
    if len(direct_fetches) == 0:
        print("   ✅ No direct swimcloud.com fetch calls")
        direct_ok = True
    else:
        print(f"   ❌ Found {len(direct_fetches)} direct swimcloud.com fetch calls")
        direct_ok = False
    
    # Check for server endpoint usage
    endpoints = {
        '/api/public/swimcloud/search': 0,
        '/api/public/swimcloud/propose': 0,
        '/api/public/swimcloud/process-times': 0,
        '/api/swimcloud/search': 0,
        '/api/swimcloud/propose': 0
    }
    
    for endpoint in endpoints:
        count = content.count(endpoint)
        endpoints[endpoint] = count
    
    print("\n   Server endpoint usage:")
    endpoint_ok = True
    for endpoint, count in endpoints.items():
        if count > 0:
            print(f"   ✅ {endpoint}: {count} usage(s)")
        else:
            # process-times might not be used anymore if we switched to propose
            if endpoint != '/api/public/swimcloud/process-times':
                print(f"   ⚠️  {endpoint}: not used")
                if endpoint in ['/api/public/swimcloud/search', '/api/public/swimcloud/propose']:
                    endpoint_ok = False
    
    # Check specific patterns
    print("\n   Specific checks:")
    
    # Check if search uses server endpoint
    if "fetch('/api/public/swimcloud/search?q=' + encodeURIComponent(name))" in content:
        print("   ✅ Search uses server endpoint")
    else:
        print("   ❌ Search might not be using server endpoint")
        endpoint_ok = False
    
    # Check if propose is used in obSelectSwimmer
    if "fetch(`/api/public/swimcloud/propose?swimmer_id=${encodeURIComponent(swimmerId)}" in content:
        print("   ✅ obSelectSwimmer uses propose endpoint")
    else:
        print("   ❌ obSelectSwimmer might not be using propose endpoint")
        endpoint_ok = False
    
    return direct_ok and endpoint_ok

def check_cloudflare_worker():
    print("\n4. Checking Cloudflare Worker files...")
    
    worker_file = 'cloudflare_worker/swimcloud_proxy.js'
    if os.path.exists(worker_file):
        print(f"   ✅ Cloudflare Worker script exists: {worker_file}")
        
        with open(worker_file, 'r') as f:
            content = f.read()
        
        # Check key components
        checks = {
            'CORS headers': 'Access-Control-Allow-Origin' in content,
            'swimcloud.com check': 'swimcloud.com' in content,
            'OPTIONS handler': 'handleOptions' in content
        }
        
        for check_name, passed in checks.items():
            status = "✅" if passed else "❌"
            print(f"   {status} {check_name}")
        
        return True
    else:
        print("   ⚠️  Cloudflare Worker script not found")
        return False

def main():
    print("Comprehensive SwimCloud API Fix Test")
    print("=" * 60)
    
    tests = [
        ("CORS Configuration", check_cors_config),
        ("Proxy Support", check_proxy_support),
        ("Client-Side Fixes", check_client_side_fixes),
        ("Cloudflare Worker", check_cloudflare_worker)
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"\n{test_name}:")
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"   ❌ Error: {e}")
            results.append((test_name, False))
    
    print("\n" + "=" * 60)
    print("SUMMARY:")
    
    all_pass = True
    for test_name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {test_name:25} {status}")
        if not passed:
            all_pass = False
    
    print("\n" + "=" * 60)
    print("NEXT STEPS:")
    
    if all_pass:
        print("✅ All tests passed! The fix is ready for deployment.")
        print("\nTo deploy:")
        print("1. Push changes to GitHub")
        print("2. Render will auto-deploy")
        print("3. If SwimCloud still blocks requests, deploy Cloudflare Worker:")
        print("   - Deploy cloudflare_worker/swimcloud_proxy.js as a Worker")
        print("   - Set SWIMCLOUD_PROXY_URL in Render environment")
    else:
        print("❌ Some tests failed. Review the issues above.")
        print("\nCommon issues:")
        print("- Direct swimcloud.com calls in static/index.html")
        print("- Missing CORS configuration in main.py")
        print("- Missing proxy support in swimcloud_client.py")
    
    return all_pass

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
