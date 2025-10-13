from flask import Flask, request, jsonify, send_from_directory
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import os
import requests
import tempfile
import logging
import json
import threading
import psutil
from datetime import datetime
from contextlib import contextmanager
import uuid
import hashlib

os.environ['PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS'] = 'true'

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET', 'dev-secret-key')

logging.basicConfig(
    level=logging.ERROR,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

# Global cookie cache for performance
_cached_cookies = None
_cookies_last_loaded = None
_cookie_lock = threading.RLock()

# Thread-local storage for browser instances (Playwright is thread-bound)
_thread_local = threading.local()

def get_base_url():
    """Get the proper base URL for constructing image URLs"""
    # Check for Replit domain first (prioritize REPLIT_DEV_DOMAIN over REPLIT_DOMAINS)
    replit_domain = os.environ.get('REPLIT_DEV_DOMAIN') or os.environ.get('REPLIT_DOMAINS')
    if replit_domain:
        # Handle multiple domains (comma or space separated) - take the first one
        domain = replit_domain.split(',')[0].split()[0].strip()
        if domain:
            return f"https://{domain}"
    
    # For Render and other platforms, use the request URL with proper scheme
    # Check X-Forwarded-Proto header first (set by most reverse proxies)
    scheme = request.headers.get('X-Forwarded-Proto')
    if not scheme:
        # Fall back to request.scheme which handles most cases correctly
        scheme = request.scheme
    
    # Get the host, checking X-Forwarded-Host for proxied requests
    host = request.headers.get('X-Forwarded-Host', request.host)
    
    # Always use https in production if not explicitly http
    if scheme == 'http' and 'localhost' not in host and '127.0.0.1' not in host:
        scheme = 'https'
    
    return f"{scheme}://{host}"

def load_cookies():
    """Load cookies from account.txt file or ACCOUNT_COOKIES env var - supports JSON, Netscape, and name=value formats"""
    global _cached_cookies, _cookies_last_loaded
    
    # Return cached cookies if loaded recently (within 5 minutes)
    if _cached_cookies is not None and _cookies_last_loaded is not None:
        if (datetime.now() - _cookies_last_loaded).total_seconds() < 300:
            return _cached_cookies
    
    cookies = []
    
    # Try to get cookies from environment variable first (for Vercel deployment)
    content = os.environ.get('ACCOUNT_COOKIES')
    
    # If not in env var, try to read from file
    if not content:
        try:
            with open('account.txt', 'r') as f:
                content = f.read()
        except FileNotFoundError:
            logger.error("account.txt file not found and ACCOUNT_COOKIES env var not set")
            return []
        except Exception as e:
            logger.error(f"Error loading cookies from file: {str(e)}")
            return []
    
    if content:
        try:
            try:
                json_data = json.loads(content)
                if isinstance(json_data, list):
                    for cookie in json_data:
                        if isinstance(cookie, dict) and 'name' in cookie and 'value' in cookie:
                            processed_cookie = {
                                'name': str(cookie['name']),
                                'value': str(cookie['value']),
                                'domain': str(cookie.get('domain', '.google.com')),
                                'path': str(cookie.get('path', '/'))
                            }
                            
                            if 'secure' in cookie and cookie['secure']:
                                processed_cookie['secure'] = cookie['secure']
                            
                            if 'expires' in cookie and cookie['expires']:
                                processed_cookie['expires'] = float(cookie['expires'])  # type: ignore
                            elif 'expirationDate' in cookie and cookie['expirationDate']:
                                processed_cookie['expires'] = float(cookie['expirationDate'])  # type: ignore
                            
                            if 'httpOnly' in cookie and cookie['httpOnly']:
                                processed_cookie['httpOnly'] = cookie['httpOnly']
                            
                            if 'sameSite' in cookie and cookie['sameSite']:
                                same_site = str(cookie['sameSite']).lower()
                                if same_site == 'no_restriction':
                                    processed_cookie['sameSite'] = 'None'
                                elif same_site == 'lax':
                                    processed_cookie['sameSite'] = 'Lax'
                                elif same_site == 'strict':
                                    processed_cookie['sameSite'] = 'Strict'
                                elif same_site in ['none', 'lax', 'strict']:
                                    processed_cookie['sameSite'] = same_site.capitalize()
                            
                            cookies.append(processed_cookie)
                    # Cache cookies for future requests
                    _cached_cookies = cookies
                    _cookies_last_loaded = datetime.now()
                    return cookies
            except json.JSONDecodeError:
                pass
            
            lines = content.split('\n')
            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                if '\t' in line:
                    parts = line.split('\t')
                    if len(parts) >= 7:
                        domain = parts[0]
                        path = parts[2]
                        secure = parts[3].upper() == 'TRUE'
                        expiry = parts[4]
                        name = parts[5]
                        value = parts[6]
                        
                        cookie = {
                            'name': name,
                            'value': value,
                            'domain': domain if domain.startswith('.') else f'.{domain}',
                            'path': path
                        }
                        
                        if secure:
                            cookie['secure'] = secure  # type: ignore
                        
                        if expiry and expiry != '0':
                            try:
                                cookie['expires'] = float(expiry)  # type: ignore
                            except:
                                pass
                        
                        cookies.append(cookie)
                    continue
                
                if '=' in line:
                    name, value = line.split('=', 1)
                    cookies.append({
                        'name': name.strip(),
                        'value': value.strip(),
                        'domain': '.google.com',
                        'path': '/'
                    })
            # Cache cookies for future requests
            _cached_cookies = cookies
            _cookies_last_loaded = datetime.now()
            return cookies
        except Exception as e:
            logger.error(f"Error parsing cookies: {str(e)}")
            return []
    
    # Cache cookies for future requests
    _cached_cookies = cookies
    _cookies_last_loaded = datetime.now()
    return cookies

def get_or_create_browser():
    """Get or create thread-local browser instance"""
    global _cookies_last_loaded
    
    # Check if this thread has browser instances
    if not hasattr(_thread_local, 'playwright'):
        _thread_local.playwright = None
        _thread_local.browser = None
        _thread_local.context = None
        _thread_local.context_created_at = None
        _thread_local.request_count = 0
    
    # Check memory usage and force cleanup if approaching limit (300MB threshold for 512MB free tier)
    try:
        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024
        if memory_mb > 300:
            logger.warning(f"High memory usage detected: {memory_mb:.1f}MB, forcing browser cleanup")
            close_browser()
            import gc
            gc.collect(2)  # Full garbage collection on high memory
    except:
        pass
    
    # Track request count and force refresh every 20 requests to prevent memory buildup (optimized for free tier)
    _thread_local.request_count = getattr(_thread_local, 'request_count', 0) + 1
    if _thread_local.request_count >= 20:
        logger.warning(f"Thread hit 20 requests, forcing browser refresh for memory optimization")
        close_browser()
        _thread_local.request_count = 0
    
    # Check if we need to refresh cookies (cache expired)
    needs_refresh = False
    if _thread_local.context is not None and _thread_local.context_created_at is not None:
        # If context is older than 90 seconds, refresh it (aggressive for free tier memory optimization)
        if (datetime.now() - _thread_local.context_created_at).total_seconds() >= 90:
            logger.warning("Thread-local context expired, recreating with fresh cookies")
            needs_refresh = True
    
    # Refresh context if needed
    if needs_refresh and _thread_local.context is not None:
        try:
            _thread_local.context.close()
        except:
            pass
        _thread_local.context = None
    
    # Create browser if needed
    if _thread_local.browser is None or _thread_local.playwright is None:
        if _thread_local.playwright is None:
            _thread_local.playwright = sync_playwright().start()
        if _thread_local.browser is None:
            _thread_local.browser = _thread_local.playwright.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-gpu',
                    '--disable-software-rasterizer',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                    '--disable-extensions',
                    '--disable-background-networking',
                    '--disable-sync',
                    '--disable-translate',
                    '--disable-default-apps',
                    '--no-first-run',
                    '--disable-setuid-sandbox',
                    '--disable-web-security',
                    '--disable-features=IsolateOrigins,site-per-process',
                    '--disable-site-isolation-trials',
                    '--js-flags=--max-old-space-size=128',
                    '--disable-accelerated-2d-canvas',
                    '--no-zygote',
                    '--disable-background-timer-throttling',
                    '--disable-backgrounding-occluded-windows',
                    '--disable-breakpad',
                    '--disable-component-extensions-with-background-pages',
                    '--disable-features=TranslateUI,BlinkGenPropertyTrees',
                    '--disable-ipc-flooding-protection',
                    '--disable-renderer-backgrounding',
                    '--disable-hang-monitor',
                    '--disable-prompt-on-repost',
                    '--metrics-recording-only',
                    '--no-default-browser-check',
                    '--disable-domain-reliability',
                    '--disable-component-update',
                    '--disk-cache-size=1'
                ]
            )
    
    # Create new context with fresh cookies if needed
    if _thread_local.context is None:
        _thread_local.context = _thread_local.browser.new_context(
            viewport={'width': 1280, 'height': 1024},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            java_script_enabled=True,
            bypass_csp=True,
            ignore_https_errors=True
        )
        _thread_local.context_created_at = datetime.now()
        
        cookies = load_cookies()
        if cookies:
            try:
                _thread_local.context.add_cookies(cookies)
            except Exception as e:
                logger.error(f"Error adding cookies: {str(e)}")
    
    return _thread_local.browser, _thread_local.context

def close_browser():
    """Close thread-local browser instance"""
    if hasattr(_thread_local, 'context') and _thread_local.context:
        try:
            _thread_local.context.close()
        except:
            pass
        _thread_local.context = None
    
    if hasattr(_thread_local, 'browser') and _thread_local.browser:
        try:
            _thread_local.browser.close()
        except:
            pass
        _thread_local.browser = None
    
    if hasattr(_thread_local, 'playwright') and _thread_local.playwright:
        try:
            _thread_local.playwright.stop()
        except:
            pass
        _thread_local.playwright = None
    
    # Reset request count
    _thread_local.request_count = 0
    
    logger.info("Thread-local browser instance closed")

@contextmanager
def get_browser_page():
    """Context manager to get a browser page from thread-local browser"""
    browser, context = get_or_create_browser()
    page = context.new_page()
    page.set_default_timeout(30000)
    
    try:
        yield page
    finally:
        try:
            page.close()
        except:
            pass
        # Aggressive cleanup for 512MB RAM limit
        try:
            import gc
            gc.collect(1)  # Light garbage collection (changed from 2 to 1 for faster execution)
        except:
            pass

def check_login_required(page):
    """Check if login is required - improved detection"""
    try:
        # First, check if we can find the chat input (indicates logged in)
        input_selectors = [
            'rich-textarea[placeholder]',
            'div[contenteditable="true"][role="textbox"]'
        ]
        for selector in input_selectors:
            if page.locator(selector).count() > 0:
                return False  # Input found = logged in
        
        # If no input, check if we're on the landing page (indicates not logged in)
        if page.locator('button:has-text("Chat with Gemini")').count() > 0:
            return True  # Landing page = need to login
        
        # Fallback: check for explicit sign-in buttons
        if page.locator('button:has-text("Sign in"), a:has-text("Sign in")').count() > 0:
            return True
        
        return False
    except:
        return True  # Default to login required if error

def wait_for_response_complete(page):
    """Wait for Gemini to complete its response"""
    try:
        page.wait_for_function("""() => {
            const buttons = Array.from(document.querySelectorAll('button'));
            const stopButton = buttons.find(btn => 
                btn.getAttribute('aria-label')?.toLowerCase().includes('stop') ||
                btn.textContent?.toLowerCase().includes('stop')
            );
            return !stopButton || stopButton.disabled || !stopButton.offsetParent;
        }""", timeout=45000, polling=100)
        return True
    except PlaywrightTimeout:
        logger.warning("Timeout waiting for response completion")
        return False

def download_image(url):
    """Download image from URL and save to temp file"""
    try:
        response = requests.get(url, timeout=12)
        response.raise_for_status()
        
        ext = url.split('.')[-1].split('?')[0] or 'jpg'
        if ext not in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
            ext = 'jpg'
        
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}')
        temp_file.write(response.content)
        temp_file.close()
        return temp_file.name
    except Exception as e:
        logger.error(f"Failed to download image: {str(e)}")
        return None

def cleanup_old_images():
    """Delete images older than 24 hours to save disk space on free tier"""
    try:
        images_dir = os.path.join('static', 'images')
        if not os.path.exists(images_dir):
            return
        
        now = datetime.now().timestamp()
        deleted_count = 0
        
        for filename in os.listdir(images_dir):
            filepath = os.path.join(images_dir, filename)
            if os.path.isfile(filepath):
                # Check file age
                file_age = now - os.path.getmtime(filepath)
                if file_age > 86400:  # 24 hours in seconds
                    try:
                        os.unlink(filepath)
                        deleted_count += 1
                    except:
                        pass
        
        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} old images")
    except Exception as e:
        logger.error(f"Error cleaning up old images: {str(e)}")

def download_image_authenticated(page, url):
    """Download image using authenticated browser context and save to static directory"""
    try:
        # Periodically clean up old images (10% chance each call to avoid overhead)
        import random
        if random.random() < 0.1:
            cleanup_old_images()
        
        # Use the authenticated page context to download the image
        response = page.request.get(url)
        if response.status == 200:
            image_data = response.body()
            
            # Detect image format
            content_type = response.headers.get('content-type', 'image/jpeg')
            if 'png' in content_type:
                ext = 'png'
            elif 'webp' in content_type:
                ext = 'webp'
            elif 'gif' in content_type:
                ext = 'gif'
            else:
                ext = 'jpg'
            
            # Create static/images directory if it doesn't exist
            images_dir = os.path.join('static', 'images')
            os.makedirs(images_dir, exist_ok=True)
            
            # Generate unique filename using hash of URL and timestamp
            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            unique_id = str(uuid.uuid4())[:8]
            filename = f"{url_hash}_{unique_id}.{ext}"
            filepath = os.path.join(images_dir, filename)
            
            # Save image to disk
            with open(filepath, 'wb') as f:
                f.write(image_data)
            
            # Return relative URL path
            return f"/images/{filename}"
        return None
    except Exception as e:
        logger.error(f"Failed to download authenticated image: {str(e)}")
        return None

@app.route('/gemini', methods=['GET'])
def gemini_chat():
    """Chat with Gemini endpoint"""
    chat_message = request.args.get('chat', '')
    
    if not chat_message:
        return jsonify({
            'status': 'error',
            'message': 'Missing chat parameter. Use /gemini?chat=your_message'
        }), 400
    
    try:
        with get_browser_page() as page:
            page.goto('https://gemini.google.com/app', timeout=30000, wait_until='domcontentloaded')
            try:
                page.wait_for_load_state('networkidle', timeout=5000)
            except PlaywrightTimeout:
                pass
            
            if check_login_required(page):
                return jsonify({
                    'status': 'error',
                    'message': 'Authentication failed. Cookies may be invalid or expired.',
                    'hint': 'Please update your cookies in account.txt'
                }), 401
            
            try:
                input_selectors = [
                    'rich-textarea[placeholder]',
                    'div[contenteditable="true"][role="textbox"]',
                    'textarea[placeholder*="prompt"]',
                    'div.ql-editor[contenteditable="true"]',
                    'textarea',
                    'div[contenteditable="true"]'
                ]
                
                input_element = None
                for selector in input_selectors:
                    try:
                        element = page.wait_for_selector(selector, timeout=8000, state='visible')
                        if element:
                            input_element = element
                            break
                    except PlaywrightTimeout:
                        continue
                
                if not input_element:
                    page.screenshot(path='/tmp/gemini_chat_error.png')
                    logger.error("Could not find input field. Screenshot saved.")
                    raise Exception("Could not find input field. Page may have changed.")
                
                input_element.click()
                input_element.type(chat_message, delay=0)
                
                # Try multiple send strategies
                send_success = False
                try:
                    # Strategy 1: Look for send button
                    send_button = page.locator('button[aria-label*="Send"]').first
                    if send_button.count() > 0:
                        send_button.click()
                        send_success = True
                except:
                    pass
                
                if not send_success:
                    try:
                        # Strategy 2: Press Enter
                        page.keyboard.press('Enter')
                        send_success = True
                    except:
                        pass
                
                if not send_success:
                    raise Exception("Could not send message")
                
                wait_for_response_complete(page)
                
                # Extract response with multiple selector strategies
                response_selectors = [
                    'div[data-message-author-role="model"]',
                    'message-content[model-turn]',
                    'model-response',
                    'div.model-response-text',
                    'div[class*="model"] div[class*="content"]',
                    'div[jsname] div[class*="markdown"]'
                ]
                
                response_text = None
                for selector in response_selectors:
                    try:
                        elements = page.locator(selector).all()
                        if elements:
                            text = elements[-1].inner_text()
                            if text and len(text.strip()) > 0:
                                response_text = text
                                break
                    except:
                        continue
                
                # Fallback: try to get any new text that appeared
                if not response_text:
                    try:
                        # Try to get the last message-like element
                        all_text_elements = page.locator('div[class*="content"], div[class*="message"], div[jsname]').all()
                        for elem in reversed(all_text_elements):
                            text = elem.inner_text()
                            if text and len(text.strip()) > 20 and text.strip() != chat_message:
                                response_text = text
                                break
                    except:
                        pass
                
                if response_text and len(response_text.strip()) > 0:
                    return jsonify({
                        'status': 'success',
                        'chat': chat_message,
                        'response': response_text.strip()
                    })
                else:
                    # Save debug screenshot
                    page.screenshot(path='/tmp/gemini_no_response.png')
                    logger.error("Could not extract response. Screenshot saved to /tmp/gemini_no_response.png")
                    return jsonify({
                        'status': 'error',
                        'message': 'No valid response received from Gemini',
                        'debug': 'Screenshot saved to /tmp/gemini_no_response.png'
                    }), 500
                
            except Exception as e:
                logger.error(f"Scraping error: {str(e)}")
                return jsonify({
                    'status': 'error',
                    'message': f'Failed to interact with Gemini: {str(e)}',
                    'hint': 'Gemini UI may have changed. Check logs for details.'
                }), 500
                
    except Exception as e:
        logger.error(f"Browser error: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Browser automation failed: {str(e)}'
        }), 500

@app.route('/gimage', methods=['GET'])
def generate_image():
    """Generate image with Gemini endpoint"""
    prompt = request.args.get('prompt', '')
    
    if not prompt:
        return jsonify({
            'status': 'error',
            'message': 'Missing prompt parameter. Use /gimage?prompt=your_image_description'
        }), 400
    
    try:
        with get_browser_page() as page:
            # Start a new chat to ensure we get the latest model (2.5 Flash)
            page.goto('https://gemini.google.com/app', timeout=30000, wait_until='domcontentloaded')
            try:
                page.wait_for_load_state('networkidle', timeout=5000)
            except PlaywrightTimeout:
                pass
            
            if check_login_required(page):
                return jsonify({
                    'status': 'error',
                    'message': 'Authentication failed. Cookies may be invalid or expired.',
                    'hint': 'Please update your cookies in account.txt'
                }), 401
            
            # Click on "New chat" to ensure we're using the latest/default model
            try:
                page.wait_for_timeout(1500)
                new_chat_button = page.locator('button[aria-label*="New chat"], button:has-text("New chat")').first
                if new_chat_button.count() > 0:
                    new_chat_button.click()
                    page.wait_for_timeout(1500)
            except Exception as e:
                logger.warning(f"Could not click new chat: {str(e)}")
            
            # Take screenshot for debugging
            page.screenshot(path='/tmp/before_prompt.png')
            
            # Check what model is being used
            model_text = page.locator('button:has-text("Flash"), div:has-text("Flash"), button:has-text("Gemini"), div:has-text("Gemini")').first
            if model_text.count() > 0:
                current_model = model_text.text_content()
                logger.error(f"Current model detected: {current_model}")
            
            try:
                input_selectors = [
                    'rich-textarea[placeholder]',
                    'div[contenteditable="true"][role="textbox"]',
                    'textarea[placeholder*="prompt"]',
                    'div.ql-editor[contenteditable="true"]'
                ]
                
                input_element = None
                for selector in input_selectors:
                    try:
                        element = page.wait_for_selector(selector, timeout=8000, state='visible')
                        if element:
                            input_element = element
                            # Log the placeholder text to see what mode we're in
                            placeholder = element.get_attribute('placeholder')
                            logger.error(f"Input placeholder: {placeholder}")
                            break
                    except PlaywrightTimeout:
                        continue
                
                if not input_element:
                    page.screenshot(path='/tmp/gimage_error.png')
                    raise Exception("Could not find input field. Screenshot saved to /tmp/gimage_error.png")
                
                input_element.click()
                
                # Ensure prompt explicitly triggers image generation
                # Check if prompt starts with image generation keywords (word boundary check)
                import re
                image_prefixes = [
                    r'^generate\s+',
                    r'^create\s+',
                    r'^make\s+an?\s+image',
                    r'^draw\s+',
                    r'^show\s+me\s+',
                    r'^(an?\s+)?image\s+of',
                    r'^(a\s+)?picture\s+of',
                    r'^(a\s+)?photo\s+of',
                    r'^paint\s+',
                    r'^design\s+',
                    r'^illustrate\s+'
                ]
                
                has_image_prefix = any(re.match(pattern, prompt.lower()) for pattern in image_prefixes)
                
                if not has_image_prefix:
                    enhanced_prompt = f"Generate an image of {prompt}"
                    logger.error(f"Enhanced prompt from '{prompt}' to '{enhanced_prompt}'")
                    prompt = enhanced_prompt
                
                input_element.type(prompt, delay=0)
                
                send_button = page.locator('button[aria-label*="Send"], button:has-text("Send")').first
                if send_button.count() > 0:
                    send_button.click()
                else:
                    page.keyboard.press('Enter')
                
                wait_for_response_complete(page)
                
                # Optimized wait for images to render (reduced from 3000ms to 1500ms)
                page.wait_for_timeout(1500)
                
                # Scroll down to ensure all images are loaded (optimized for speed)
                try:
                    # Optimized scroll attempts - reduced waits for faster response
                    for _ in range(2):
                        page.evaluate("""() => {
                            window.scrollTo(0, document.body.scrollHeight);
                        }""")
                        page.wait_for_timeout(800)
                    
                    # Scroll back to top
                    page.evaluate("""() => {
                        window.scrollTo(0, 0);
                    }""")
                    page.wait_for_timeout(1000)
                except:
                    pass
                
                # Look for images specifically in the model response area
                response_area_selectors = [
                    'div[data-message-author-role="model"] img',
                    'model-response img',
                    'message-content img'
                ]
                
                google_image_urls = []
                all_found_images = []
                all_image_details = []
                
                # First try to find images in response areas only
                for selector in response_area_selectors:
                    images = page.locator(selector).all()
                    if images:
                        for img in images:
                            src = img.get_attribute('src')
                            if src and 'http' in src:
                                # Get image dimensions to help distinguish generated images from profile pics
                                try:
                                    width = img.evaluate('el => el.naturalWidth || el.width')
                                    height = img.evaluate('el => el.naturalHeight || el.height')
                                    alt_text = img.get_attribute('alt') or ''
                                except:
                                    width = 0
                                    height = 0
                                    alt_text = ''
                                
                                all_found_images.append(src)
                                all_image_details.append({
                                    'src': src,
                                    'width': width,
                                    'height': height,
                                    'alt': alt_text
                                })
                                
                                # Exclude UI elements and profile pictures
                                is_ui_element = (
                                    src.endswith('.svg') or
                                    '/logo' in src.lower() or '/icon' in src.lower() or
                                    'sparkle' in src.lower() or 'gemini_sparkle' in src or
                                    '/lamda/images/' in src or
                                    'gstatic.com/lamda' in src or
                                    '/a/ACg8oc' in src or '/a/default' in src or
                                    '/photo.jpg' in src or '/avatar' in src.lower() or
                                    's64-c' in src or 's32-c' in src or
                                    's96-c' in src or 's128-c' in src or
                                    's256-c' in src or 's512-c' in src
                                )
                                
                                # Also exclude very small images (likely icons/profile pics)
                                is_too_small = width > 0 and height > 0 and (width < 100 or height < 100)
                                
                                if 'googleusercontent.com' in src and not is_ui_element and not is_too_small:
                                    if src not in google_image_urls:
                                        google_image_urls.append(src)
                        if google_image_urls:
                            break
                
                # Fallback: if no images in response area, search globally with profile picture exclusions
                if not google_image_urls:
                    all_images = page.locator('img[src*="googleusercontent.com"]').all()
                    for img in all_images:
                        src = img.get_attribute('src')
                        if src and 'http' in src:
                            # Get dimensions for fallback images too
                            try:
                                width = img.evaluate('el => el.naturalWidth || el.width')
                                height = img.evaluate('el => el.naturalHeight || el.height')
                            except:
                                width = 0
                                height = 0
                            
                            all_found_images.append(src)
                            all_image_details.append({
                                'src': src,
                                'width': width,
                                'height': height,
                                'alt': img.get_attribute('alt') or ''
                            })
                            
                            # Exclude known UI elements and profile pictures
                            is_excluded = (
                                src.endswith('.svg') or
                                '/logo' in src.lower() or '/icon' in src.lower() or
                                'sparkle' in src.lower() or 'gemini_sparkle' in src or
                                '/lamda/images/' in src or
                                'gstatic.com/lamda' in src or
                                'gstatic.com' in src or  # Exclude all gstatic
                                '/a/ACg8oc' in src or '/a/default' in src or  # Profile pictures
                                '/photo.jpg' in src or '/avatar' in src.lower() or
                                # Small profile sizes (but allow larger images)
                                (('=s64' in src or '=s32' in src or '=s96' in src or 
                                  's64-c' in src or 's32-c' in src or 's96-c' in src or
                                  's128-c' in src or 's256-c' in src or 's512-c' in src) and
                                 '/a/' in src)  # Only exclude small sizes if it's a profile (/a/)
                            )
                            
                            # Also check dimensions in fallback
                            is_too_small = width > 0 and height > 0 and (width < 100 or height < 100)
                            
                            # Accept Gemini-generated images from googleusercontent
                            if 'googleusercontent.com' in src and not is_excluded and not is_too_small:
                                if src not in google_image_urls:
                                    google_image_urls.append(src)
                
                # Enhanced debug logging with image details
                if not google_image_urls and all_image_details:
                    debug_details = []
                    for detail in all_image_details[:3]:  # Show first 3 images
                        debug_details.append(f"URL: {detail['src'][:60]}... Size: {detail['width']}x{detail['height']}")
                    logger.error(f"Found {len(all_image_details)} images but all were filtered: {'; '.join(debug_details)}")
                
                if google_image_urls:
                    # Download images and save to static directory
                    local_image_urls = []
                    base_url = get_base_url()
                    
                    for img_url in google_image_urls:
                        local_path = download_image_authenticated(page, img_url)
                        if local_path:
                            # Construct full URL
                            full_url = f"{base_url}{local_path}"
                            local_image_urls.append(full_url)
                    
                    if local_image_urls:
                        return jsonify({
                            'status': 'success',
                            'prompt': prompt,
                            'images': local_image_urls
                        })
                    else:
                        return jsonify({
                            'status': 'error',
                            'message': 'Found images but failed to download them'
                        }), 500
                else:
                    # Take screenshot for debugging
                    page.screenshot(path='/tmp/no_images_debug.png')
                    
                    # Build detailed debug info
                    debug_info = f"No valid images found. Total images seen: {len(all_image_details)}"
                    if all_image_details:
                        # Include details about why images were filtered
                        detail = all_image_details[0]
                        debug_info += f". First image: {detail['src'][:60]}... ({detail['width']}x{detail['height']})"
                        
                        # Check if first image is a profile picture
                        if '/a/ACg8oc' in detail['src'] or '/a/default' in detail['src']:
                            debug_info += " [profile picture - filtered out]"
                        elif detail['width'] > 0 and detail['height'] > 0 and (detail['width'] < 100 or detail['height'] < 100):
                            debug_info += " [too small - likely icon/profile]"
                    
                    logger.error(debug_info)
                    
                    # Provide helpful error message
                    error_msg = 'No images generated. '
                    if all_image_details and '/a/' in all_image_details[0]['src']:
                        error_msg += 'Gemini may have shown profile pictures instead of generating images. Try a more explicit prompt like "Create an image showing a beautiful sunset over mountains".'
                    else:
                        error_msg += 'Try a more specific prompt like "Generate an image of a sunset" or check if Gemini supports image generation in your region.'
                    
                    return jsonify({
                        'status': 'error',
                        'message': error_msg,
                        'debug': debug_info if all_image_details else 'No images found on page'
                    }), 500
                    
            except Exception as e:
                logger.error(f"Image generation error: {str(e)}")
                return jsonify({
                    'status': 'error',
                    'message': f'Failed to generate image: {str(e)}'
                }), 500
                
    except Exception as e:
        logger.error(f"Browser error: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Browser automation failed: {str(e)}'
        }), 500

@app.route('/gedit', methods=['GET'])
def edit_image():
    """Edit image with Gemini endpoint"""
    img_url = request.args.get('imgUrl', '')
    prompt = request.args.get('prompt', '')
    
    if not img_url or not prompt:
        return jsonify({
            'status': 'error',
            'message': 'Missing parameters. Use /gedit?imgUrl=image_url&prompt=edit_instructions'
        }), 400
    
    image_file = download_image(img_url)
    if not image_file:
        return jsonify({
            'status': 'error',
            'message': 'Failed to download image from provided URL'
        }), 400
    
    # Ensure temp file is always cleaned up
    try:
        with get_browser_page() as page:
            page.goto('https://gemini.google.com/app', timeout=30000, wait_until='domcontentloaded')
            try:
                page.wait_for_load_state('networkidle', timeout=5000)
            except PlaywrightTimeout:
                pass
            
            if check_login_required(page):
                os.unlink(image_file)
                return jsonify({
                    'status': 'error',
                    'message': 'Authentication failed. Cookies may be invalid or expired.',
                    'hint': 'Please update your cookies in account.txt'
                }), 401
            
            try:
                upload_selectors = [
                    'button[aria-label*="Upload"], button[aria-label*="upload"]',
                    'button:has-text("Upload")',
                    'input[type="file"][accept*="image"]',
                    'button[aria-label*="Add"]'
                ]
                
                file_input = None
                for selector in upload_selectors:
                    try:
                        if 'input[type="file"]' in selector:
                            locator = page.locator(selector).first
                            if locator.count() > 0:
                                file_input = locator
                                break
                        else:
                            button = page.locator(selector).first
                            if button.count() > 0:
                                button.click()
                                try:
                                    page.wait_for_selector('input[type="file"]', timeout=2000, state='attached')
                                    file_input = page.locator('input[type="file"]').first
                                    if file_input.count() > 0:
                                        break
                                except:
                                    pass
                    except:
                        continue
                
                if not file_input or file_input.count() == 0:
                    file_input = page.locator('input[type="file"]').first
                
                if file_input and file_input.count() > 0:
                    file_input.set_input_files(image_file)
                else:
                    raise Exception("Could not find file upload button")
                
                input_selectors = [
                    'rich-textarea[placeholder]',
                    'div[contenteditable="true"][role="textbox"]',
                    'textarea[placeholder*="prompt"]',
                    'div.ql-editor[contenteditable="true"]'
                ]
                
                input_element = None
                for selector in input_selectors:
                    try:
                        element = page.wait_for_selector(selector, timeout=8000, state='visible')
                        if element:
                            input_element = element
                            break
                    except PlaywrightTimeout:
                        continue
                
                if not input_element:
                    page.screenshot(path='/tmp/gedit_error.png')
                    raise Exception("Could not find input field. Screenshot saved to /tmp/gedit_error.png")
                
                input_element.click()
                input_element.type(prompt, delay=0)
                
                send_button = page.locator('button[aria-label*="Send"], button:has-text("Send")').first
                if send_button.count() > 0:
                    send_button.click()
                else:
                    page.keyboard.press('Enter')
                
                wait_for_response_complete(page)
                
                # Optimized wait for images to render (reduced from 3000ms to 1500ms)
                page.wait_for_timeout(1500)
                
                # Scroll down to ensure all images are loaded (optimized for speed)
                try:
                    # Optimized scroll attempts - reduced waits for faster response
                    for _ in range(2):
                        page.evaluate("""() => {
                            window.scrollTo(0, document.body.scrollHeight);
                        }""")
                        page.wait_for_timeout(800)
                    
                    # Scroll back to top
                    page.evaluate("""() => {
                        window.scrollTo(0, 0);
                    }""")
                    page.wait_for_timeout(1000)
                except:
                    pass
                
                image_selectors = [
                    'img[src*="googleusercontent.com"]',
                    'img[src*="gemini"]',
                    'img[src*="http"]'
                ]
                
                google_result_urls = []
                all_edit_image_details = []
                
                for selector in image_selectors:
                    images = page.locator(selector).all()
                    if images:
                        # Get ALL images, not just the last one
                        for img in images:
                            src = img.get_attribute('src')
                            if src and ('http' in src):
                                # Get image dimensions to distinguish generated images from profile pics
                                try:
                                    width = img.evaluate('el => el.naturalWidth || el.width')
                                    height = img.evaluate('el => el.naturalHeight || el.height')
                                except:
                                    width = 0
                                    height = 0
                                
                                all_edit_image_details.append({
                                    'src': src,
                                    'width': width,
                                    'height': height
                                })
                                
                                # Strict exclusions: UI elements that are never generated images
                                is_excluded = (
                                    src.endswith('.svg') or  # SVG files are UI elements
                                    '/logo' in src.lower() or '/icon' in src.lower() or
                                    'sparkle' in src.lower() or 'gemini_sparkle' in src or
                                    '/lamda/images/' in src or  # Gemini UI assets
                                    'gstatic.com/lamda' in src or  # Google static UI assets
                                    '/a/ACg8oc' in src or '/a/default' in src or  # Profile pics
                                    '/photo.jpg' in src or '/avatar' in src.lower() or
                                    's64-c' in src or 's32-c' in src or  # Small profile sizes
                                    's96-c' in src or 's128-c' in src or
                                    's256-c' in src or 's512-c' in src
                                )
                                
                                # Also exclude very small images (likely icons/profile pics)
                                is_too_small = width > 0 and height > 0 and (width < 100 or height < 100)
                                
                                # Accept if from googleusercontent and not excluded
                                if 'googleusercontent.com' in src and not is_excluded and not is_too_small:
                                    # Avoid duplicates
                                    if src not in google_result_urls:
                                        google_result_urls.append(src)
                        if google_result_urls:
                            break
                
                response_selectors = [
                    'div[data-message-author-role="model"]',
                    'div.model-response-text'
                ]
                
                response_text = None
                for selector in response_selectors:
                    elements = page.locator(selector).all()
                    if elements:
                        response_text = elements[-1].inner_text()
                        if response_text and len(response_text.strip()) > 0:
                            break
                
                if google_result_urls or response_text:
                    result = {
                        'status': 'success',
                        'original_image': img_url,
                        'prompt': prompt
                    }
                    if google_result_urls:
                        # Download images and save to static directory
                        local_edited_urls = []
                        base_url = get_base_url()
                        
                        for img_url_edit in google_result_urls:
                            local_path = download_image_authenticated(page, img_url_edit)
                            if local_path:
                                # Construct full URL
                                full_url = f"{base_url}{local_path}"
                                local_edited_urls.append(full_url)
                        
                        if local_edited_urls:
                            result['edited_images'] = local_edited_urls  # type: ignore
                    if response_text:
                        result['response'] = response_text.strip()
                    return jsonify(result)
                else:
                    # Enhanced debug info for image edit failures
                    page.screenshot(path='/tmp/gedit_no_images_debug.png')
                    debug_info = f"No valid edited images found. Total images seen: {len(all_edit_image_details)}"
                    if all_edit_image_details:
                        detail = all_edit_image_details[0]
                        debug_info += f". First image: {detail['src'][:60]}... ({detail['width']}x{detail['height']})"
                        if '/a/' in detail['src']:
                            debug_info += " [profile picture - filtered out]"
                    
                    logger.error(debug_info)
                    
                    return jsonify({
                        'status': 'error',
                        'message': 'No edited images or response received from Gemini',
                        'debug': debug_info if all_edit_image_details else 'No images found'
                    }), 500
                    
            except Exception as e:
                logger.error(f"Image editing error: {str(e)}")
                return jsonify({
                    'status': 'error',
                    'message': f'Failed to edit image: {str(e)}'
                }), 500
                
    except Exception as e:
        logger.error(f"Browser error: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Browser automation failed: {str(e)}'
        }), 500
    finally:
        # Always clean up temp file
        if image_file and os.path.exists(image_file):
            try:
                os.unlink(image_file)
            except:
                pass

@app.route('/images/<filename>')
def serve_image(filename):
    """Serve images from static/images directory with proper headers"""
    try:
        images_dir = os.path.join('static', 'images')
        response = send_from_directory(images_dir, filename)
        # Add headers to ensure images are accessible and cacheable
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Cache-Control'] = 'public, max-age=31536000'
        response.headers['Cross-Origin-Resource-Policy'] = 'cross-origin'
        return response
    except FileNotFoundError:
        logger.error(f"Image not found: {filename}")
        return jsonify({
            'status': 'error',
            'message': 'Image not found'
        }), 404
    except Exception as e:
        logger.error(f"Failed to serve image {filename}: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Error serving image: {str(e)}'
        }), 500

@app.route('/health', methods=['GET'])
def health():
    """Health check with memory monitoring"""
    try:
        process = psutil.Process()
        memory_info = process.memory_info()
        memory_mb = memory_info.rss / 1024 / 1024
        
        return jsonify({
            'status': 'healthy',
            'memory_usage_mb': round(memory_mb, 2),
            'memory_percent': round(process.memory_percent(), 2),
            'thread_count': threading.active_count(),
            'cookies_loaded': _cached_cookies is not None
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/', methods=['GET'])
def home():
    """API documentation"""
    return jsonify({
        'status': 'running',
        'endpoints': {
            '/health': 'Health check with memory monitoring',
            '/gemini': 'Chat with Gemini - GET /gemini?chat=your_message',
            '/gimage': 'Generate images - GET /gimage?prompt=image_description',
            '/gedit': 'Edit images - GET /gedit?imgUrl=image_url&prompt=edit_instructions'
        }
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
