FROM python:3.11-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App code (this Dockerfile expects the build context to be THIS directory —
# set the Railway service "Root Directory" to
# products/financial-doc-analyzer/backend).
COPY . .

ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
