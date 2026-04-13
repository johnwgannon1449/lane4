#!/usr/bin/env python3
"""
Simple Python proxy for SwimCloud API.
Run this on any Python hosting service (PythonAnywhere, Replit, etc.)
"""
from flask import Flask, request, jsonify, Response
import requests
import os

app = Flask(__name__)

@app.route('/', methods=['GET'])
def proxy():
    """Proxy endpoint for SwimCloud API."""
    target_url = request.args.get('url')
    
    if not target_url:
        return jsonify({'error': 'Missing url parameter'}), 400
    
    if 'swimcloud.com' not in target_url:
        return jsonify({'error': 'Only swimcloud.com URLs allowed'}), 400
    
    try:
        # Forward request with browser-like headers
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.swimcloud.com/',
            'X-Requested-With': 'XMLHttpRequest',
        }
        
        response = requests.get(target_url, headers=headers, timeout=10)
        
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
    port = int(os.environ.get('PORT', 8080))
    print(f"Starting SwimCloud proxy on port {port}")
    print(f"Set SWIMCLOUD_PROXY_URL=http://your-host:{port}/")
    app.run(host='0.0.0.0', port=port, debug=False)
