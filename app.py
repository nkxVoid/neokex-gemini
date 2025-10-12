from flask import Flask, request, jsonify, send_from_directory
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import os
import requests
import tempfile
from pathlib import Path
import logging
import uuid
import json

os.environ['PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS'] = 'true'

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET', 'dev-secret-key')

STATIC_IMAGES_DIR = Path('static/images')
STATIC_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_cookies():
    """Load cookies from account.txt file or ACCOUNT_COOKIES env var - supports JSON, Netscape, and name=value formats"""
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
            return cookies
        except Exception as e:
            logger.error(f"Error parsing cookies: {str(e)}")
            return []
    
    return cookies

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
        }""", timeout=60000)
        
        page.wait_for_timeout(2000)
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
        with sync_playwright() as p:
            browser, context = setup_browser_context(p)
            page = context.new_page()
            page.set_default_timeout(90000)
            
            logger.info(f"Navigating to Gemini with message: {chat_message}")
            page.goto('https://gemini.google.com/app', timeout=90000, wait_until='domcontentloaded')
            page.wait_for_timeout(5000)
            
            if check_login_required(page):
                browser.close()
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
                        element = page.wait_for_selector(selector, timeout=10000, state='visible')
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
                page.wait_for_timeout(1000)
                input_element.type(chat_message)
                page.wait_for_timeout(1000)
                
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
                page.wait_for_timeout(3000)
                
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
                        page.wait_for_timeout(2000)
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
                    browser.close()
                    return jsonify({
                        'status': 'success',
                        'chat': chat_message,
                        'response': response_text.strip()
                    })
                else:
                    # Save debug screenshot before closing browser
                    page.screenshot(path='/tmp/gemini_no_response.png')
                    logger.error("Could not extract response. Screenshot saved to /tmp/gemini_no_response.png")
                    browser.close()
                    return jsonify({
                        'status': 'error',
                        'message': 'No valid response received from Gemini',
                        'debug': 'Screenshot saved to /tmp/gemini_no_response.png'
                    }), 500
                
            except Exception as e:
                browser.close()
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
        with sync_playwright() as p:
            browser, context = setup_browser_context(p)
            page = context.new_page()
            page.set_default_timeout(90000)
            
            logger.info(f"Generating image with prompt: {prompt}")
            page.goto('https://gemini.google.com/app', timeout=90000, wait_until='domcontentloaded')
            page.wait_for_timeout(5000)
            
            if check_login_required(page):
                browser.close()
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
                page.wait_for_timeout(500)
                input_element.fill(f"Generate an image: {prompt}")
                page.wait_for_timeout(500)
                
                send_button = page.locator('button[aria-label*="Send"], button:has-text("Send")').first
                if send_button.count() > 0:
                    send_button.click()
                else:
                    page.keyboard.press('Enter')
                
                page.wait_for_timeout(3000)
                
                wait_for_response_complete(page)
                
                page.wait_for_timeout(2000)
                
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
                
                browser.close()
                
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
                browser.close()
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
        with sync_playwright() as p:
            browser, context = setup_browser_context(p)
            page = context.new_page()
            page.set_default_timeout(90000)
            
            logger.info(f"Editing image with prompt: {prompt}")
            page.goto('https://gemini.google.com/app', timeout=90000, wait_until='domcontentloaded')
            page.wait_for_timeout(5000)
            
            if check_login_required(page):
                browser.close()
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
                                page.wait_for_timeout(500)
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
                    page.wait_for_timeout(2000)
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
                page.wait_for_timeout(500)
                input_element.fill(prompt)
                page.wait_for_timeout(500)
                
                send_button = page.locator('button[aria-label*="Send"], button:has-text("Send")').first
                if send_button.count() > 0:
                    send_button.click()
                else:
                    page.keyboard.press('Enter')
                
                page.wait_for_timeout(3000)
                
                wait_for_response_complete(page)
                
                page.wait_for_timeout(2000)
                
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
                
                browser.close()
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
                browser.close()
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
    """Health check endpoint"""
    cookies = load_cookies()
    return jsonify({
        'status': 'healthy',
        'cookies_loaded': len(cookies) > 0,
        'cookie_count': len(cookies)
    })

@app.route('/debug_cookies', methods=['GET'])
def debug_cookies():
    """Debug endpoint to check cookies in browser"""
    try:
        with sync_playwright() as p:
            browser, context = setup_browser_context(p)
            page = context.new_page()
            
            page.goto('https://gemini.google.com/app', timeout=30000)
            page.wait_for_timeout(3000)
            
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
            
            browser.close()
            
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
