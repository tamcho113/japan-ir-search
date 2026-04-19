FROM python:3.12-slim

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency files first for caching
COPY pyproject.toml uv.lock ./

# Install dependencies (no dev deps)
RUN uv sync --frozen --no-dev --no-install-project

# Copy source
COPY src/ src/
COPY README.md ./

# Install the project itself
RUN uv sync --frozen --no-dev

# Data directory (mounted as Fly volume)
ENV JAPAN_IR_SEARCH_DATA=/data
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8000

EXPOSE 8000

CMD ["uv", "run", "japan-ir-search", "serve", "--transport", "streamable-http"]
