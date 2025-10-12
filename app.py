from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import os
import requests
import tempfile
from pathlib import Path
import logging
import uuid

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET', 'dev-secret-key')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_cookies():
    """Load cookies from account.txt file"""
    cookies = []
    try:
        with open('account.txt', 'r') as f:
            lines = f.readlines()
            for line in lines:
                line = line.strip()
                if line and not line.startswith('#'):
                    if '=' in line:
                        name, value = line.split('=', 1)
                        cookies.append({
                            'name': name.strip(),
                            'value': value.strip(),
                            'domain': '.google.com',
                            'path': '/'
                        })
        logger.info(f"Loaded {len(cookies)} cookies from account.txt")
        return cookies
    except FileNotFoundError:
        logger.error("account.txt file not found")
        return []
    except Exception as e:
        logger.error(f"Error loading cookies: {str(e)}")
        return []

def setup_browser_context(playwright):
    """Setup browser with cookies"""
    browser = playwright.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
    context = browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    )
    
    cookies = load_cookies()
    if cookies:
        context.add_cookies(cookies)
    
    return browser, context

def check_login_required(page):
    """Check if login is required"""
    try:
        login_indicators = [
            'a[href*="accounts.google.com"]',
            'button:has-text("Sign in")',
            'a:has-text("Sign in")'
        ]
        for indicator in login_indicators:
            if page.locator(indicator).count() > 0:
                return True
        return False
    except:
        return False

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
            
            logger.info(f"Navigating to Gemini with message: {chat_message}")
            page.goto('https://gemini.google.com/app', timeout=60000)
            page.wait_for_load_state('networkidle')
            page.wait_for_timeout(3000)
            
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
                input_element.fill(chat_message)
                page.wait_for_timeout(500)
                
                send_button = page.locator('button[aria-label*="Send"], button:has-text("Send")').first
                if send_button.count() > 0:
                    send_button.click()
                else:
                    page.keyboard.press('Enter')
                
                page.wait_for_timeout(2000)
                
                wait_for_response_complete(page)
                
                response_selectors = [
                    'div[data-message-author-role="model"]',
                    'div.model-response-text',
                    'div[class*="response"] div[class*="message-content"]'
                ]
                
                response_text = None
                for selector in response_selectors:
                    elements = page.locator(selector).all()
                    if elements:
                        response_text = elements[-1].inner_text()
                        if response_text and len(response_text.strip()) > 0:
                            break
                
                browser.close()
                
                if response_text and len(response_text.strip()) > 0:
                    return jsonify({
                        'status': 'success',
                        'chat': chat_message,
                        'response': response_text.strip()
                    })
                else:
                    return jsonify({
                        'status': 'error',
                        'message': 'No valid response received from Gemini'
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
            
            logger.info(f"Generating image with prompt: {prompt}")
            page.goto('https://gemini.google.com/app', timeout=60000)
            page.wait_for_load_state('networkidle')
            page.wait_for_timeout(3000)
            
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
                
                image_urls = []
                for selector in image_selectors:
                    images = page.locator(selector).all()
                    for img in images:
                        src = img.get_attribute('src')
                        if src and ('http' in src) and src not in image_urls:
                            image_urls.append(src)
                    if image_urls:
                        break
                
                browser.close()
                
                if image_urls:
                    return jsonify({
                        'status': 'success',
                        'prompt': prompt,
                        'images': image_urls
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
            
            logger.info(f"Editing image with prompt: {prompt}")
            page.goto('https://gemini.google.com/app', timeout=60000)
            page.wait_for_load_state('networkidle')
            page.wait_for_timeout(3000)
            
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
                
                result_image_urls = []
                for selector in image_selectors:
                    images = page.locator(selector).all()
                    for img in images:
                        src = img.get_attribute('src')
                        if src and ('http' in src) and src not in result_image_urls:
                            result_image_urls.append(src)
                    if result_image_urls:
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
                
                browser.close()
                os.unlink(image_file)
                
                if result_image_urls or response_text:
                    result = {
                        'status': 'success',
                        'original_image': img_url,
                        'prompt': prompt
                    }
                    if result_image_urls:
                        result['edited_images'] = result_image_urls
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
    """API documentation endpoint"""
    return jsonify({
        'name': 'Gemini Scraper API',
        'version': '1.0.0',
        'endpoints': {
            '/gemini': {
                'method': 'GET',
                'params': {'chat': 'Your message to Gemini'},
                'example': '/gemini?chat=Hello, how are you?'
            },
            '/gimage': {
                'method': 'GET',
                'params': {'prompt': 'Image description'},
                'example': '/gimage?prompt=A beautiful sunset over mountains'
            },
            '/gedit': {
                'method': 'GET',
                'params': {
                    'imgUrl': 'URL of image to edit',
                    'prompt': 'Edit instructions'
                },
                'example': '/gedit?imgUrl=https://example.com/image.jpg&prompt=Make it brighter'
            }
        },
        'setup': {
            'step1': 'Add your Gemini cookies to account.txt',
            'step2': 'Format: name=value (one per line)',
            'step3': 'Important cookies: __Secure-1PSID, __Secure-1PSIDTS, __Secure-1PAPISID'
        }
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
