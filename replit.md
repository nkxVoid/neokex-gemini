# Gemini Scraper API Project

## Overview
This project provides a Flask-based REST API that enables programmatic interaction with Google Gemini for chat, image generation, and image editing. It leverages web scraping with Playwright and browser cookies for authentication, eliminating the need for a paid Gemini API key. The API aims to offer a free and accessible way to utilize Gemini's capabilities.

**Important:** This API requires Docker-based deployment (Render, Railway, Fly.io) and is NOT compatible with serverless platforms like Netlify or Vercel due to Playwright browser automation requirements.

## Recent Changes

*October 13, 2025 - Latest (v4)*
- **PERFORMANCE: API Response Speed Optimization** - Reduced wait times by ~50% (~4.9s vs ~9.5s)
  - **Optimized Wait Times**:
    - Initial image render wait: 1500ms (from 3000ms)
    - Scroll attempts: 2 iterations (from 3) with 800ms waits (from 1500ms)
    - Scroll back wait: 1000ms (from 2000ms)
  - **Enhanced Image Detection** - Better filtering for headless environments (Render/Railway)
    - Added dimension-based filtering: excludes images smaller than 100x100px (likely icons/profile pics)
    - Checks both URL patterns AND image dimensions for accurate filtering
    - Collects detailed image metadata (width, height, alt text) for debugging
  - **Improved Error Diagnostics**:
    - Enhanced debug messages include image dimensions and filtering reasons
    - Shows why specific images were filtered out (e.g., "profile picture", "too small")
    - Provides helpful hints when profile pictures are detected instead of generated images
    - Debug screenshots saved with detailed image analysis
  - **Result: Faster API responses (~50% reduction in wait time) with more reliable image detection and better debugging for Render/Railway deployments**

*October 13, 2025 - Previous (v3)*
- **FIXED: Image Generation Prompt Enhancement** - Automatic prompt optimization for reliable image generation
  - **Smart Prompt Detection**: Uses regex with word boundaries to detect if prompt needs enhancement
  - **Automatic Enhancement**: Prompts like "a cat" or "makeup concept art" are enhanced to "Generate an image of..."
  - **Edge Case Handling**: Correctly handles prompts with "make", "create" in middle of words (e.g., "makeup" won't false-match)
  - **Improved Image Filtering**: 
    - Prioritizes images from model response area first
    - Relaxed filtering to accept all Gemini-generated images (not just those with size parameters)
    - Only excludes UI elements (SVGs, logos, icons) and small profile pictures (with `/a/` path)
  - **Result: All prompts now trigger image generation correctly, and valid generated images are captured**

*October 13, 2025 - Previous (v2)*
- **FIXED: Image URL Accessibility & Free Tier Optimization** - Complete URL and resource management overhaul
  - **Enhanced URL Construction**: 
    - Fixed `get_base_url()` to prioritize `REPLIT_DEV_DOMAIN` over `REPLIT_DOMAINS`
    - Handles multiple domain strings safely (comma/space separated)
    - Returns correct HTTPS URLs for all deployment platforms
  - **Image Endpoint Improvements**:
    - Added CORS headers (`Access-Control-Allow-Origin: *`)
    - Strong caching (`Cache-Control: public, max-age=31536000`)
    - Cross-origin resource policy for universal access
    - Better error handling with specific FileNotFoundError catch
  - **Aggressive Free Tier Optimization**:
    - Lowered memory threshold to 300MB (from 400MB) for 512MB free tier
    - Browser refresh every 20 requests (from 50) to prevent memory buildup
    - Context refresh every 90 seconds (from 120s) for tighter resource management
    - Added automatic image cleanup: deletes files older than 24 hours
    - Randomized cleanup invocation (10% chance per image download) to balance disk space
  - **Result: Images now return valid, accessible URLs that work from any client, and server runs efficiently on free tier for extended periods**

*October 13, 2025 - Previous*
- **FIXED: Railway/Render Deployment Issues** - Enhanced for production environments
  - Increased viewport size to 1280x1024 (from 1024x600) for better image visibility
  - Extended wait times: 3s initial wait + 3x 1.5s scrolls for lazy-loaded images
  - Multiple scroll attempts to ensure all images load in headless environments
  - Debug logging shows which images were found/filtered for troubleshooting
  - **Result: Images now generate reliably on Railway/Render free tiers**
- **FIXED: Image URLs Now Accessible** - Images saved locally and served via API
  - Downloads images using authenticated browser session (bypasses Google's access restrictions)
  - Saves images to `static/images/` directory with unique filenames
  - Returns full URLs (e.g., `https://your-domain.com/images/abc123_xyz.jpg`)
  - New `/images/<filename>` endpoint serves the downloaded images
  - Automatic MIME type detection and file extension (JPEG, PNG, WebP, GIF)
  - **Result: Clean, accessible image URLs that work from any client**
- **FIXED: SVG Logo/UI Element Filtering** - Improved exclusion logic
  - Blocks SVG files, logos, icons, sparkle images
  - Excludes gstatic.com UI assets and profile pictures
  - Only returns actual generated images from Gemini

## User Preferences
- No credit card, prefers free solutions
- Wants to scrape Gemini web interface
- Needs cookie-based authentication
- Requires three specific endpoints for different Gemini features

## System Architecture

### UI/UX Decisions
- JSON response format for all API endpoints.

### Technical Implementations
- **Core Functionality**: Flask API server running on port 5000, handling three main endpoints: `/gemini` (chat), `/gimage` (image generation), and `/gedit` (image editing).
- **Authentication**: Cookie-based authentication by reading cookies from `account.txt` and injecting them into Playwright's browser context.
- **Browser Automation**: Utilizes Playwright with Chromium in a headless mode for web scraping.
- **Image Handling**: Supports all image formats (PNG, JPG, WebP, GIF) with automatic format detection. Images are downloaded using authenticated browser sessions, saved to local storage (`static/images/`), and served via the `/images/<filename>` endpoint. This eliminates 403 errors and provides clean, accessible URLs.
- **Memory Management**: Aggressive optimizations for resource-constrained environments (e.g., Render's 512MB free tier), including:
    - Browser refresh every 20 requests per thread and automatic cleanup when memory exceeds 300MB.
    - Context refresh every 90 seconds to prevent memory buildup.
    - `gc.collect(2)` on high memory events, `gc.collect(1)` per request.
    - Automatic image cleanup: deletes files older than 24 hours (10% randomized invocation).
    - Ensured temp file cleanup in `/gedit` endpoint.
    - Thread-local browser instances with automatic refresh and cookie caching.
    - Optimized Gunicorn configuration for minimal resource usage.
- **Performance**: Significant optimizations in wait times, polling, and input methods for faster API responses.
- **Error Handling**: Basic error handling and logging (set to ERROR level for minimal I/O).

### Feature Specifications
- **Chat Endpoint (`/gemini?chat=`)**: Interacts with Gemini for text-based conversations.
- **Image Generation Endpoint (`/gimage?prompt=`)**: Generates images based on a given prompt. Returns full URLs to locally-stored images.
- **Image Editing Endpoint (`/gedit?imgUrl=&prompt=`)**: Edits an existing image using a prompt. Returns full URLs to locally-stored edited images.
- **Image Serving Endpoint (`/images/<filename>`)**: Serves downloaded images from the static directory with CORS, caching, and cross-origin headers for universal access.
- **Health Check Endpoint (`/health`)**: Provides memory usage, thread count, and cookie status.

### System Design Choices
- **Technology Stack**: Python 3.11, Flask, Playwright, Requests, python-dotenv.
- **Project Structure**: Centralized `app.py` for all API logic, `account.txt` for cookies, `requirements.txt` for dependencies, and `Dockerfile`/`render.yaml` for deployment.
- **Deployment**: Designed for Docker-based deployment on platforms like Render, Railway, or Fly.io. NOT compatible with serverless platforms (Netlify, Vercel) due to browser automation requirements.

## External Dependencies

### Python Packages
- `flask`: Web framework for building the API.
- `playwright`: For browser automation and web scraping.
- `requests`: HTTP client for making external requests.
- `python-dotenv`: For managing environment variables.
- `gunicorn`: WSGI HTTP Server for UNIX, used in production deployments.
- `psutil`: For system and process utility monitoring (specifically memory).

### System Requirements
- Playwright Chromium browser (installed automatically via Playwright).
- Python 3.11 runtime environment.
- Docker (for deployment to platforms like Render).

## Deployment Options

### ✅ Compatible Platforms
These platforms support Docker and browser automation:

1. **Render** (Recommended - Free tier available)
   - Full Docker support with 512MB RAM
   - Browser automation compatible
   - Easy deployment via render.yaml

2. **Railway.app**
   - Similar to Render, supports Docker
   - Good for Playwright applications

3. **Fly.io**
   - Global edge deployment with Docker
   - Supports browser automation

4. **Google Cloud Run**
   - Serverless containers with browser support
   - Scalable but more complex

5. **Heroku** (Paid plans only)
   - Supports Playwright with buildpacks

### ❌ Incompatible Platforms
These platforms **will NOT work** with this API:

- **Netlify** - Static site hosting, no browser automation support
- **Vercel** - Serverless functions only, 10s timeout limit
- **AWS Lambda** - Requires custom layers for browser, complex setup
- **Cloudflare Workers** - No browser support

**Why Netlify won't work:**
- Designed for static sites and lightweight serverless functions
- No support for Playwright browser automation (200-300MB Chromium)
- 10-second function timeout (browser automation needs longer)
- No persistent processes or system dependencies