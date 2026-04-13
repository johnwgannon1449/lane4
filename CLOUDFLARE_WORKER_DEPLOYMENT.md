# Cloudflare Worker Deployment Guide for SwimCloud API

## Problem
SwimCloud blocks requests from Render's datacenter IPs with Cloudflare WAF (403 errors).

## Solution
Deploy a Cloudflare Worker that acts as a proxy. The Worker runs on Cloudflare's network (not blocked) and forwards requests to SwimCloud.

## Step-by-Step Deployment

### 1. Create Cloudflare Account (if you don't have one)
- Go to https://dash.cloudflare.com/sign-up
- Sign up for free account
- Verify your email

### 2. Create a Worker
1. Go to **Workers & Pages** in Cloudflare dashboard
2. Click **Create application**
3. Click **Create Worker**
4. Name your worker (e.g., `swimcloud-proxy`)
5. Click **Deploy**

### 3. Configure the Worker
1. After deployment, click **Edit code**
2. Delete all existing code in the editor
3. Copy the entire content from `cloudflare_worker/swimcloud_proxy.js`
4. Paste it into the editor
5. Click **Save and deploy**

### 4. Get Your Worker URL
1. After deployment, you'll see your Worker URL
2. It will look like: `https://swimcloud-proxy.your-username.workers.dev`
3. Copy this URL

### 5. Configure Render Environment
1. Go to your Render dashboard
2. Select your Lane4 service
3. Click **Environment**
4. Click **Add Environment Variable**
5. Add:
   - **Key**: `SWIMCLOUD_PROXY_URL`
   - **Value**: Your Worker URL (e.g., `https://swimcloud-proxy.your-username.workers.dev`)
6. Click **Save Changes**

### 6. Redeploy on Render
1. In Render dashboard, go to your Lane4 service
2. Click **Manual Deploy**
3. Click **Deploy latest commit**
4. Wait for deployment to complete

## Testing the Worker

### Test 1: Direct Worker Test
```bash
curl "https://swimcloud-proxy.your-username.workers.dev/?url=https://www.swimcloud.com/api/search/?q=Joseph%20Gannon&type=swimmer"
```
Should return JSON with swimmer results.

### Test 2: Test Onboarding Flow
1. Go to your Lane4 app URL
2. Start onboarding
3. Search for "Joseph Gannon"
4. Should now show swimmer profiles

## Worker Code Overview

The Worker (`cloudflare_worker/swimcloud_proxy.js`):
- Accepts GET requests with `url` parameter
- Validates URL contains `swimcloud.com`
- Forwards request to SwimCloud with proper headers
- Adds CORS headers to response
- Handles OPTIONS preflight requests

## Troubleshooting

### Worker returns 400 "Invalid URL"
- Make sure `url` parameter includes `swimcloud.com`
- URL should be properly encoded

### Worker returns 500 error
- Check Cloudflare Worker logs
- Might be SwimCloud blocking even Cloudflare IPs (unlikely)

### Still getting "Couldn't find [name]"
1. Check Render logs for errors
2. Verify `SWIMCLOUD_PROXY_URL` is set correctly
3. Test Worker directly (see above)
4. Check if SwimCloud API changed

### CORS errors in browser
- Worker should handle CORS
- Check Worker code has proper CORS headers
- Browser might cache old responses - try hard refresh

## Alternative: Quick Test Without Worker

If you want to test quickly without deploying Worker:

1. **Run local proxy** (for testing only):
   ```bash
   python3 simple_proxy.py
   ```
   
2. **Set environment variable locally**:
   ```bash
   export SWIMCLOUD_PROXY_URL=http://localhost:8080
   python3 main.py
   ```

3. **Test locally** - should work, but won't help Render deployment

## Security Notes

- Worker only allows `swimcloud.com` URLs
- Only GET requests allowed
- No authentication needed (public API)
- Cloudflare provides DDoS protection

## Maintenance

- Worker code is in `cloudflare_worker/swimcloud_proxy.js`
- Update Worker if SwimCloud API changes
- Monitor Cloudflare Worker usage (free tier has limits)

## Support
If issues persist:
1. Check Render deployment logs
2. Test Worker directly with curl
3. Check SwimCloud API status
4. Contact for help with deployment
