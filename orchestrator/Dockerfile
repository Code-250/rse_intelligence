FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY orchestrator/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy entire repo (agents CLAUDE.md files + orchestrator code)
COPY . .

ENV PYTHONPATH=/app
ENV PORT=8080

EXPOSE 8080

CMD ["uvicorn", "orchestrator.main:app", "--host", "0.0.0.0", "--port", "8080"]
