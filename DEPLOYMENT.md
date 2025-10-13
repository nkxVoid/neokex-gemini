# Deployment Guide

## Platform Compatibility

### ✅ COMPATIBLE PLATFORMS
Platforms that support Docker and browser automation:

#### 1. Render (Recommended)
- **Free tier:** 512MB RAM, sufficient for this API
- **Setup:** Automatic via `render.yaml`
- **Deployment:**
  ```bash
  1. Push code to GitHub
  2. Connect to Render
  3. Add ACCOUNT_COOKIES environment variable
  4. Deploy automatically
  ```

#### 2. Railway.app
- Docker support
- Good for Playwright apps
- Similar pricing to Render

#### 3. Fly.io
- Global edge deployment
- Docker support
- Free tier available

#### 4. Google Cloud Run
- Serverless containers
- Browser automation support
- More complex but scalable

---

### ❌ INCOMPATIBLE PLATFORMS
These will **NOT** work:

#### Netlify
- ❌ Static site hosting only
- ❌ No browser automation support
- ❌ 10-second timeout limit
- ❌ No Chromium support

#### Vercel
- ❌ Serverless functions only
- ❌ No persistent browser processes
- ❌ Limited to 10-second execution

#### AWS Lambda (without custom setup)
- ❌ Requires custom layers for Chromium
- ❌ Complex configuration needed

---

## Why This API Needs Docker

1. **Browser Automation (Playwright)**
   - Requires Chromium browser (200-300MB)
   - Needs system dependencies
   - Requires persistent processes

2. **Resource Requirements**
   - Memory: 400-512MB minimum
   - Execution time: 30-60 seconds per request
   - System-level dependencies

3. **Long-Running Processes**
   - Browser instances stay alive between requests
   - Not suitable for serverless functions

---

## Recommended: Deploy to Render

### Step 1: Prepare Your Repository
```bash
git add .
git commit -m "Ready for deployment"
git push origin main
```

### Step 2: Deploy to Render
1. Go to [render.com](https://render.com)
2. Click "New +" → "Blueprint"
3. Connect your GitHub repository
4. Render auto-detects `render.yaml`
5. Add environment variable: `ACCOUNT_COOKIES` (your Gemini cookies)
6. Click "Apply"

### Step 3: Monitor Your Deployment
- Check `/health` endpoint for memory usage
- Monitor logs for browser refresh events
- Ensure memory stays below 400MB

---

## Environment Variables

### Required
- `ACCOUNT_COOKIES` - Your Gemini cookies (JSON format)

### Optional
- `PORT` - Default: 5000
- `SESSION_SECRET` - Flask secret key

---

## Monitoring

### Health Check
```bash
curl https://your-app.onrender.com/health
```

Response:
```json
{
  "status": "healthy",
  "memory_usage_mb": 45.24,
  "memory_percent": 0.07,
  "thread_count": 2,
  "cookies_loaded": true
}
```

### What to Monitor
- Memory usage should stay < 400MB
- Browser refreshes logged every 50 requests
- Temp file cleanup on errors
