# Use official Playwright Python image (fixes missing font packages on Railway)
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

# Set environment variables for Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HEADLESS=true

WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir requests

# Copy the rest of the project codebase
COPY . .

# Run the engine
CMD ["python", "main.py"]
