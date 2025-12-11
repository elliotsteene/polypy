# syntax=docker/dockerfile:1

# Build stage
FROM python:3.14-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set working directory
WORKDIR /app

# Copy dependency files, source, and README
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# Install dependencies to virtual environment
RUN uv sync --frozen --no-dev

# Runtime stage
FROM python:3.14-slim

# Set working directory
WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application source
COPY src ./src

# Set PATH to use virtual environment
ENV PATH="/app/.venv/bin:$PATH"

# Expose HTTP server port
EXPOSE 8080

# Run the application
CMD ["python", "src/main.py"]
