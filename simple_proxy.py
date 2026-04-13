#!/usr/bin/env python3
"""
Simple local proxy to test the SwimCloud API fix.
Run this locally, then set SWIMCLOUD_PROXY_URL=http://localhost:8080
"""
from flask import Flask, request, jsonify, Response
import requests
import json

app = Flask(__name__)

@app.route('/', methods=['GET'])
def proxy():
    """Simple proxy endpoint."""
    target_url = request.args.get('url')
    
    if not target_url:
        return jsonify({'error': 'Missing url parameter'}), 400
    
    if 'swimcloud.com' not in target_url:
        return jsonify({'error': 'Only swimcloud.com URLs allowed'}), 400
    
    # Parse params if provided
    params = {}
    params_str = request.args.get('params')
    if params_str:
        try:
            params = json.loads(params_str)
        except:
            pass
    
    # Build request
    try:
        response = requests.get(
            target_url,
            params=params,
            headers={
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'X-Requested-With': 'XMLHttpRequest',
                'Referer': 'https://www.swimcloud.com/',
            },
            timeout=10
        )
        
        # Return response with CORS headers
        return Response(
            response.content,
            status=response.status_code,
            headers={
                'Content-Type': response.headers.get('Content-Type', 'application/json'),
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type',
            }
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/', methods=['OPTIONS'])
def options():
    """Handle CORS preflight."""
    return '', 200, {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
    }

if __name__ == '__main__':
    print("Starting simple proxy on http://localhost:8080")
    print("Set SWIMCLOUD_PROXY_URL=http://localhost:8080")
    print("Then test with: curl 'http://localhost:8080/?url=https://www.swimcloud.com/api/search/?q=test&type=swimmer'")
    app.run(port=8080, debug=False)
