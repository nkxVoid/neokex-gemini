# Cookie Format Guide for account.txt

The `account.txt` file now supports **three different cookie formats**. The system automatically detects which format you're using.

## 🔥 Format 1: JSON (Recommended)

The most flexible and feature-rich format. Supports all cookie properties.

### Example:
```json
[
  {
    "name": "__Secure-1PSID",
    "value": "your_cookie_value_here",
    "domain": ".google.com",
    "path": "/",
    "secure": true,
    "httpOnly": true,
    "sameSite": "None"
  },
  {
    "name": "__Secure-1PSIDTS",
    "value": "another_cookie_value",
    "domain": ".google.com",
    "path": "/",
    "secure": true
  }
]
```

### Supported Properties:
- `name` (required): Cookie name
- `value` (required): Cookie value
- `domain` (optional): Cookie domain (default: `.google.com`)
- `path` (optional): Cookie path (default: `/`)
- `secure` (optional): HTTPS only flag
- `httpOnly` (optional): HTTP only flag
- `sameSite` (optional): SameSite policy (`Strict`, `Lax`, or `None`)
- `expires` (optional): Expiration timestamp

### How to Export Cookies in JSON Format:

#### From Browser DevTools:
1. Open Chrome/Edge DevTools (F12)
2. Go to Application → Storage → Cookies
3. Select the website (e.g., `https://gemini.google.com`)
4. Use a cookie export extension like **EditThisCookie** or **Cookie-Editor**
5. Export as JSON and paste into `account.txt`

---

## 📋 Format 2: Netscape (Tab-Separated)

The classic Netscape cookie format with tab-separated values.

### Example:
```
.google.com	TRUE	/	TRUE	1234567890	__Secure-1PSID	cookie_value_here
.google.com	TRUE	/	TRUE	1234567890	__Secure-1PSIDTS	another_value
```

### Format Structure:
```
domain [TAB] flag [TAB] path [TAB] secure [TAB] expiry [TAB] name [TAB] value
```

### How to Export:
- Use browser extensions like **EditThisCookie** (select Netscape format)
- Export from curl: `curl -c cookies.txt https://example.com`

---

## 📝 Format 3: Simple Name=Value

The simplest format for quick testing. One cookie per line.

### Example:
```
__Secure-1PSID=your_cookie_value_here
__Secure-1PSIDTS=another_cookie_value
__Secure-1PAPISID=yet_another_value
```

### Notes:
- Automatically sets domain to `.google.com` and path to `/`
- Good for quick testing but lacks advanced cookie properties
- Lines starting with `#` are treated as comments

---

## 🔑 Important Gemini Cookies

For Gemini to work properly, you need these cookies:

1. **`__Secure-1PSID`** - Primary session ID
2. **`__Secure-1PSIDTS`** - Session timestamp
3. **`__Secure-1PAPISID`** - API session ID

Optional but recommended:
- `SID` - Session ID
- `SIDCC` - Session cookie
- `SAPISID` - API session ID
- `APISID` - Another API session ID

---

## 🛠️ How to Get Your Cookies

### Method 1: Browser DevTools (Manual)
1. Visit `https://gemini.google.com` and log in
2. Open DevTools (F12) → Application → Cookies
3. Copy the required cookies
4. Paste into `account.txt` in any format above

### Method 2: Cookie Export Extension (Recommended)
1. Install **EditThisCookie** or **Cookie-Editor** extension
2. Visit `https://gemini.google.com` and log in
3. Click the extension icon
4. Export as JSON or Netscape format
5. Save to `account.txt`

---

## ✅ Testing Your Setup

After adding cookies to `account.txt`, test with:

```bash
curl http://localhost:5000/health
```

Expected response:
```json
{
  "status": "healthy",
  "cookies_loaded": true,
  "cookie_count": 11
}
```

---

## 🔄 Format Auto-Detection

The system automatically detects your cookie format:

1. **JSON Check**: Tries to parse as JSON first
2. **Netscape Check**: Looks for tab-separated values
3. **Name=Value Check**: Falls back to simple format

You can mix comments (`#`) with Netscape and Name=Value formats, but not with JSON.

---

## ⚠️ Important Notes

- Keep your cookies **private** - never commit them to version control
- Cookies expire - refresh them if authentication fails
- Use JSON format for best compatibility and features
- The file is read fresh on each API request
