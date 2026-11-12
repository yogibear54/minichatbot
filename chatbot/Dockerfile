# Production image for the site chatbot.
# Base image ships Playwright + Chromium (used by render_js for JS-rendered sites).
FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY requirements.txt .
RUN pip install -r requirements.txt \
    && playwright install chromium

COPY . .

# Chromium in containers needs the no-sandbox flags; core/ingest.py applies them
# automatically when running as root (the default in this image).
ENV CONFIG_PATH=/app/site/config.yaml \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request, os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/api/health', timeout=4)"]

CMD ["sh", "-c", "python cli.py serve --config ${CONFIG_PATH} --host 0.0.0.0 --port ${PORT}"]
