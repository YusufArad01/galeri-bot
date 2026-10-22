# Use official Playwright Python image (fixes missing font packages on Railway)
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

# Set environment variables for Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HEADLESS=true

WORKDIR /app

# Sanal ekran (xvfb) ve gerçek Google Chrome: Cloudflare doğrulaması için gerçek tarayıcı gerekiyor.
# Kurulamazsa derleme bozulmasın: uygulama paketli Chromium'a döner.
RUN apt-get update && apt-get install -y --no-install-recommends xvfb && rm -rf /var/lib/apt/lists/*
RUN playwright install chrome || echo "Google Chrome kurulamadi, paketli Chromium kullanilacak"

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir requests

# Copy the rest of the project codebase
COPY . .

# Run the engine
CMD ["python", "main.py"]
