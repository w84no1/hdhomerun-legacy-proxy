# Use an official lightweight Python image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Install dependencies: hdhomerun_config and curl (for healthcheck)
# No pip packages required — the proxy uses only the Python standard library.
RUN apt-get update && \
    apt-get install -y --no-install-recommends hdhomerun-config curl && \
    rm -rf /var/lib/apt/lists/*

# Copy the proxy script into the container
COPY proxy.py .

# Expose the port the proxy will run on
EXPOSE 5004

# Health check using the /health endpoint
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:5004/health || exit 1

# Command to run the proxy script when the container starts
CMD ["python", "-u", "proxy.py"]