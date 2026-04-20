FROM python:3.11-slim
LABEL org.opencontainers.image.title="FinTech GOAT - MCP mcp_internal_bridge"
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN mkdir mcp_internal_bridge
COPY . ./mcp_internal_bridge/
EXPOSE 8080
CMD ["python", "-m", "mcp_internal_bridge.server"]
