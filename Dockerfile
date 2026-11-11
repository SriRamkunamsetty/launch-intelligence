# ==============================================================================
# Multi-Stage Production Dockerfile for Launch Intelligence
# Stage 1: Builder stage compiles wheels and installs dependencies
# Stage 2: Hardened, non-root runtime container with minimal attack surface
# ==============================================================================

FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install build dependencies and build wheels
RUN pip install --no-cache-dir --upgrade pip build wheel && \
    pip install --no-cache-dir ".[all]" && \
    pip wheel --no-cache-dir --wheel-dir=/build/wheels ".[all]"


# ==============================================================================
# Stage 2: Production Runtime Stage
# ==============================================================================
FROM python:3.12-slim AS runner

LABEL maintainer="SriRamKunamsetty <mohansriramkunamsetty@gmail.com>"
LABEL org.opencontainers.image.source="https://github.com/SriRamkunamsetty/launch-intelligence"
LABEL org.opencontainers.image.description="Enterprise entertainment launch analytics & AI forecasting"

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8000

# Install runtime system packages (curl for healthcheck, libpq for postgres)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Create dedicated non-root application user
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/bash -m appuser

# Copy installed wheels and app from builder
COPY --from=builder /build/wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/*.whl && \
    rm -rf /wheels

COPY --chown=appuser:appgroup src/ /app/src/
COPY --chown=appuser:appgroup pyproject.toml README.md /app/

# Install the app itself in editable/site-packages
RUN pip install --no-cache-dir --no-deps -e /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

ENTRYPOINT ["launch-intel"]
CMD ["api", "--host", "0.0.0.0", "--port", "8000"]
