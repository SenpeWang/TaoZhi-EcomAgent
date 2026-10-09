# Multi-stage production Dockerfile for TaoZhi-EcomAgent
FROM python:3.10-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Final runtime image
FROM python:3.10-slim AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY src/ ./src/
COPY web/dist/ ./web/dist/
COPY scripts/ ./scripts/

ENV PYTHONPATH=/app/src
ENV APP_ENV=production
ENV PYTHONUNBUFFERED=1

EXPOSE 18501

CMD ["python", "-m", "ecom_copilot.enterprise.worker"]
