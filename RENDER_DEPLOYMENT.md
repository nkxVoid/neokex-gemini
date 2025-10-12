# Render Deployment Guide

## 🚀 How to Deploy to Render

### Prerequisites
- A Render account (sign up at [render.com](https://render.com))
- Your code pushed to GitHub, GitLab, or Bitbucket

---

## Deployment Steps

### Method 1: Using render.yaml (Recommended)

The project includes a `render.yaml` file that automates deployment:

1. **Push to Git**
   ```bash
   git add .
   git commit -m "Ready for Render deployment"
   git push
   ```

2. **Connect to Render**
   - Go to [render.com](https://render.com)
   - Click "New +" → "Blueprint"
   - Connect your Git repository
   - Render will auto-detect `render.yaml`
   - Click "Apply"

3. **Set Environment Variables**
   - After deployment, go to your service
   - Navigate to "Environment" tab
   - Add `ACCOUNT_COOKIES` with your cookie JSON

### Method 2: Manual Deployment

1. **Create New Web Service**
   - Go to [render.com](https://render.com)
   - Click "New +" → "Web Service"
   - Connect your repository

2. **Configure Settings**
   - **Name**: gemini-api (or your choice)
   - **Runtime**: Python 3
   - **Build Command**: 
     ```bash
     pip install -r requirements.txt && playwright install chromium
     ```
   - **Start Command**: 
     ```bash
     python app.py
     ```

3. **Environment Variables**
   - Click "Advanced" → "Add Environment Variable"
   - Add:
     - `PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS` = `true`
     - `ACCOUNT_COOKIES` = (your cookies in JSON format)

4. **Deploy**
   - Click "Create Web Service"
   - Wait for build to complete (~3-5 minutes)

---

## Environment Variables Setup

### ACCOUNT_COOKIES Format

Your cookies should be in JSON array format:

```json
[
  {
    "name": "__Secure-1PSID",
    "value": "your_cookie_value",
    "domain": ".google.com",
    "path": "/"
  },
  {
    "name": "__Secure-1PSIDTS",
    "value": "your_cookie_value",
    "domain": ".google.com",
    "path": "/"
  }
]
```

### How to Add in Render:
1. Go to your service dashboard
2. Click "Environment" in left sidebar
3. Click "Add Environment Variable"
4. Key: `ACCOUNT_COOKIES`
5. Value: Paste your JSON cookie array
6. Click "Save Changes"

---

## Free Tier Limits

Render's free tier includes:
- ✅ 750 hours/month (enough for 24/7 operation)
- ✅ Automatic SSL/HTTPS
- ✅ Custom domains supported
- ✅ Auto-deploys from Git

**Note**: Free services spin down after 15 min of inactivity. First request after spindown takes ~30 seconds.

---

## Testing Your Deployment

After deployment, test your endpoints:

```bash
# Get your Render URL (e.g., https://gemini-api.onrender.com)

# Test if API is alive
curl https://your-app.onrender.com/

# Test image generation
curl "https://your-app.onrender.com/gimage?prompt=a%20cute%20cat"

# Test chat
curl "https://your-app.onrender.com/gemini?chat=hello"
```

---

## Updating Your App

Render auto-deploys on Git push:

```bash
# Make your changes
git add .
git commit -m "Update API"
git push

# Render automatically rebuilds and deploys
```

---

## Troubleshooting

### Build Fails: "Playwright installation error"
- **Solution**: Render's build command installs Chromium automatically
- Check build logs for specific error
- Ensure `playwright install chromium` is in build command

### Service Won't Start
- Check "Logs" tab in Render dashboard
- Verify port 5000 is used (Flask default)
- Ensure all environment variables are set

### Slow First Request
- **Normal behavior** on free tier
- Service spins down after 15min inactivity
- Upgrade to paid plan ($7/month) for always-on service

### Images Not Loading
- Check if `/images/<filename>` route works
- Verify `static/images/` directory permissions
- Check logs for image download errors

---

## Files for Render Deployment

- ✅ `render.yaml` - Automated deployment configuration
- ✅ `requirements.txt` - Python dependencies
- ✅ `app.py` - Main application
- ✅ `.gitignore` - Excludes unnecessary files

---

## Cost Optimization

**Free tier is enough if:**
- You're okay with cold starts (15min inactivity → 30s first request)
- Usage is under 750 hours/month

**Upgrade to paid ($7/month) for:**
- Always-on service (no cold starts)
- Better performance
- More memory/CPU

---

## Next Steps

1. ✅ Deploy using render.yaml
2. ✅ Set `ACCOUNT_COOKIES` environment variable
3. ✅ Test all endpoints
4. ✅ (Optional) Add custom domain
5. ✅ (Optional) Set up monitoring

Your API will work exactly as it does on Replit! 🚀
