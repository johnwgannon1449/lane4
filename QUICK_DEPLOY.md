# Quick Deployment Guide for SwimCloud API Fix

## Option A: Quick Fix (Public Proxy)
1. **Go to Render Dashboard**: https://dashboard.render.com/
2. **Select your 'lane4' service**
3. **Click 'Environment'**
4. **Add environment variable**:
   - Key: `USE_PUBLIC_PROXY`
   - Value: `true`
5. **Click 'Save Changes'**
6. **Redeploy**: Manual Deploy → Deploy latest commit
7. **Test**: Search for "Joseph Gannon" in onboarding

**Note**: Uses free public proxy (corsproxy.io). Slower but works immediately.

## Option B: Better Fix (Cloudflare Worker)
1. **Deploy Cloudflare Worker**:
   - Go to: https://dash.cloudflare.com/
   - Workers & Pages → Create Worker
   - Name: `swimcloud-proxy`
   - Deploy, then Edit code
   - Paste from `cloudflare_worker/swimcloud_proxy.js`
   - Save and deploy
   - Copy Worker URL

2. **Configure Render**:
   - Add environment variable:
     - Key: `SWIMCLOUD_PROXY_URL`
     - Value: `https://swimcloud-proxy.YOURNAME.workers.dev`
   - Save and redeploy

3. **Test**: Search for "Joseph Gannon"

## Option C: Test Locally First
```bash
# Start local proxy
python3 simple_proxy.py

# In another terminal
export SWIMCLOUD_PROXY_URL=http://localhost:8080
python3 main.py

# Test in browser: http://localhost:5000
```

## Files Modified
- `main.py` - Added CORS
- `swimcloud_client.py` - Added proxy support + public proxy fallback
- `static/index.html` - Fixed client-side API calls

## Verification
Run: `python3 verify_fix.py`
All checks should pass ✅

## Immediate Action
1. **Try Option A first** (easiest)
2. **If slow, use Option B** (better long-term)
3. **Commit changes**: Already done

## Expected Result
After deployment, onboarding should:
1. Search swimmers by name
2. Show matching profiles
3. Allow importing swim times
