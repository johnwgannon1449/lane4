#!/usr/bin/env python3
"""
Test if the local fix works without Cloudflare Worker.
"""
import subprocess
import time
import requests
import sys

def start_app():
    """Start the Flask app in background."""
    print("Starting Flask app...")
    # Start app in background
    proc = subprocess.Popen(
        ['python3', 'main.py'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    # Wait for app to start
    time.sleep(3)
    return proc

def test_endpoint():
    """Test the swimcloud search endpoint."""
    print("\nTesting /api/public/swimcloud/search...")
    try:
        response = requests.get(
            'http://localhost:5000/api/public/swimcloud/search',
            params={'q': 'Joseph Gannon'},
            timeout=10
        )
        print(f"Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            if 'results' in data:
                print(f"✅ Success! Found {len(data['results'])} results")
                return True
            elif 'error' in data:
                print(f"❌ Error: {data.get('error')}")
                print(f"   Detail: {data.get('detail', 'No detail')}")
                return False
        else:
            print(f"❌ Failed with status {response.status_code}")
            print(f"Response: {response.text[:200]}")
            return False
            
    except Exception as e:
        print(f"❌ Exception: {e}")
        return False

def main():
    print("Testing Local SwimCloud Fix")
    print("=" * 60)
    
    # Start app
    proc = start_app()
    
    try:
        # Test endpoint
        success = test_endpoint()
        
        print("\n" + "=" * 60)
        if success:
            print("✅ LOCAL FIX WORKS!")
            print("\nThe server endpoints are working correctly.")
            print("If this works locally but not on Render, it's because:")
            print("1. SwimCloud blocks Render's IPs")
            print("2. You need the Cloudflare Worker proxy")
        else:
            print("❌ LOCAL FIX FAILED")
            print("\nThe server is still getting blocked by SwimCloud.")
            print("You DEFINITELY need the Cloudflare Worker proxy.")
        
    finally:
        # Kill the app
        print("\nStopping Flask app...")
        proc.terminate()
        proc.wait()

if __name__ == '__main__':
    main()
