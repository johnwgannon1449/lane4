# SwimCloud API Fix - Deployment Instructions

## Problem
SwimCloud blocks requests from Render's datacenter IP addresses with Cloudflare WAF (403 errors).

## Solution Implemented

### 1. Client-Side Fetching (Primary Solution)
- Browser fetches SwimCloud API directly (bypasses IP block)
- Server processes raw data from browser
- CORS configured to allow cross-origin requests

### 2. Cloudflare Worker Proxy (Fallback Solution)
- If client-side fetching has CORS issues
- Deploy `cloudflare_worker/swimcloud_proxy.js` as a Cloudflare Worker
- Set `SWIMCLOUD_PROXY_URL` environment variable in Render

## Files Modified

1. `main.py` - Added CORS configuration
2. `swimcloud_client.py` - Fixed type hints, added proxy support
3. `cloudflare_worker/swimcloud_proxy.js` - Cloudflare Worker script
4. `test_swimcloud_fix.py` - Test script

## Testing Locally

1. Run the test script:
   ```bash
   python test_swimcloud_fix.py
   ```

2. Start the app:
   ```bash
   python main.py
   ```

3. Open browser to http://localhost:5000
4. Test onboarding flow with SwimCloud search

## Deployment to Render

### Option A: Client-Side Fetching (Recommended)
1. Push changes to GitHub
2. Render will auto-deploy
3. No additional configuration needed

### Option B: Cloudflare Worker Proxy (if Option A has issues)
1. Deploy Cloudflare Worker:
   - Go to Cloudflare Dashboard → Workers & Pages
   - Create new Worker
   - Paste code from `cloudflare_worker/swimcloud_proxy.js`
   - Deploy

2. Configure Render environment variable:
   - In Render dashboard, go to Environment
   - Add variable: `SWIMCLOUD_PROXY_URL` = `https://your-worker.workers.dev`

3. Redeploy app

## Verification

After deployment:
1. Complete onboarding flow
2. Search for a swimmer name
3. Select a swimmer profile
4. Verify times are imported correctly

## Troubleshooting

### CORS Errors in Browser Console
- Check CORS is configured in `main.py`
- Verify `flask-cors` is in requirements.txt
- Check browser console for errors

### 403 Errors from SwimCloud
- Expected for server-side requests from Render
- Client-side fetching should bypass this
- If persists, use Cloudflare Worker proxy

### Proxy Not Working
- Verify Cloudflare Worker is deployed
- Check `SWIMCLOUD_PROXY_URL` environment variable
- Test proxy directly: `curl "https://your-worker.workers.dev?url=https://www.swimcloud.com/api/search/?q=test&type=swimmer"`

## Code Changes Summary

### main.py
- Added `from flask_cors import CORS`
- Added `CORS(app)` after Flask app creation

### swimcloud_client.py
- Fixed type hints for Python 3.9 compatibility
- Added proxy support via `SWIMCLOUD_PROXY_URL` environment variable
- Added `import os` and `import json`

### New Files
- `cloudflare_worker/swimcloud_proxy.js` - Cloudflare Worker proxy
- `test_swimcloud_fix.py` - Test script
