FROM node:24-slim AS frontend-builder
ARG NPM_REGISTRY=""
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi && npm ci
COPY web/ ./
RUN npm run build

FROM python:3.10-slim AS app
ARG APP_UID=1000
ARG APP_GID=1000
ARG PIP_INDEX_URL=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

ARG APT_MIRROR=""
RUN if [ -n "$APT_MIRROR" ]; then \
        sed -i "s#deb.debian.org#$APT_MIRROR#g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates postgresql-client \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g "${APP_GID}" appuser \
    && useradd -m -u "${APP_UID}" -g "${APP_GID}" appuser

COPY requirements.txt ./
RUN pip install --no-cache-dir --timeout 60 --retries 10 ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} -r requirements.txt

COPY alembic.ini ./
COPY config ./config
COPY migrations ./migrations
COPY src ./src
COPY scripts ./scripts
COPY evals ./evals
COPY --from=frontend-builder /web/dist ./web/dist

RUN mkdir -p /app/data/runtime && chown -R appuser:appuser /app/data
ARG GIT_SHA=""
ENV ECOM_GIT_SHA=${GIT_SHA}

USER appuser

EXPOSE 18080
CMD ["uvicorn", "taozhi.server.api:app", "--host", "0.0.0.0", "--port", "18080", "--no-access-log"]

FROM python:3.10-slim AS embedding
ARG APP_UID=1000
ARG APP_GID=1000
ARG PIP_INDEX_URL=""
ARG APT_MIRROR=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN if [ -n "$APT_MIRROR" ]; then \
        sed -i "s#deb.debian.org#$APT_MIRROR#g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g "${APP_GID}" appuser \
    && useradd -m -u "${APP_UID}" -g "${APP_GID}" appuser

RUN pip install --no-cache-dir --timeout 60 --retries 10 \
        ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} \
        --extra-index-url https://download.pytorch.org/whl/cu118 \
        torch==2.3.1+cu118 \
        "transformers==4.41.2" \
        "numpy<2" \
        "fastapi==0.141.0" \
        "uvicorn[standard]==0.34.0" \
        "pydantic>=2.10,<3"

COPY scripts/embedding_server.py ./scripts/embedding_server.py

USER appuser
EXPOSE 18555
CMD ["python", "scripts/embedding_server.py"]
