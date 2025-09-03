# Use an official lightweight Python image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Install the hdhomerun_config command-line utility
RUN apt-get update && \
    apt-get install -y hdhomerun-config && \
    rm -rf /var/lib/apt/lists/*

# Expose the port the proxy will run on
EXPOSE 5004

# Command to run the proxy script when the container starts
CMD ["python", "-u", "proxy.py"]
