FROM python:3.11-slim

WORKDIR /app

# System deps: Tesseract is needed to OCR scanned/image PDFs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App code (build context = this directory; set the Railway service
# "Root Directory" to products/financial-doc-analyzer/backend).
COPY . .

ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
