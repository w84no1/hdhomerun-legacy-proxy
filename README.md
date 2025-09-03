# HDHomeRun Legacy Docker Proxy

A simple Python proxy server, designed to run in Docker, that makes legacy SiliconDust HDHomeRun network tuners (which use a raw UDP/RTP stream) compatible with modern clients like xTeVe, Plex, and Jellyfin by rebroadcasting them as a standard HTTP stream.

## The Problem
Very old HDHomeRun models (e.g., HDHR-US, HDHR3-US) do not have the built-in HTTP server on port 5004 that modern clients expect. This makes them incompatible with most IPTV-style PVR software. The official HDHomeRun app still works, but third-party tools cannot access the streams.

## The Solution
This script runs as a background service. When it receives a request for a channel, it uses the official `hdhomerun_config` command-line tool to tune the legacy device and then pipes the resulting raw video stream into a simple HTTP server. It also dynamically generates a `lineup.m3u` playlist for easy integration.

## Features
-   Acts as a bridge between legacy HDHomeRun tuners and modern clients.
-   Runs in a lightweight, self-contained Docker container.
-   Dynamically generates an M3U playlist compatible with xTeVe and other PVRs.
-   Configurable via environment variables and direct script edits.

## Configuration
1.  **Edit the Channel Lineup:** Open the `proxy.py` file and edit the `CHANNELS` list to match the output from your tuner's API.
2.  **Set the Device ID:** When running the Docker container, you must set the `HDHOMERUN_ID` environment variable to your tuner's 8-character device ID.

## Usage (with Docker)
1.  Place `proxy.py` and `Dockerfile` in a directory.
2.  Build the Docker image:
    ```sh
    docker build -t hdhomerun-legacy-proxy .
    ```
3.  Run the container, making sure to use host networking and set your device ID:
    ```sh
    docker run -d \
      --restart unless-stopped \
      --net=host \
      -e HDHOMERUN_ID=YOUR_ID_HERE \
      --name hdhomerun-proxy \
      hdhomerun-legacy-proxy
    ```
4.  In your PVR software (xTeVe, etc.), use the following URL for your M3U playlist, replacing `<YOUR_DOCKER_HOST_IP>` with the IP address of the machine running this container:
    ```
    http://<YOUR_DOCKER_HOST_IP>:5004/lineup.m3u
    ```

## Credits
This script is a simplified and combined implementation based on the work of many community members who have developed solutions for these legacy tuners over the years.
