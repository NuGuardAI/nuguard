FROM python:3.11-slim

LABEL org.opencontainers.image.title="FinTech GOAT - MCP Banking Server"
LABEL org.opencontainers.image.description="Deliberately vulnerable MCP server for NuGuard red-team testing"

# /app is the Python root; mcp_server/ is the package inside it.
WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Install all deps from requirements.txt; Celery 5 is included as-is
# (celery[redis]>=5.4.0 — replaces vulnerable Celery 3 per Redis migration).
# VULN-03b/c kept: requests==2.24.0, urllib3==1.25.11, SQLAlchemy==1.3.23
RUN pip install --no-cache-dir -r requirements.txt

# Copy source into the mcp_server/ package directory so that
# `python -m mcp_server.server` resolves correctly from /app.
RUN mkdir mcp_server
COPY . ./mcp_server/

# Expose SSE transport port
EXPOSE 8080

# Start the MCP server
CMD ["python", "-m", "mcp_server.server"]
