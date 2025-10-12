# Vercel Deployment Guide

## 🚀 How to Deploy to Vercel

### Prerequisites
- A Vercel account (sign up at [vercel.com](https://vercel.com))
- Vercel CLI installed (optional, for command line deployment)

### Deployment Steps

#### Option 1: Deploy via Vercel Dashboard (Recommended)
1. Go to [vercel.com](https://vercel.com) and log in
2. Click "Add New Project"
3. Import your Git repository (GitHub, GitLab, or Bitbucket)
4. Vercel will auto-detect the settings
5. Click "Deploy"

#### Option 2: Deploy via Vercel CLI
```bash
# Install Vercel CLI
npm i -g vercel

# Deploy
vercel
```

### Important: Environment Variables

After deployment, you **MUST** add your environment variables in Vercel:

1. Go to your project in Vercel Dashboard
2. Navigate to **Settings** → **Environment Variables**
3. Add the following variable:
   - `ACCOUNT_COOKIES` = (paste your account.txt contents here)

### Important: Playwright Setup

⚠️ **Note**: Playwright with Chromium may have limitations on Vercel's serverless platform due to:
- Binary size limits (250MB uncompressed, 50MB compressed)
- Function execution timeout (10-60 seconds depending on plan)
- Memory constraints

If you encounter issues, consider:
1. Using Vercel Pro plan for higher limits
2. Using a lighter browser automation tool
3. Hosting on a platform with persistent servers (Railway, Render, Fly.io)

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
