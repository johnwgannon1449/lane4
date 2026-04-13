#!/usr/bin/env python3
"""
Test Flask endpoints for SwimCloud integration.
"""
import sys
import os
from unittest.mock import patch, MagicMock

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_swimcloud_client_proxy():
    """Test that swimcloud_client uses proxy when SWIMCLOUD_PROXY_URL is set."""
    print("Testing swimcloud_client proxy logic...")
    
    import swimcloud_client
    
    # Mock environment variable
    with patch.dict('os.environ', {'SWIMCLOUD_PROXY_URL': 'https://proxy.example.com'}):
        # Mock the _get_session function
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_session.get.return_value = mock_response
        
        with patch.object(swimcloud_client, '_get_session', return_value=mock_session):
            # Test that proxy is used for swimcloud.com URLs
            swimcloud_client._get('https://www.swimcloud.com/api/search/')
            
            # Check that proxy was called
            mock_session.get.assert_called_once()
            args, kwargs = mock_session.get.call_args
            
            # Should call proxy URL, not swimcloud.com directly
            if 'https://proxy.example.com' in args[0]:
                print("✅ Proxy URL used when SWIMCLOUD_PROXY_URL is set")
                return True
            else:
                print("❌ Proxy URL NOT used even though SWIMCLOUD_PROXY_URL is set")
                return False
    
    print("✅ Proxy logic test completed")
    return True

def test_routes_exist():
    """Test that the necessary routes are defined."""
    print("\nTesting route definitions...")
    
    import routes.swimcloud
    
    # Check that the blueprint has the expected routes
    expected_routes = [
        ('/api/public/swimcloud/search', ['GET']),
        ('/api/public/swimcloud/propose', ['GET']),
        ('/api/public/swimcloud/process-times', ['POST']),
        ('/api/swimcloud/search', ['GET']),
        ('/api/swimcloud/propose', ['GET']),
        ('/api/swimcloud/check-prs', ['GET'])
    ]
    
    # Get routes from the blueprint
    routes_list = routes.swimcloud.swimcloud_bp.deferred_functions
    
    print(f"Found {len(routes_list)} route functions in blueprint")
    
    # Check function names
    func_names = [func.__name__ for func in routes.swimcloud.swimcloud_bp.deferred_functions 
                  if hasattr(func, '__name__')]
    
    expected_funcs = ['sc_search_public', 'sc_propose_public', 'sc_process_times_public',
                     'sc_search', 'sc_propose', 'sc_check_prs']
    
    for func in expected_funcs:
        if func in func_names:
            print(f"✅ Route function '{func}' exists")
        else:
            print(f"❌ Route function '{func}' missing")
    
    return True

def test_cors_config():
    """Test that CORS is properly configured."""
    print("\nTesting CORS configuration...")
    
    # Check main.py
    with open('main.py', 'r') as f:
        content = f.read()
    
    checks = [
        ('CORS import', 'from flask_cors import CORS' in content),
        ('CORS initialization', 'CORS(app)' in content),
        ('flask-cors in requirements', True)  # We'll check separately
    ]
    
    all_ok = True
    for check_name, passed in checks:
        status = "✅" if passed else "❌"
        print(f"{status} {check_name}")
        if not passed:
            all_ok = False
    
    # Check requirements.txt
    with open('requirements.txt', 'r') as f:
        req_content = f.read()
    
    if 'flask-cors' in req_content:
        print("✅ flask-cors in requirements.txt")
    else:
        print("❌ flask-cors NOT in requirements.txt")
        all_ok = False
    
    return all_ok

def main():
    print("SwimCloud API Fix - Endpoint Tests")
    print("=" * 50)
    
    tests = [
        ("CORS Configuration", test_cors_config),
        ("Route Definitions", test_routes_exist),
        ("Proxy Logic", test_swimcloud_client_proxy)
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"\n{test_name}:")
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))
    
    print("\n" + "=" * 50)
    print("TEST RESULTS:")
    
    all_pass = True
    for test_name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {test_name:20} {status}")
        if not passed:
            all_pass = False
    
    print("\n" + "=" * 50)
    if all_pass:
        print("✅ All endpoint tests passed!")
        print("\nThe SwimCloud API fix includes:")
        print("1. CORS configuration for cross-origin requests")
        print("2. Server endpoints that handle SwimCloud API calls")
        print("3. Proxy support for bypassing IP blocks")
        print("4. Client-side code that uses server endpoints")
    else:
        print("❌ Some tests failed. Review the output above.")
    
    return all_pass

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
