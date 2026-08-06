FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# temp install fixed version of researcher
RUN pip uninstall -y gpt-researcher
RUN pip install --no-cache-dir --force-reinstall "gpt-researcher @ git+https://github.com/assafelovic/gpt-researcher.git@5d84d2f5553e70a2765a8ff3a0d2672d60437ce8"

# Copy application code
COPY . .

# Set environment variables for Docker
ENV MCP_TRANSPORT=sse
ENV DOCKER_CONTAINER=true
ENV PYTHONUNBUFFERED=1

# Expose the port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=7s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the server
CMD ["python", "server.py"] 
