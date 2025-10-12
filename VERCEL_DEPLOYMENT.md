# ⚠️ IMPORTANT: Vercel Deployment Does NOT Work

## The Problem

**This Python Playwright application CANNOT run on Vercel** due to fundamental limitations:

### Why It Fails:
- **Playwright's Chromium binary**: ~280MB (too large)
- **Vercel's function size limit**: 250MB uncompressed (not enough)
- **No lightweight alternative** for Python (unlike JavaScript which has `@sparticuz/chromium`)
- **Browser installation impossible** in serverless Python environment

### Error You'll Encounter:
```
Executable doesn't exist at /home/sbx_user1051/.cache/ms-playwright/chromium...
Please run: playwright install
```

This error cannot be fixed on Vercel's serverless platform.

---

## ✅ Recommended Alternatives

### Option 1: Railway.app (BEST - Free & Easy)

**Why Railway:**
- ✅ 500 free hours/month
- ✅ Playwright works perfectly
- ✅ One-command deployment
- ✅ No code changes needed

**Deploy to Railway:**
```bash
# 1. Install Railway CLI
npm i -g @railway/cli

# 2. Login
railway login

# 3. Deploy from your project directory
railway up

# 4. Add your cookies as environment variable
railway variables set ACCOUNT_COOKIES="your_cookies_json"

# 5. Open your deployed app
railway open
```

Your API will work exactly as it does on Replit!

### Option 2: Render.com (Also Free)

**Why Render:**
- ✅ 750 free hours/month
- ✅ Git-based deployment
- ✅ Playwright support

**Deploy to Render:**
1. Push code to GitHub
2. Go to [render.com](https://render.com)
3. Create new "Web Service"
4. Connect your repo
5. Set build command: `pip install -r requirements.txt && playwright install chromium`
6. Set start command: `python app.py`
7. Add environment variable: `ACCOUNT_COOKIES`

### Option 3: Fly.io

**Why Fly:**
- ✅ 3 free VMs
- ✅ Global deployment
- ✅ Excellent performance

**Deploy to Fly:**
```bash
# 1. Install flyctl
curl -L https://fly.io/install.sh | sh

# 2. Launch app
fly launch

# 3. Deploy
fly deploy
```

### Cookie Management on Vercel

Since `account.txt` won't be deployed, the app will read cookies from the `ACCOUNT_COOKIES` environment variable:

1. In Vercel Dashboard → Settings → Environment Variables
2. Create `ACCOUNT_COOKIES` variable
3. Paste your entire account.txt content (JSON format recommended)

### Static Files Note

Images are stored in `/tmp` on Vercel (serverless limitation). Note:
- `/tmp` is cleared between function invocations
- Images may not persist long-term
- Consider using Vercel Blob Storage for persistent image storage

### Testing Your Deployment

After deployment, test your endpoints:
```bash
# Check if API is alive
curl https://your-app.vercel.app/

# Test image generation
curl "https://your-app.vercel.app/gimage?prompt=a%20cute%20cat"
```

### Files Added for Vercel

- `vercel.json` - Vercel configuration
- `api/index.py` - Serverless function entry point
- `requirements.txt` - Python dependencies
- `.vercelignore` - Files to exclude from deployment

### Troubleshooting

**Issue**: "Failed to install Playwright browsers"
- Playwright binaries may be too large for Vercel
- Solution: Consider alternative deployment platforms

**Issue**: "Function timeout"
- Gemini automation takes time
- Solution: Upgrade to Vercel Pro for 60s timeout

**Issue**: "Images not persisting"
- Serverless functions clear /tmp between runs
- Solution: Use Vercel Blob Storage or external image hosting
