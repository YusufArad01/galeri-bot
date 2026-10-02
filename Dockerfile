# Use Python 3.11 Slim Image
FROM python:3.11-slim

# Set environment variables for Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
# Railway and CI/CD requires headless operation
ENV HEADLESS=true

# Install system dependencies needed for Playwright and Chromium
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Ensure requests is installed for telegram notification
RUN pip install --no-cache-dir requests

# Install Playwright and its Chromium dependencies
RUN playwright install --with-deps chromium

# Copy the rest of the project codebase
COPY . .

# Run the engine
CMD ["python", "main.py"]
