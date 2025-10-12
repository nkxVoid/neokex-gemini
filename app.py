from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright
import os
import json
import time
import base64
from pathlib import Path
import logging

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
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    )
    
    cookies = load_cookies()
    if cookies:
        context.add_cookies(cookies)
    
    return browser, context

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
            page.goto('https://gemini.google.com/u/5/app?pageId=none', timeout=30000)
            page.wait_for_timeout(3000)
            
            # Try to find the input field
            try:
                input_selector = 'rich-textarea[aria-label*="prompt"], textarea[aria-label*="prompt"], div[contenteditable="true"]'
                page.wait_for_selector(input_selector, timeout=10000)
                page.fill(input_selector, chat_message)
                page.wait_for_timeout(500)
                
                # Try to submit
                page.keyboard.press('Enter')
                page.wait_for_timeout(5000)
                
                # Wait for response
                response_selector = 'div[data-test-id*="response"], .model-response, message-content'
                page.wait_for_selector(response_selector, timeout=30000)
                page.wait_for_timeout(2000)
                
                # Extract response
                response_elements = page.query_selector_all(response_selector)
                response_text = response_elements[-1].inner_text() if response_elements else "No response received"
                
                browser.close()
                
                return jsonify({
                    'status': 'success',
                    'chat': chat_message,
                    'response': response_text
                })
                
            except Exception as e:
                browser.close()
                logger.error(f"Scraping error: {str(e)}")
                return jsonify({
                    'status': 'error',
                    'message': f'Failed to interact with Gemini: {str(e)}',
                    'hint': 'Make sure cookies are valid and Gemini UI has not changed'
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
            page.goto('https://gemini.google.com/u/5/app?pageId=none', timeout=30000)
            page.wait_for_timeout(3000)
            
            try:
                # Send image generation prompt
                input_selector = 'rich-textarea[aria-label*="prompt"], textarea[aria-label*="prompt"], div[contenteditable="true"]'
                page.wait_for_selector(input_selector, timeout=10000)
                page.fill(input_selector, f"Generate an image: {prompt}")
                page.wait_for_timeout(500)
                
                page.keyboard.press('Enter')
                page.wait_for_timeout(10000)  # Wait longer for image generation
                
                # Try to find generated image
                image_selector = 'img[src*="googleusercontent"], img[alt*="generated"]'
                page.wait_for_selector(image_selector, timeout=30000)
                
                images = page.query_selector_all(image_selector)
                image_urls = [img.get_attribute('src') for img in images if img.get_attribute('src')]
                
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
                        'message': 'No images generated'
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
    
    try:
        with sync_playwright() as p:
            browser, context = setup_browser_context(p)
            page = context.new_page()
            
            logger.info(f"Editing image: {img_url} with prompt: {prompt}")
            page.goto('https://gemini.google.com/u/5/app?pageId=none', timeout=30000)
            page.wait_for_timeout(3000)
            
            try:
                # Try to upload image via URL (this depends on Gemini's UI)
                input_selector = 'rich-textarea[aria-label*="prompt"], textarea[aria-label*="prompt"], div[contenteditable="true"]'
                page.wait_for_selector(input_selector, timeout=10000)
                
                # Send edit request with image URL
                edit_message = f"Edit this image {img_url} according to: {prompt}"
                page.fill(input_selector, edit_message)
                page.wait_for_timeout(500)
                
                page.keyboard.press('Enter')
                page.wait_for_timeout(10000)
                
                # Wait for edited image
                image_selector = 'img[src*="googleusercontent"], img[alt*="edited"]'
                page.wait_for_selector(image_selector, timeout=30000)
                
                images = page.query_selector_all(image_selector)
                image_urls = [img.get_attribute('src') for img in images if img.get_attribute('src')]
                
                browser.close()
                
                if image_urls:
                    return jsonify({
                        'status': 'success',
                        'original_image': img_url,
                        'prompt': prompt,
                        'edited_images': image_urls
                    })
                else:
                    return jsonify({
                        'status': 'error',
                        'message': 'No edited images received'
                    }), 500
                    
            except Exception as e:
                browser.close()
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
