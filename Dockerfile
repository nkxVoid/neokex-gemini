FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies for Chromium
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libwayland-client0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers with dependencies
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy application files
COPY . .

# Create static images directory
RUN mkdir -p static/images

# Expose port
EXPOSE 5000

# Set environment variable to skip host requirements validation
ENV PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true

# Run with optimized gunicorn settings for Render free tier
CMD gunicorn --bind 0.0.0.0:${PORT:-5000} \
    --workers 1 \
    --threads 1 \
    --worker-class sync \
    --timeout 90 \
    --graceful-timeout 30 \
    --keep-alive 2 \
    --max-requests 1000 \
    --max-requests-jitter 100 \
    --worker-tmp-dir /dev/shm \
    --log-level error \
    --preload \
    app:app
