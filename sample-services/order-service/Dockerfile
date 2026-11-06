FROM python:3.11-slim

WORKDIR /app

# Install curl for container health check
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency installation
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy workspace dependencies configuration
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application and shared modules
COPY common/ ./common/
COPY order-service/ ./order-service/

WORKDIR /app/order-service
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"

EXPOSE 8002

HEALTHCHECK --interval=5s --timeout=3s --retries=3 \
  CMD curl -f http://localhost:8002/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
