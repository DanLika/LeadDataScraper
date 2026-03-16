# Use the official Microsoft Playwright image which comes with all necessary dependencies
# This avoids complicated dependency installation for browser automation on Linux
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# Set work directory
WORKDIR /app

# Install system dependencies if any extra are needed (usually not with the official image)
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (chromium is enough for our scraper)
RUN playwright install chromium

# Copy project files
# We copy everything, including src/, backend/, and the root files
COPY . .

# Expose the default FastAPI port
EXPOSE 8000

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Start the FastAPI application via uvicorn
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
