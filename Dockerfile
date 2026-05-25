# syntax=docker/dockerfile:1
FROM python:3.12-slim

# System deps required by Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 \
    libatspi2.0-0 libwayland-client0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser only
RUN playwright install chromium --with-deps

# Copy source code
COPY . .

# Create runtime directories
RUN mkdir -p data logs sessions uploads errors

EXPOSE 5000

ENV PYTHONUNBUFFERED=1
ENV PYTHONUTF8=1

CMD ["python", "gui_server.py"]
