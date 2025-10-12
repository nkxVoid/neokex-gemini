# Gemini Scraper API

A Flask-based API server that uses web scraping to interact with Google Gemini for chat, image generation, and image editing.

## Features

- **Chat with Gemini**: Send text messages and get AI responses
- **Generate Images**: Create images from text descriptions
- **Edit Images**: Modify existing images with text instructions
- **Cookie-based Authentication**: Uses your Gemini account cookies for access

## Setup Instructions

### 1. Configure Your Cookies

1. Open your browser and log in to [Gemini](https://gemini.google.com/u/5/app?pageId=none)
2. Open Developer Tools (F12)
3. Go to **Application** > **Cookies** > `https://gemini.google.com`
4. Copy the following important cookies:
   - `__Secure-1PSID`
   - `__Secure-1PSIDTS`
   - `__Secure-1PAPISID`
   - `__Secure-3PSID` (if available)
   - `__Secure-3PSIDCC` (if available)

5. Open `account.txt` and add your cookies in this format:
   ```
   __Secure-1PSID=your_value_here
   __Secure-1PSIDTS=your_value_here
   __Secure-1PAPISID=your_value_here
   ```

### 2. Start the Server

The server is already running on port 5000. You can access it at the webview URL.

## API Endpoints

### 1. Home / Documentation
```
GET /
```
Returns API documentation and available endpoints.

### 2. Health Check
```
GET /health
```
Check if the server is running and cookies are loaded.

**Response:**
```json
{
  "status": "healthy",
  "cookies_loaded": true,
  "cookie_count": 5
}
```

### 3. Chat with Gemini
```
GET /gemini?chat=your_message
```

**Parameters:**
- `chat` (required): Your message to Gemini

**Example:**
```
/gemini?chat=What is the capital of France?
```

**Response:**
```json
{
  "status": "success",
  "chat": "What is the capital of France?",
  "response": "The capital of France is Paris."
}
```

### 4. Generate Images
```
GET /gimage?prompt=image_description
```

**Parameters:**
- `prompt` (required): Description of the image to generate

**Example:**
```
/gimage?prompt=A beautiful sunset over mountains
```

**Response:**
```json
{
  "status": "success",
  "prompt": "A beautiful sunset over mountains",
  "images": ["https://..."]
}
```

### 5. Edit Images
```
GET /gedit?imgUrl=image_url&prompt=edit_instructions
```

**Parameters:**
- `imgUrl` (required): URL of the image to edit
- `prompt` (required): Instructions for editing the image

**Example:**
```
/gedit?imgUrl=https://example.com/image.jpg&prompt=Make it brighter
```

**Response:**
```json
{
  "status": "success",
  "original_image": "https://example.com/image.jpg",
  "prompt": "Make it brighter",
  "edited_images": ["https://..."]
}
```

## Error Responses

All endpoints return error responses in this format:

```json
{
  "status": "error",
  "message": "Error description"
}
```

## Important Notes

✅ **Features:**
- **Robust automation**: Multiple selector fallbacks for better reliability
- **Login detection**: Automatically detects and reports authentication failures
- **Image upload**: /gedit endpoint downloads and properly uploads images
- **Response verification**: Waits for complete responses before returning
- **Error handling**: Comprehensive error messages for easier debugging

⚠️ **Limitations:**
- This API uses web scraping and may break if Google changes Gemini's UI
- Cookie-based authentication may expire and need to be refreshed
- Rate limiting may apply based on your Gemini account
- Image editing functionality depends on Gemini's capabilities
- Image generation depends on your Gemini account having access to this feature

⚠️ **Security:**
- Never share your `account.txt` file (it contains your authentication cookies)
- The `account.txt` file is added to `.gitignore` by default
- Keep your cookies private and secure

## Troubleshooting

### Cookies Not Working
1. Make sure you're logged into the correct Gemini account (u/5)
2. Copy ALL cookies from the Gemini domain
3. Ensure cookies are in the correct format (name=value)

### No Response from Endpoints
1. Check if cookies are loaded: visit `/health`
2. Verify your cookies are still valid (not expired)
3. Check the server logs for detailed error messages

### Browser Automation Issues
- The server uses Playwright with Chromium browser
- Selectors may need updates if Gemini's UI changes
- Check logs for specific error messages

## Technical Stack

- **Flask**: Web framework
- **Playwright**: Browser automation
- **Python 3.11**: Runtime environment

## License

This is a personal project for educational purposes. Please respect Google's Terms of Service when using this API.
