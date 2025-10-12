import sys
from pathlib import Path

# Add parent directory to path so we can import app
sys.path.insert(0, str(Path(__file__).parent.parent))

from app import app

# Export the Flask app for Vercel
# Vercel will automatically use this as the WSGI application
