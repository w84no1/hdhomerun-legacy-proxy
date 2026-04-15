# HDHomeRun Legacy UDP-to-HTTP Proxy (v9 - Active Wake & Recovery)
import os
import re
import sys
import json
import signal
import subprocess
import requests
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlparse

# --- CONFIGURATION (Environment Variable Overrides) ---
HDHOMERUN_CONFIG_PATH = os.environ.get("HDHOMERUN_CONFIG_PATH", "hdhomerun_config")
PROXY_PORT = int(os.environ.get("PROXY_PORT", "5004"))
KEEP_ALIVE_INTERVAL = int(os.environ.get("KEEP_ALIVE_INTERVAL", "60"))  # seconds
WAKE_MAX_RETRIES = int(os.environ.get("WAKE_MAX_RETRIES", "3"))
STREAM_CHUNK_SIZE = int(os.environ.get("STREAM_CHUNK_SIZE", str(1024 * 128)))  # 128KB

# --- STATE (mutable globals protected by lock) ---
_state_lock = threading.Lock()
TUNER_COUNT = 0
CHANNELS = []
HDHOMERUN_IP = os.environ.get("HDHOMERUN_IP", None)  # Optional: skip auto-discovery
# --------------------------------------------------


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in a separate thread."""
    daemon_threads = True


# =============================================================================
#  Discovery & Wake
# =============================================================================

def discover_hdhomerun():
    """Broadcast-discover an HDHomeRun on the local network (UDP port 65001)."""
    print("Discovering HDHomeRun device on the network...")
    try:
        discover_cmd = [HDHOMERUN_CONFIG_PATH, "discover"]
        result = subprocess.run(
            discover_cmd, check=True, capture_output=True, text=True, timeout=10
        )
        match = re.search(r'found at (\d+\.\d+\.\d+\.\d+)', result.stdout)
        if match:
            ip_address = match.group(1)
            print(f"Found HDHomeRun at IP: {ip_address}")
            return ip_address
        else:
            print(f"Discovery ran but could not parse IP from output: {result.stdout.strip()}")
            return None
    except FileNotFoundError:
        print(f"FATAL: '{HDHOMERUN_CONFIG_PATH}' not found. Is it installed?")
        return None
    except subprocess.TimeoutExpired:
        print("Discovery timed out — no HDHomeRun found on the network.")
        return None
    except subprocess.CalledProcessError as e:
        print(f"Discovery failed: {e.stderr.strip() if e.stderr else e}")
        return None


def device_is_reachable(ip, timeout=5):
    """Quick check: can we talk to the HDHR at the given IP?"""
    try:
        cmd = [HDHOMERUN_CONFIG_PATH, ip, "get", "/sys/version"]
        result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)
        return bool(result.stdout.strip())
    except Exception:
        return False


def wake_and_recover():
    """
    Attempt to wake / rediscover the HDHomeRun.
    Uses exponential backoff across retries.  On success the global IP and
    CHANNELS are updated.
    Returns True if the device is reachable after recovery.
    """
    global HDHOMERUN_IP, CHANNELS, TUNER_COUNT

    for attempt in range(1, WAKE_MAX_RETRIES + 1):
        print(f"Wake attempt {attempt}/{WAKE_MAX_RETRIES}: running full UDP discovery...")
        new_ip = discover_hdhomerun()
        if new_ip and device_is_reachable(new_ip):
            with _state_lock:
                HDHOMERUN_IP = new_ip
            # Refresh lineup in case anything changed
            lineup = fetch_device_config_and_lineup(new_ip)
            if lineup:
                with _state_lock:
                    CHANNELS = lineup
            print(f"Wake successful — device at {new_ip}")
            return True
        backoff = 2 ** (attempt - 1)
        print(f"Wake attempt {attempt} failed. Retrying in {backoff}s...")
        time.sleep(backoff)

    print("Wake FAILED after all retries.")
    return False


# =============================================================================
#  Device Configuration
# =============================================================================

def fetch_device_config_and_lineup(hdhr_ip):
    """Fetch tuner count and channel lineup from the HDHR's HTTP interface."""
    global TUNER_COUNT
    print("Fetching device config and channel lineup...")
    try:
        discover_url = f"http://{hdhr_ip}/discover.json"
        print(f"  GET {discover_url}")
        discover_resp = requests.get(discover_url, timeout=30)
        discover_resp.raise_for_status()
        discover_data = discover_resp.json()

        tuner_count = discover_data.get("TunerCount")
        if tuner_count:
            TUNER_COUNT = int(tuner_count)
            print(f"  Device reports {TUNER_COUNT} tuner(s).")
        else:
            print("  Warning: TunerCount missing — defaulting to 2.")
            TUNER_COUNT = 2

        lineup_url = discover_data.get("LineupURL")
        if not lineup_url:
            print("  Error: LineupURL not found in discover.json")
            return None

        print(f"  GET {lineup_url}")
        lineup_resp = requests.get(lineup_url, timeout=30)
        lineup_resp.raise_for_status()
        lineup_data = lineup_resp.json()
        print(f"  Fetched {len(lineup_data)} channel(s).")
        return lineup_data

    except requests.exceptions.RequestException as e:
        print(f"  Network error fetching lineup: {e}")
        return None
    except (json.JSONDecodeError, KeyError) as e:
        print(f"  JSON parse error: {e}")
        return None


# =============================================================================
#  Tuner Helpers
# =============================================================================

def cleanup_tuners():
    """Release all tuners — called on startup and shutdown to clear stale locks."""
    if TUNER_COUNT == 0:
        return
    print("Releasing all tuners...")
    for i in range(TUNER_COUNT):
        try:
            run_command([HDHOMERUN_CONFIG_PATH, HDHOMERUN_IP, "set",
                         f"/tuner{i}/channel", "none"])
        except Exception:
            pass
    print("All tuners released.")


def find_free_tuner():
    """Return the index of a free tuner, or None."""
    for i in range(TUNER_COUNT):
        try:
            status_cmd = [HDHOMERUN_CONFIG_PATH, HDHOMERUN_IP, "get", f"/tuner{i}/status"]
            result = subprocess.run(
                status_cmd, check=True, capture_output=True, text=True, timeout=5
            )
            if "ch=none" in result.stdout:
                print(f"  Found free tuner: {i}")
                return i
        except subprocess.CalledProcessError:
            continue
    print("  Error: No free tuners available.")
    return None


def run_command(command, timeout=10):
    """Run a subprocess command. Returns True on success, False on failure."""
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
        return True
    except subprocess.CalledProcessError as e:
        error_output = e.stderr.strip() if e.stderr else "No error output."
        print(f"  Command failed: {' '.join(command)} — {error_output}")
        return False
    except subprocess.TimeoutExpired:
        print(f"  Command timed out: {' '.join(command)}")
        return False


def tune_to_channel(vchannel):
    """
    Find a free tuner and tune it to the given virtual channel.
    If the device is unreachable, attempts a wake/recovery cycle first.
    Returns (tuner_index, success).
    """
    # Pre-flight: is the device reachable?
    if not device_is_reachable(HDHOMERUN_IP):
        print(f"Device {HDHOMERUN_IP} unreachable — attempting wake...")
        if not wake_and_recover():
            return None, False

    tuner_index = find_free_tuner()
    if tuner_index is None:
        return None, False

    with _state_lock:
        channels = list(CHANNELS)

    target_channel = next((c for c in channels if c["GuideNumber"] == vchannel), None)
    if not target_channel:
        print(f"  Error: Virtual channel {vchannel} not in channel list.")
        return None, False

    match = re.search(r'/ch(\d+)-(\d+)', target_channel["URL"])
    if not match:
        print(f"  Error: Cannot parse freq/program from URL: {target_channel['URL']}")
        return None, False

    frequency, program = match.groups()
    print(f"  Tuning tuner {tuner_index} → vchannel {vchannel} (Freq:{frequency} Prog:{program})...")

    if not run_command([HDHOMERUN_CONFIG_PATH, HDHOMERUN_IP, "set",
                        f"/tuner{tuner_index}/channel", f"8vsb:{frequency}"]):
        return None, False
    if not run_command([HDHOMERUN_CONFIG_PATH, HDHOMERUN_IP, "set",
                        f"/tuner{tuner_index}/program", program]):
        return None, False

    print(f"  Tuner {tuner_index} locked to vchannel {vchannel}.")
    return tuner_index, True


# =============================================================================
#  Keep-Alive Thread (with auto-recovery)
# =============================================================================

def keep_alive_thread():
    """
    Periodically pings the HDHR to keep it active.
    On failure, attempts full rediscovery and lineup refresh.
    """
    global HDHOMERUN_IP, CHANNELS
    consecutive_failures = 0
    print(f"Keep-alive thread started (interval={KEEP_ALIVE_INTERVAL}s).")

    while True:
        time.sleep(KEEP_ALIVE_INTERVAL)

        current_ip = HDHOMERUN_IP
        try:
            # Touch /sys/version AND /tuner0/status to keep the full device engaged
            subprocess.run(
                [HDHOMERUN_CONFIG_PATH, current_ip, "get", "/sys/version"],
                check=True, capture_output=True, text=True, timeout=10
            )
            subprocess.run(
                [HDHOMERUN_CONFIG_PATH, current_ip, "get", "/tuner0/status"],
                check=True, capture_output=True, text=True, timeout=10
            )
            if consecutive_failures > 0:
                print(f"Keep-alive: recovered after {consecutive_failures} failure(s).")
            consecutive_failures = 0
        except Exception as e:
            consecutive_failures += 1
            print(f"Keep-alive FAILED ({consecutive_failures} consecutive): {e}")
            print("Keep-alive: attempting rediscovery...")
            if wake_and_recover():
                consecutive_failures = 0
            else:
                print(f"Keep-alive: recovery failed. Will retry in {KEEP_ALIVE_INTERVAL}s.")


# =============================================================================
#  HTTP Handler
# =============================================================================

class ProxyHTTPRequestHandler(BaseHTTPRequestHandler):

    # Suppress per-request log lines from BaseHTTPRequestHandler
    def log_message(self, format, *args):
        print(f"[HTTP] {self.address_string()} — {format % args}")

    def do_GET(self):
        parsed_path = urlparse(self.path)

        # --- Health check endpoint ---
        if parsed_path.path == "/health":
            reachable = device_is_reachable(HDHOMERUN_IP, timeout=5)
            status_code = 200 if reachable else 503
            body = json.dumps({
                "status": "ok" if reachable else "device_unreachable",
                "device_ip": HDHOMERUN_IP,
                "tuners": TUNER_COUNT,
                "channels": len(CHANNELS),
            })
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            return

        # --- M3U playlist ---
        if parsed_path.path == "/lineup.m3u":
            self.send_response(200)
            self.send_header("Content-Type", "application/x-mpegURL")
            self.end_headers()
            host = self.headers["Host"]

            with _state_lock:
                channels = list(CHANNELS)

            m3u_content = "#EXTM3U\n"
            for chan in channels:
                m3u_content += (
                    f'#EXTINF:-1 tvg-id="{chan["GuideNumber"]}" '
                    f'tvg-chno="{chan["GuideNumber"]}" '
                    f'tvg-name="{chan["GuideName"]}",{chan["GuideName"]}\n'
                    f'http://{host}/auto/v{chan["GuideNumber"]}\n'
                )
            self.wfile.write(m3u_content.encode("utf-8"))
            return

        # --- Stream a channel ---
        if parsed_path.path.startswith("/auto/v"):
            channel = parsed_path.path.split("v")[-1]
            tuner_index, success = tune_to_channel(channel)
            if not success:
                self.send_response(503)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Failed to tune - device may be unreachable.")
                return

            self.send_response(200)
            self.send_header("Content-Type", "video/mpeg")
            self.end_headers()

            print(f"Streaming tuner {tuner_index} → vchannel {channel}...")
            process = subprocess.Popen(
                [HDHOMERUN_CONFIG_PATH, HDHOMERUN_IP, "save", f"/tuner{tuner_index}", "-"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                while True:
                    chunk = process.stdout.read(STREAM_CHUNK_SIZE)
                    if not chunk:
                        print(f"Stream ended (tuner {tuner_index}) — no more data.")
                        break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                print(f"Client disconnected (tuner {tuner_index}).")
            except Exception as e:
                print(f"Stream error (tuner {tuner_index}): {e}")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                print(f"Stream stopped (tuner {tuner_index}). Releasing tuner...")
                run_command([HDHOMERUN_CONFIG_PATH, HDHOMERUN_IP, "set",
                             f"/tuner{tuner_index}/channel", "none"])
            return

        # --- 404 ---
        self.send_response(404)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Not Found.")


# =============================================================================
#  Graceful Shutdown
# =============================================================================

_httpd = None


def graceful_shutdown(signum, frame):
    sig_name = signal.Signals(signum).name
    print(f"\nReceived {sig_name} — shutting down gracefully...")
    cleanup_tuners()
    if _httpd:
        _httpd.shutdown()
    sys.exit(0)


# =============================================================================
#  Main
# =============================================================================

if __name__ == "__main__":
    # Register signal handlers for clean shutdown
    signal.signal(signal.SIGTERM, graceful_shutdown)
    signal.signal(signal.SIGINT, graceful_shutdown)

    # Step 1: Discover the device (unless HDHOMERUN_IP is set via env)
    if not HDHOMERUN_IP:
        HDHOMERUN_IP = discover_hdhomerun()

    if not HDHOMERUN_IP:
        print("FATAL: Could not discover HDHomeRun device. Exiting.")
        sys.exit(1)

    # Step 2: Fetch config and lineup
    lineup = fetch_device_config_and_lineup(HDHOMERUN_IP)
    if not lineup:
        print("FATAL: Could not fetch channel lineup. Exiting.")
        sys.exit(1)

    CHANNELS = lineup

    # Step 3: Clean up any stale tuner locks from previous runs
    cleanup_tuners()

    # Step 4: Start keep-alive heartbeat (daemon thread)
    heartbeat = threading.Thread(target=keep_alive_thread, daemon=True)
    heartbeat.start()

    # Step 5: Start the HTTP server
    server_address = ("0.0.0.0", PROXY_PORT)
    _httpd = ThreadingHTTPServer(server_address, ProxyHTTPRequestHandler)
    print(f"\n{'='*60}")
    print(f"  HDHomeRun Legacy Proxy v9 (Active Wake & Recovery)")
    print(f"  Device:     {HDHOMERUN_IP}")
    print(f"  Tuners:     {TUNER_COUNT}")
    print(f"  Channels:   {len(CHANNELS)}")
    print(f"  Listening:  http://0.0.0.0:{PROXY_PORT}")
    print(f"  Heartbeat:  every {KEEP_ALIVE_INTERVAL}s")
    print(f"  Endpoints:  /lineup.m3u  /auto/v<channel>  /health")
    print(f"{'='*60}\n")
    _httpd.serve_forever()