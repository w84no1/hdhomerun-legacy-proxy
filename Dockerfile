# Use an official lightweight Python image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Install dependencies: hdhomerun_config and the Python 'requests' library
RUN apt-get update && \
    apt-get install -y hdhomerun-config && \
    pip install requests && \
    rm -rf /var/lib/apt/lists/*

# The COPY command for proxy.py is not needed if using a volume mount
# COPY proxy.py .

# Expose the port the proxy will run on
EXPOSE port 5004

# Command to run the proxy script when the container starts
CMD ["python", "-u", "proxy.py"]