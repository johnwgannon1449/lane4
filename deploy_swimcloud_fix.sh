#!/bin/bash
# Deployment script for SwimCloud API fix
# Run this script step by step

set -e  # Exit on error

echo "================================================"
echo "SwimCloud API Fix - Complete Deployment Guide"
echo "================================================"
echo ""
echo "This script will guide you through deploying the fix."
echo "You need to complete some steps manually in browsers."
echo ""

# Step 1: Verify changes are committed
echo "Step 1: Verify Git status"
echo "--------------------------"
git status --short
echo ""
read -p "Are all changes committed? (y/n): " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Please commit changes first: git add . && git commit -m 'SwimCloud fix' && git push"
    exit 1
fi

# Step 2: Check Cloudflare Worker script
echo ""
echo "Step 2: Cloudflare Worker Script"
echo "--------------------------------"
echo "Checking Cloudflare Worker script..."
if [ -f "cloudflare_worker/swimcloud_proxy.js" ]; then
    echo "✅ Worker script found: cloudflare_worker/swimcloud_proxy.js"
    echo "Preview of first 10 lines:"
    head -10 cloudflare_worker/swimcloud_proxy.js
else
    echo "❌ Worker script not found!"
    exit 1
fi

# Step 3: Manual Cloudflare Deployment Instructions
echo ""
echo "Step 3: Deploy Cloudflare Worker (Manual)"
echo "------------------------------------------"
echo "You need to manually deploy the Cloudflare Worker:"
echo ""
echo "1. Go to: https://dash.cloudflare.com/"
echo "2. Sign in or create account"
echo "3. Go to 'Workers & Pages'"
echo "4. Click 'Create application'"
echo "5. Click 'Create Worker'"
echo "6. Name: swimcloud-proxy"
echo "7. Click 'Deploy'"
echo "8. Click 'Edit code'"
echo "9. Delete all code in editor"
echo "10. Copy this entire file:"
echo "    cat cloudflare_worker/swimcloud_proxy.js"
echo "11. Paste into editor"
echo "12. Click 'Save and deploy'"
echo "13. Copy your Worker URL (looks like: https://swimcloud-proxy.USERNAME.workers.dev)"
echo ""
read -p "Have you deployed the Cloudflare Worker and copied the URL? (y/n): " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Please deploy the Cloudflare Worker first."
    exit 1
fi

# Step 4: Get Worker URL
echo ""
echo "Step 4: Configure Render"
echo "------------------------"
echo "Enter your Cloudflare Worker URL:"
read WORKER_URL
echo ""
echo "Your Worker URL: $WORKER_URL"
echo ""
echo "Now configure Render:"
echo "1. Go to: https://dashboard.render.com/"
echo "2. Select your 'lane4' service"
echo "3. Click 'Environment'"
echo "4. Click 'Add Environment Variable'"
echo "5. Add:"
echo "   - Key: SWIMCLOUD_PROXY_URL"
echo "   - Value: $WORKER_URL"
echo "6. Click 'Save Changes'"
echo ""
read -p "Have you added SWIMCLOUD_PROXY_URL to Render? (y/n): " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Please configure Render first."
    exit 1
fi

# Step 5: Redeploy Render
echo ""
echo "Step 5: Redeploy on Render"
echo "--------------------------"
echo "Now redeploy your app on Render:"
echo "1. In Render dashboard, go to your 'lane4' service"
echo "2. Click 'Manual Deploy'"
echo "3. Click 'Deploy latest commit'"
echo "4. Wait for deployment (2-3 minutes)"
echo ""
read -p "Have you triggered a redeploy on Render? (y/n): " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Please redeploy on Render."
    exit 1
fi

# Step 6: Test
echo ""
echo "Step 6: Test the Fix"
echo "--------------------"
echo "After Render deployment completes:"
echo "1. Go to your Lane4 app URL"
echo "2. Start onboarding"
echo "3. Search for 'Joseph Gannon'"
echo "4. Should show swimmer profiles"
echo ""
echo "Quick test command (after deployment):"
echo "curl \"$WORKER_URL/?url=https://www.swimcloud.com/api/search/?q=Joseph%20Gannon&type=swimmer\""
echo ""
echo "================================================"
echo "Deployment Complete!"
echo "================================================"
echo ""
echo "If you have issues:"
echo "1. Check Render logs for errors"
echo "2. Test Worker directly with curl command above"
echo "3. Verify SWIMCLOUD_PROXY_URL is set correctly"
echo "4. Contact for help if needed"
