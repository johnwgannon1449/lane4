# FINAL SOLUTION: SwimCloud API Fix

## The Problem
SwimCloud blocks:
1. Render's datacenter IPs (403 Forbidden)
2. Most public proxy services
3. Server-side requests from proxies

## The Solution
We need a proxy that:
1. Runs on a non-datacenter IP
2. Looks like a real browser
3. Allows server-side requests

## Option 1: EASIEST (PythonAnywhere)
1. **Sign up** at https://www.pythonanywhere.com (free tier)
2. **Create new Web App**
3. **Upload files**:
   - `python_proxy.py`
   - `python_proxy_requirements.txt`
4. **Configure**:
   - Source code: `python_proxy.py`
   - WSGI: `from python_proxy import app`
5. **Get URL**: `https://yourusername.pythonanywhere.com`
6. **Configure Render**:
   - Add: `SWIMCLOUD_PROXY_URL=https://yourusername.pythonanywhere.com`
7. **Redeploy Render**

## Option 2: Replit (Also Easy)
1. **Sign up** at https://replit.com (free)
2. **Create new Python repl**
3. **Upload** `python_proxy.py`
4. **Run** - get URL like `https://replname.username.repl.co`
5. **Configure Render** with that URL
6. **Redeploy**

## Option 3: Cloudflare Worker (Best)
1. **Deploy** `cloudflare_worker/swimcloud_proxy.js` as Worker
2. **Get URL**: `https://swimcloud-proxy.username.workers.dev`
3. **Configure Render** with that URL
4. **Redeploy**

## Option 4: Vercel (Good)
1. **Deploy** `vercel_proxy/` folder to Vercel
2. **Get URL**
3. **Configure Render**
4. **Redeploy**

## Immediate Test
Try this **right now**:

```bash
# Test if PythonAnywhere would work
curl "https://www.swimcloud.com/api/search/?q=Joseph%20Gannon&type=swimmer"
# If 403, you need a proxy

# Test with a free hosting service
# 1. Go to https://replit.com
# 2. Create Python repl
# 3. Paste python_proxy.py
# 4. Run, get URL
# 5. Test: curl "YOUR_REPL_URL/?url=https://www.swimcloud.com/api/search/?q=Joseph%20Gannon&type=swimmer"
```

## Files You Need
- `python_proxy.py` - Run anywhere
- `cloudflare_worker/swimcloud_proxy.js` - Cloudflare Worker
- `vercel_proxy/` - Vercel deployment

## Quick Start
1. **Pick Option 1** (PythonAnywhere) - easiest
2. **Deploy proxy**
3. **Set SWIMCLOUD_PROXY_URL in Render**
4. **Redeploy**
5. **Test onboarding**

## Why This Works
- Proxy runs on different IP (not Render)
- Adds proper browser headers
- Bypasses SwimCloud blocks

## If Still Blocked
SwimCloud might block ALL server requests. In that case:
1. **Contact SwimCloud** for API access
2. **Use alternative data source**
3. **Manual entry only** (fallback)

## Commit & Push
All fixes are ready. Commit and push:

```bash
git add .
git commit -m "Add multiple proxy options for SwimCloud API fix"
git push
```

Then deploy proxy and configure Render.
