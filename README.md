# **HDHomeRun Legacy Docker Proxy**

A self-contained, multi-threaded Docker service that makes legacy SiliconDust HDHomeRun network tuners compatible with modern PVR software like xTeVe, Plex, and Jellyfin.

## **The Problem**

Very old HDHomeRun models (e.g., the white box models like HDHR-US, HDHR3-US) do not have a built-in HTTP server for streaming. They serve raw MPEG-TS video data over UDP/RTP, a protocol that modern clients no longer support. This makes these perfectly functional legacy tuners incompatible with most modern PVR software.

## **The Solution**

This project runs a lightweight Python proxy in a Docker container. It acts as a smart bridge:

1. **Auto-Discovers:** It automatically finds your HDHomeRun tuner on the network.  
2. **Auto-Configures:** It connects to the tuner, discovers its configuration (like tuner count), and automatically fetches the current channel lineup.  
3. **Re-Broadcasts:** When a client requests a channel, the proxy uses the official `hdhomerun_config` command-line tool to tune a free tuner on the legacy device. It then captures the raw video stream and rebroadcasts it over a standard, modern HTTP stream that any client can understand.

## **Features**

* **Fully Automated:** No need to configure IP addresses, channel lists, or tuner counts. The service discovers everything on startup.  
* **Multi-Client Ready:** The server is multi-threaded, allowing it to serve different channels to multiple clients simultaneously, up to the number of available tuners on your device.  
* **Self-Contained:** The `proxy.py` script is baked into the Docker image, making deployment incredibly simple. No extra files are needed.  
* **Lightweight:** The official Python-slim base image and minimal dependencies keep the container small and efficient.  
* **Plug-and-Play:** Designed for easy deployment on any system that can run a Docker container.

## **Prerequisites**

* A legacy HDHomeRun network tuner on the same local network.  
* A host machine (like a NAS, server, or Raspberry Pi) running Docker and Docker Compose (for the compose method).

## **Deployment**

There are two recommended methods for running this container. Using Docker Compose is the preferred method as it makes configuration clear and repeatable.

### **Option 1: Using Docker Compose (Recommended)**

1. Create a file named `docker-compose.yml` on your Docker host.  
2. Paste the contents below into the file.  
3. From your terminal in the same directory as the file, run the command `docker-compose up -d`.

#### **`docker-compose.yml`**

\# Docker Compose file for the HDHomeRun Legacy Proxy  
version: '3.8'

services:  
  hdhomerun-proxy:  
    \# This is the publicly available, self-contained image.  
    \# Replace 'w84no1' with your Docker Hub username if you build your own.  
    image: w84no1/hdhr-proxy:latest  
      
    container\_name: hdhomerun-proxy  
      
    \# CRITICAL: This allows the container to discover the HDHomeRun   
    \# on your local network using UDP broadcasts.  
    network\_mode: host  
      
    \# Ensures the container starts automatically if the server reboots.  
    restart: unless-stopped

### **Option 2: Using Docker Run**

If you prefer not to use Docker Compose, you can run the container with a single command from your terminal.

docker run \-d \\  
  \--name hdhomerun-proxy \\  
  \--net=host \\  
  \--restart unless-stopped \\  
  w84no1/hdhr-proxy:latest

## **Using the Proxy**

Once the container is running, it's ready to use.

**M3U Playlist URL:** The proxy automatically generates a playlist compatible with most PVR software. Use the following URL in your client (xTeVe, Jellyfin, etc.), replacing `<YOUR_DOCKER_HOST_IP>` with the IP address of the machine running this container.  
http://\<YOUR\_DOCKER\_HOST\_IP\>:5004/lineup.m3u

* 

## **Troubleshooting**

* **Container Fails to Start with "No HDHomeRun devices found":**  
  * Ensure your HDHomeRun is powered on and connected to the same network as your Docker host.  
  * Confirm that `--net=host` (for `docker run`) or `network_mode: host` (for Docker Compose) is correctly set. This setting is essential for the discovery process to work.  
* **Channels Won't Tune or Stream is Buffering:**  
  * Check the container logs for errors using `docker logs hdhomerun-proxy`.  
  * Ensure your Docker host has a stable network connection to the HDHomeRun.  
  * Some host systems have a built-in firewall. Ensure it is not blocking traffic between the Docker container and your local network devices.

## **Credits**

This project relies on the official `hdhomerun-config` command-line utility provided by [SiliconDust](https://www.silicondust.com/).