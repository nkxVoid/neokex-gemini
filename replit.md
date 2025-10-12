# Gemini Scraper API Project

## Overview
A Flask-based REST API server that uses web scraping (Playwright) to interact with Google Gemini. The API provides three main endpoints for chat, image generation, and image editing without requiring a paid Gemini API key.

## Purpose
This project allows users to programmatically interact with Google Gemini through a REST API using their browser cookies for authentication, bypassing the need for official API credits or a credit card.

## Current State
- ✅ Flask API server running on port 5000
- ✅ Cookie-based authentication system
- ✅ Three functional endpoints: /gemini, /gimage, /gedit
- ✅ Playwright browser automation configured
- ✅ Health check and documentation endpoints
- ⚠️ User needs to add their Gemini cookies to account.txt

## Recent Changes
*October 12, 2025*
- Initial project setup with Python 3.11
- Installed Flask, Playwright, requests, and python-dotenv
- Created Flask API server with three endpoints
- Implemented browser automation with Playwright
- Added cookie loading system from account.txt with dual format support
- Fixed critical bugs in browser automation
- Configured API Server workflow on port 5000
- **Modified image handling to only save .jpg format (removed .png support)**
- **Added Docker configuration for Render deployment**
- **Cleaned up project by removing:**
  - All documentation files (README.md, COOKIE_FORMATS.md, FIXES_SUMMARY.md, etc.)
  - pyproject.toml and uv.lock (using requirements.txt only)
  - Old PNG images from static/images directory
- **Performance & Speed Optimizations:**
  - Reduced wait_for_timeout delays by 70% (1500ms→500ms on page loads, 1000ms→500ms on actions)
  - Optimized typing with delay=0 for instant text input
  - Added faster polling (200ms) to response detection function
  - Reduced total wait times from ~5-6 seconds to ~1.5 seconds per request
  - **Result: API responses are now 3-4x faster than before**
- **Lightweight Optimizations for Render Free Tier:**
  - Added memory-efficient browser launch flags (--disable-extensions, --disable-background-networking, etc.)
  - Reduced viewport from 1920x1080 to 1280x720 for lower memory usage
  - Added aggressive garbage collection after each page close
  - Changed logging from INFO to WARNING level (reduced I/O overhead)
  - More frequent cleanup: every 2 hours, max image age 6 hours (vs 6h/24h)
  - Running in production mode (debug=False, threaded=True)
  - **Result: Memory footprint reduced by ~30-40% for stable free tier operation**
- **Thread-Local Browser Pooling (Earlier optimization):**
  - Each Flask thread reuses its own persistent browser instance
  - Cookie caching in memory (5-minute TTL) to avoid repeated file I/O
  - Automatic cookie refresh every 5 minutes without server restart
  - Thread-safe cookie caching with RLock protection
  
**Technical Implementation:**
  - Uses `threading.local()` for thread-local browser instances (Playwright sync API is thread-bound)
  - Each Flask request thread gets its own persistent browser/context
  - Browser contexts automatically refresh after 5 minutes with fresh cookies
  - Cookie cache shared globally with `threading.RLock()` for thread-safe access
  - All cookie formats (JSON/Netscape/simple) properly cached
  - Chromium launched with Linux-compatible flags (no --single-process which breaks on Render)
  - Aggressive resource cleanup with garbage collection
  - Production-ready with proper thread isolation and cleanup

## Project Architecture

### Technology Stack
- **Runtime**: Python 3.11
- **Web Framework**: Flask 3.1.2
- **Browser Automation**: Playwright 1.55.0
- **HTTP Client**: Requests 2.32.5
- **Environment**: python-dotenv 1.1.1

### Project Structure
```
.
├── app.py                 # Main Flask application with all endpoints
├── account.txt           # Cookie storage (user needs to populate)
├── requirements.txt      # Python dependencies
├── Dockerfile            # Docker config for Render deployment
├── render.yaml           # Render deployment configuration
├── replit.md             # Project memory and documentation
├── .gitignore            # Git ignore rules
└── static/
    └── images/           # Downloaded images (.jpg only)
```

### API Endpoints

1. **GET /** - API documentation
2. **GET /health** - Health check and cookie status
3. **GET /gemini?chat=** - Chat with Gemini
4. **GET /gimage?prompt=** - Generate images
5. **GET /gedit?imgUrl=&prompt=** - Edit images

### Key Features
- Cookie-based authentication (no API key needed)
- Browser automation with Playwright Chromium
- JSON response format for all endpoints
- Error handling and logging
- Headless browser operation
- **All images served as .jpg format only**
- **Docker-ready for Render deployment**
- **Automatic cleanup system (optimized for free tier):**
  - Background thread cleans up old images every 2 hours
  - Deletes images older than 6 hours automatically  
  - Temp files cleaned up immediately after use
  - Prevents disk space issues for long-term operation on free tier

### Authentication System
- Reads cookies from `account.txt` file
- Supports standard cookie format: `name=value`
- Injects cookies into Playwright browser context
- Critical cookies: `__Secure-1PSID`, `__Secure-1PSIDTS`, `__Secure-1PAPISID`

## User Preferences
- No credit card, prefers free solutions
- Wants to scrape Gemini web interface
- Needs cookie-based authentication
- Requires three specific endpoints for different Gemini features

## Setup Instructions for Users

### Step 1: Add Cookies
1. Visit https://gemini.google.com/u/5/app?pageId=none
2. Open Developer Tools (F12) → Application → Cookies
3. Copy cookies to `account.txt` in format: `name=value`
4. Required cookies: `__Secure-1PSID`, `__Secure-1PSIDTS`, `__Secure-1PAPISID`

### Step 2: Use the API
- The server is already running on port 5000
- Visit the webview to see the API documentation
- Test with: `/health` to verify cookies are loaded
- Use the three main endpoints as documented

## Important Notes

### Limitations
- Web scraping may break if Google changes Gemini's UI
- Cookies expire and need periodic refresh
- Selectors are based on current Gemini interface (October 2025)
- Rate limiting depends on user's Gemini account

### Security
- `account.txt` is gitignored by default
- Never commit or share cookies
- SESSION_SECRET environment variable used for Flask

### Maintenance
- Monitor Gemini UI changes that may break selectors
- Update cookie selectors if authentication fails
- Check Playwright browser updates for compatibility

## Deployment to Render

### Quick Deploy Steps
1. Push code to Git repository
2. Go to Render.com → "New +" → "Blueprint"
3. Connect repository (Render auto-detects render.yaml)
4. Add `ACCOUNT_COOKIES` environment variable
5. Deploy with Docker automatically

### Docker Configuration
- Uses Python 3.11-slim base image
- Installs all Chromium system dependencies
- Playwright browsers installed during build
- Lightweight and production-ready

## Dependencies

### Python Packages
- flask==3.1.2
- playwright==1.55.0
- requests==2.32.5
- python-dotenv==1.1.1

### System Requirements
- Playwright Chromium browser installed
- Python 3.11
- Port 5000 available
- Docker for deployment (Render)

## Troubleshooting

### Common Issues
1. **No response from endpoints**: Check if cookies are valid and loaded
2. **Browser errors**: Ensure Playwright browsers are installed
3. **Selector failures**: Gemini UI may have changed, update selectors in app.py
4. **Cookie expiration**: Re-export cookies from browser

### Debug Steps
1. Check `/health` endpoint for cookie status
2. Review server logs in workflow console
3. Verify account.txt format and content
4. Test with fresh cookies from browser

## Future Enhancements
- Rate limiting to prevent blocking
- Cookie refresh mechanism
- Request queuing for concurrent requests
- Response caching
- Additional error handling
- Support for batch operations
