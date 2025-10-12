from flask import Flask, request, jsonify, send_from_directory
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import os
import requests
import tempfile
from pathlib import Path
import logging
import uuid
import json
import threading
import time
from datetime import datetime, timedelta
import atexit
from contextlib import contextmanager

os.environ['PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS'] = 'true'

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET', 'dev-secret-key')

STATIC_IMAGES_DIR = Path('static/images')
STATIC_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.ERROR,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

# Memory monitoring for debugging
def log_memory_usage():
    try:
        import psutil
        import os
        process = psutil.Process(os.getpid())
        mem_mb = process.memory_info().rss / 1024 / 1024
        logger.error(f"Memory usage: {mem_mb:.1f}MB")
    except:
        pass

# Global cookie cache for performance
_cached_cookies = None
_cookies_last_loaded = None
_cookie_lock = threading.RLock()

# Thread-local storage for browser instances (Playwright is thread-bound)
_thread_local = threading.local()

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
                            logger.info(f"Loaded JSON cookie: {cookie['name']}")
                    
                    logger.info(f"Total cookies loaded from JSON: {len(cookies)}")
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
                        logger.info(f"Loaded Netscape cookie: {name}")
                    continue
                
                if '=' in line:
                    name, value = line.split('=', 1)
                    cookies.append({
                        'name': name.strip(),
                        'value': value.strip(),
                        'domain': '.google.com',
                        'path': '/'
                    })
                    logger.info(f"Loaded simple cookie: {name.strip()}")
        
            logger.info(f"Total cookies loaded: {len(cookies)}")
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

def cleanup_old_images(max_age_hours=24):
    """Delete image files older than max_age_hours from static/images directory"""
    try:
        current_time = datetime.now()
        deleted_count = 0
        
        for image_file in STATIC_IMAGES_DIR.glob('*.jpg'):
            file_age = current_time - datetime.fromtimestamp(image_file.stat().st_mtime)
            if file_age > timedelta(hours=max_age_hours):
                try:
                    image_file.unlink()
                    deleted_count += 1
                    logger.info(f"Deleted old image: {image_file.name} (age: {file_age})")
                except Exception as e:
                    logger.error(f"Failed to delete {image_file.name}: {str(e)}")
        
        if deleted_count > 0:
            logger.info(f"Cleanup complete: Deleted {deleted_count} old images")
    except Exception as e:
        logger.error(f"Error during image cleanup: {str(e)}")

def cleanup_background_task(interval_hours=6, max_age_hours=24):
    """Background task to periodically clean up old images"""
    while True:
        try:
            time.sleep(interval_hours * 3600)
            cleanup_old_images(max_age_hours)
        except Exception as e:
            logger.error(f"Background cleanup task error: {str(e)}")

def start_cleanup_thread(interval_hours=1, max_age_hours=3):
    """Start background cleanup thread - very aggressive for 512MB RAM limit"""
    cleanup_thread = threading.Thread(
        target=cleanup_background_task,
        args=(interval_hours, max_age_hours),
        daemon=True
    )
    cleanup_thread.start()
    logger.error(f"Started cleanup thread (every {interval_hours}h, max_age: {max_age_hours}h)")

def get_or_create_browser():
    """Get or create thread-local browser instance"""
    global _cookies_last_loaded
    
    # Check if this thread has browser instances
    if not hasattr(_thread_local, 'playwright'):
        _thread_local.playwright = None
        _thread_local.browser = None
        _thread_local.context = None
        _thread_local.context_created_at = None
    
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
        logger.info(f"Creating thread-local browser instance for thread {threading.current_thread().name}")
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
                    '--disable-renderer-backgrounding'
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
                logger.info(f"Adding {len(cookies)} cookies to thread-local browser context")
                _thread_local.context.add_cookies(cookies)
                logger.info("Cookies successfully added")
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
    
    logger.info("Thread-local browser instance closed")

@contextmanager
def get_browser_page():
    """Context manager to get a browser page from thread-local browser"""
    browser, context = get_or_create_browser()
    page = context.new_page()
    page.set_default_timeout(60000)
    
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
            gc.collect(2)  # Full garbage collection
        except:
            pass

# Register cleanup on exit
atexit.register(close_browser)

def setup_browser_context(playwright):
    """Setup browser with cookies"""
    browser = playwright.chromium.launch(
        headless=True, 
        args=[
            '--disable-blink-features=AutomationControlled',
            '--disable-gpu',
            '--disable-software-rasterizer',
            '--disable-dev-shm-usage',
            '--no-sandbox'
        ]
    )
    context = browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    )
    
    cookies = load_cookies()
    if cookies:
        try:
            logger.info(f"Attempting to add {len(cookies)} cookies to browser context")
            context.add_cookies(cookies)
            logger.info("Cookies successfully added to browser context")
        except Exception as e:
            logger.error(f"Error adding cookies to context: {str(e)}")
            logger.error(f"Cookie that failed: {cookies}")
            raise
    
    return browser, context

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
        }""", timeout=45000, polling=200)
        
        page.wait_for_timeout(300)
        logger.info("Response completed")
        return True
    except PlaywrightTimeout:
        logger.warning("Timeout waiting for response completion")
        return False

def download_image(url):
    """Download image from URL and save to temp file"""
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        ext = url.split('.')[-1].split('?')[0] or 'jpg'
        if ext not in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
            ext = 'jpg'
        
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}')
        temp_file.write(response.content)
        temp_file.close()
        
        logger.info(f"Downloaded image to {temp_file.name}")
        return temp_file.name
    except Exception as e:
        logger.error(f"Failed to download image: {str(e)}")
        return None

def download_authenticated_image(page, url):
    """Download image using authenticated browser context and save to static folder"""
    try:
        logger.info(f"Downloading authenticated image from: {url}")
        
        # Navigate to the image URL with the authenticated context
        response = page.request.get(url)
        
        if response.status != 200:
            logger.error(f"Failed to download image: HTTP {response.status}")
            return None
        
        image_data = response.body()
        
        # Always use .jpg extension
        ext = 'jpg'
        
        # Generate unique filename
        filename = f"{uuid.uuid4()}.{ext}"
        filepath = STATIC_IMAGES_DIR / filename
        
        # Save image to static folder
        with open(filepath, 'wb') as f:
            f.write(image_data)
        
        logger.info(f"Saved authenticated image to {filepath}")
        return filename
    except Exception as e:
        logger.error(f"Failed to download authenticated image: {str(e)}")
        return None

@app.route('/images/<filename>')
def serve_image(filename):
    """Serve downloaded images from static folder"""
    return send_from_directory(STATIC_IMAGES_DIR, filename)

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
            logger.info(f"Navigating to Gemini with message: {chat_message}")
            page.goto('https://gemini.google.com/app', timeout=60000, wait_until='domcontentloaded')
            page.wait_for_timeout(500)
            
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
                            logger.info(f"Found input using selector: {selector}")
                            break
                    except PlaywrightTimeout:
                        continue
                
                if not input_element:
                    page.screenshot(path='/tmp/gemini_chat_error.png')
                    logger.error("Could not find input field. Screenshot saved.")
                    raise Exception("Could not find input field. Page may have changed.")
                
                input_element.click()
                page.wait_for_timeout(100)
                input_element.type(chat_message, delay=0)
                page.wait_for_timeout(100)
                
                # Try multiple send strategies
                send_success = False
                try:
                    # Strategy 1: Look for send button
                    send_button = page.locator('button[aria-label*="Send"]').first
                    if send_button.count() > 0:
                        send_button.click()
                        send_success = True
                        logger.info("Clicked send button via aria-label")
                except:
                    pass
                
                if not send_success:
                    try:
                        # Strategy 2: Press Enter
                        page.keyboard.press('Enter')
                        send_success = True
                        logger.info("Sent message via Enter key")
                    except:
                        pass
                
                if not send_success:
                    raise Exception("Could not send message")
                
                # Wait for response to start appearing
                page.wait_for_timeout(500)
                
                # Wait for response to complete
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
                                logger.info(f"Extracted response using selector: {selector}")
                                break
                    except:
                        continue
                
                # Fallback: try to get any new text that appeared
                if not response_text:
                    try:
                        # Wait a bit more
                        page.wait_for_timeout(500)
                        # Try to get the last message-like element
                        all_text_elements = page.locator('div[class*="content"], div[class*="message"], div[jsname]').all()
                        for elem in reversed(all_text_elements):
                            text = elem.inner_text()
                            if text and len(text.strip()) > 20 and text.strip() != chat_message:
                                response_text = text
                                logger.info("Extracted response using fallback method")
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
            logger.info(f"Generating image with prompt: {prompt}")
            page.goto('https://gemini.google.com/app', timeout=60000, wait_until='domcontentloaded')
            page.wait_for_timeout(500)
            
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
                        element = page.wait_for_selector(selector, timeout=5000)
                        if element:
                            input_element = element
                            logger.info(f"Found input using selector: {selector}")
                            break
                    except PlaywrightTimeout:
                        continue
                
                if not input_element:
                    raise Exception("Could not find input field")
                
                input_element.click()
                page.wait_for_timeout(100)
                input_element.fill(f"Generate an image: {prompt}")
                page.wait_for_timeout(100)
                
                send_button = page.locator('button[aria-label*="Send"], button:has-text("Send")').first
                if send_button.count() > 0:
                    send_button.click()
                else:
                    page.keyboard.press('Enter')
                
                page.wait_for_timeout(500)
                
                wait_for_response_complete(page)
                
                page.wait_for_timeout(300)
                
                image_selectors = [
                    'img[src*="googleusercontent.com"]',
                    'img[src*="gemini"]',
                    'div[data-message-author-role="model"] img'
                ]
                
                google_image_urls = []
                for selector in image_selectors:
                    images = page.locator(selector).all()
                    for img in images:
                        src = img.get_attribute('src')
                        if src and ('http' in src) and src not in google_image_urls:
                            google_image_urls.append(src)
                    if google_image_urls:
                        break
                
                # Download images with authentication and save locally
                local_image_urls = []
                if google_image_urls:
                    for google_url in google_image_urls:
                        filename = download_authenticated_image(page, google_url)
                        if filename:
                            # Get the base URL from request
                            base_url = request.host_url.rstrip('/')
                            local_url = f"{base_url}/images/{filename}"
                            local_image_urls.append(local_url)
                
                if local_image_urls:
                    return jsonify({
                        'status': 'success',
                        'prompt': prompt,
                        'images': local_image_urls
                    })
                else:
                    return jsonify({
                        'status': 'error',
                        'message': 'No images generated. Gemini may not support image generation in this session.'
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
    
    try:
        with get_browser_page() as page:
            logger.info(f"Editing image with prompt: {prompt}")
            page.goto('https://gemini.google.com/app', timeout=60000, wait_until='domcontentloaded')
            page.wait_for_timeout(500)
            
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
                            file_input = page.locator(selector).first
                            if file_input.count() > 0:
                                logger.info(f"Found file input: {selector}")
                                break
                        else:
                            button = page.locator(selector).first
                            if button.count() > 0:
                                button.click()
                                page.wait_for_timeout(200)
                                file_input = page.locator('input[type="file"]').first
                                if file_input.count() > 0:
                                    logger.info(f"Found file input after clicking: {selector}")
                                    break
                    except:
                        continue
                
                if not file_input or file_input.count() == 0:
                    file_input = page.locator('input[type="file"]').first
                
                if file_input.count() > 0:
                    file_input.set_input_files(image_file)
                    page.wait_for_timeout(500)
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
                        element = page.wait_for_selector(selector, timeout=5000)
                        if element:
                            input_element = element
                            logger.info(f"Found input using selector: {selector}")
                            break
                    except PlaywrightTimeout:
                        continue
                
                if not input_element:
                    raise Exception("Could not find input field")
                
                input_element.click()
                page.wait_for_timeout(100)
                input_element.fill(prompt)
                page.wait_for_timeout(100)
                
                send_button = page.locator('button[aria-label*="Send"], button:has-text("Send")').first
                if send_button.count() > 0:
                    send_button.click()
                else:
                    page.keyboard.press('Enter')
                
                page.wait_for_timeout(500)
                
                wait_for_response_complete(page)
                
                page.wait_for_timeout(300)
                
                image_selectors = [
                    'img[src*="googleusercontent.com"]',
                    'img[src*="gemini"]',
                    'div[data-message-author-role="model"] img'
                ]
                
                google_result_urls = []
                for selector in image_selectors:
                    images = page.locator(selector).all()
                    for img in images:
                        src = img.get_attribute('src')
                        if src and ('http' in src) and src not in google_result_urls:
                            google_result_urls.append(src)
                    if google_result_urls:
                        break
                
                # Download images with authentication and save locally
                local_result_urls = []
                if google_result_urls:
                    for google_url in google_result_urls:
                        filename = download_authenticated_image(page, google_url)
                        if filename:
                            # Get the base URL from request
                            base_url = request.host_url.rstrip('/')
                            local_url = f"{base_url}/images/{filename}"
                            local_result_urls.append(local_url)
                
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
                
                os.unlink(image_file)
                
                if local_result_urls or response_text:
                    result = {
                        'status': 'success',
                        'original_image': img_url,
                        'prompt': prompt
                    }
                    if local_result_urls:
                        result['edited_images'] = local_result_urls  # type: ignore
                    if response_text:
                        result['response'] = response_text.strip()
                    return jsonify(result)
                else:
                    return jsonify({
                        'status': 'error',
                        'message': 'No edited images or response received from Gemini'
                    }), 500
                    
            except Exception as e:
                os.unlink(image_file)
                logger.error(f"Image editing error: {str(e)}")
                return jsonify({
                    'status': 'error',
                    'message': f'Failed to edit image: {str(e)}'
                }), 500
                
    except Exception as e:
        if image_file and os.path.exists(image_file):
            os.unlink(image_file)
        logger.error(f"Browser error: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Browser automation failed: {str(e)}'
        }), 500

@app.route('/', methods=['GET'])
def home():
    """API status check"""
    return jsonify({
        'status': 'alive',
        'message': 'API is running'
    })

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint - ultra-lightweight for fast response"""
    return jsonify({'status': 'healthy'}), 200

@app.route('/debug_cookies', methods=['GET'])
def debug_cookies():
    """Debug endpoint to check cookies in browser"""
    try:
        browser, context = get_or_create_browser()
        with get_browser_page() as page:
            page.goto('https://gemini.google.com/app', timeout=30000)
            page.wait_for_timeout(500)
            
            browser_cookies = context.cookies()
            
            # Check for login indicators
            login_check = check_login_required(page)
            
            # Check for user profile indicator (logged in state)
            page.screenshot(path='/tmp/debug_screenshot_app.png')
            
            # Get page title and URL
            page_title = page.title()
            page_url = page.url
            
            # Try to find the input field
            input_found = False
            try:
                input_element = page.wait_for_selector('rich-textarea[placeholder], div[contenteditable="true"][role="textbox"]', timeout=5000)
                input_found = input_element is not None
            except:
                pass
            
            return jsonify({
                'status': 'success',
                'loaded_cookies_count': len(load_cookies()),
                'browser_cookies_count': len(browser_cookies),
                'login_required': login_check,
                'input_field_found': input_found,
                'page_title': page_title,
                'page_url': page_url,
                'screenshot': '/tmp/debug_screenshot_app.png'
            })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

# Initialize cleanup on module load (works with both Flask dev server and gunicorn)
try:
    # Start very aggressive cleanup for 512MB RAM limit (runs every 1 hour, deletes images older than 3 hours)
    start_cleanup_thread(interval_hours=1, max_age_hours=3)
    
    # Run initial cleanup on startup
    cleanup_old_images(max_age_hours=3)
except Exception as e:
    logger.error(f"Failed to initialize cleanup: {str(e)}")

if __name__ == '__main__':
    # Use PORT from environment (for Render) or default to 5000
    port = int(os.environ.get('PORT', 5000))
    
    # Use production settings for free tier
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
