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
    # Check for Replit domain first
    replit_domain = os.environ.get('REPLIT_DOMAINS')
    if replit_domain:
        return f"https://{replit_domain}"
    
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
    
    # Check memory usage and force cleanup if approaching limit (400MB threshold for 512MB tier)
    try:
        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024
        if memory_mb > 400:
            logger.warning(f"High memory usage detected: {memory_mb:.1f}MB, forcing browser cleanup")
            close_browser()
            import gc
            gc.collect(2)  # Full garbage collection on high memory
    except:
        pass
    
    # Track request count and force refresh every 50 requests to prevent memory buildup
    _thread_local.request_count = getattr(_thread_local, 'request_count', 0) + 1
    if _thread_local.request_count >= 50:
        logger.warning(f"Thread hit 50 requests, forcing browser refresh")
        close_browser()
        _thread_local.request_count = 0
    
    # Check if we need to refresh cookies (cache expired)
    needs_refresh = False
    if _thread_local.context is not None and _thread_local.context_created_at is not None:
        # If context is older than 2 minutes, refresh it (aggressive for memory)
        if (datetime.now() - _thread_local.context_created_at).total_seconds() >= 120:
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
            viewport={'width': 1024, 'height': 600},
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

def download_image_authenticated(page, url):
    """Download image using authenticated browser context and save to static directory"""
    try:
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
                    page.screenshot(path='/tmp/gimage_error.png')
                    raise Exception("Could not find input field. Screenshot saved to /tmp/gimage_error.png")
                
                input_element.click()
                input_element.type(f"Generate an image: {prompt}", delay=0)
                
                send_button = page.locator('button[aria-label*="Send"], button:has-text("Send")').first
                if send_button.count() > 0:
                    send_button.click()
                else:
                    page.keyboard.press('Enter')
                
                wait_for_response_complete(page)
                
                # Scroll down to ensure all images are loaded (especially important for small viewports on Render)
                try:
                    page.evaluate("""() => {
                        window.scrollTo(0, document.body.scrollHeight);
                    }""")
                    page.wait_for_timeout(2000)  # Wait 2 seconds for lazy-loaded images
                    page.evaluate("""() => {
                        window.scrollTo(0, 0);
                    }""")
                    page.wait_for_timeout(1000)  # Wait after scrolling back up
                except:
                    pass
                
                image_selectors = [
                    'img[src*="googleusercontent.com"]',
                    'img[src*="gemini"]',
                    'img[src*="http"]'
                ]
                
                google_image_urls = []
                all_found_images = []
                for selector in image_selectors:
                    images = page.locator(selector).all()
                    if images:
                        # Get ALL images, not just the last one
                        for img in images:
                            src = img.get_attribute('src')
                            if src and ('http' in src):
                                all_found_images.append(src)
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
                                
                                # Accept if from googleusercontent and not excluded
                                if 'googleusercontent.com' in src and not is_excluded:
                                    # Avoid duplicates
                                    if src not in google_image_urls:
                                        google_image_urls.append(src)
                        if google_image_urls:
                            break
                
                # Debug logging
                if not google_image_urls and all_found_images:
                    logger.error(f"Found {len(all_found_images)} images but all were filtered. First image: {all_found_images[0][:100]}")
                
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
                    debug_info = f"No images found after filtering. Total images seen: {len(all_found_images)}"
                    if all_found_images:
                        debug_info += f". First URL: {all_found_images[0][:80]}"
                    logger.error(debug_info)
                    
                    return jsonify({
                        'status': 'error',
                        'message': 'No images generated. Try a more specific prompt like "Generate an image of a sunset" or check if Gemini supports image generation in your region.',
                        'debug': debug_info if all_found_images else 'No images found on page'
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
                
                # Scroll down to ensure all images are loaded (especially important for small viewports on Render)
                try:
                    page.evaluate("""() => {
                        window.scrollTo(0, document.body.scrollHeight);
                    }""")
                    page.wait_for_timeout(2000)  # Wait 2 seconds for lazy-loaded images
                    page.evaluate("""() => {
                        window.scrollTo(0, 0);
                    }""")
                    page.wait_for_timeout(1000)  # Wait after scrolling back up
                except:
                    pass
                
                image_selectors = [
                    'img[src*="googleusercontent.com"]',
                    'img[src*="gemini"]',
                    'img[src*="http"]'
                ]
                
                google_result_urls = []
                for selector in image_selectors:
                    images = page.locator(selector).all()
                    if images:
                        # Get ALL images, not just the last one
                        for img in images:
                            src = img.get_attribute('src')
                            if src and ('http' in src):
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
                                
                                # Accept if from googleusercontent and not excluded
                                if 'googleusercontent.com' in src and not is_excluded:
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
                    return jsonify({
                        'status': 'error',
                        'message': 'No edited images or response received from Gemini'
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
    """Serve images from static/images directory"""
    try:
        images_dir = os.path.join('static', 'images')
        return send_from_directory(images_dir, filename)
    except Exception as e:
        logger.error(f"Failed to serve image {filename}: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': 'Image not found'
        }), 404

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
