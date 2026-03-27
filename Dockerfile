# Use official Python slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all bot source files
COPY . .

# Fly.io: no port needed (background worker, not a web server)
# Environment variables (API keys) are injected via fly secrets

# Run the trading bot
CMD ["python", "-u", "automated_trading_loop.py"]
