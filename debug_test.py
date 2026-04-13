#!/usr/bin/env python3
import requests
import sys

# Test with more debugging
url = 'http://localhost:5000/api/public/swimcloud/search?q=test'

try:
    response = requests.get(url, timeout=5)
    print(f'Status: {response.status_code}')
    print(f'Headers: {dict(response.headers)}')
    print(f'Body length: {len(response.text)}')
    print(f'Body: {response.text[:500]}')
    
    # Also test a different endpoint
    print('\\n--- Testing root endpoint ---')
    root_response = requests.get('http://localhost:5000/', timeout=5)
    print(f'Root Status: {root_response.status_code}')
    
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
