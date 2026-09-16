# HDHomeRun Legacy UDP-to-HTTP Proxy (v10 — Refactored)
import os
import re
import select
import sys
import json
import signal
import subprocess
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlparse
from urllib.request import urlopen
from urllib.error import URLError

# --- CONFIGURATION (Environment Variable Overrides) ---
HDHOMERUN_CONFIG_PATH = os.environ.get("HDHOMERUN_CONFIG_PATH", "hdhomerun_config")
PROXY_PORT = int(os.environ.get("PROXY_PORT", "5004"))
KEEP_ALIVE_INTERVAL = int(os.environ.get("KEEP_ALIVE_INTERVAL", "60"))  # seconds
WAKE_MAX_RETRIES = int(os.environ.get("WAKE_MAX_RETRIES", "3"))
STREAM_CHUNK_SIZE = int(os.environ.get("STREAM_CHUNK_SIZE", str(1024 * 128)))  # 128KB
STREAM_READ_TIMEOUT = int(os.environ.get("STREAM_READ_TIMEOUT", "30"))  # seconds
MODULATION = os.environ.get("MODULATION", "8vsb")  # 8vsb (ATSC OTA), qam256 (ClearQAM)


# =============================================================================
#  Thread-safe Device State
# =============================================================================

class DeviceState:
    """Thread-safe container for all mutable HDHomeRun device state.

    Replaces the scattered global variables with property accessors that
    always hold the internal lock.  Per-tuner allocation locks prevent the
    TOCTOU race in find-free-tuner → tune.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._ip = os.environ.get("HDHOMERUN_IP")
        self._tuner_count = 0
        self._channels = []
        self._tuner_locks = []  # one Lock per tuner slot

    # -- IP -------------------------------------------------------------------

    @property
    def ip(self):
        with self._lock:
            return self._ip

    @ip.setter
    def ip(self, value):
        with self._lock:
            self._ip = value

    # -- Tuner count ----------------------------------------------------------

    @property
    def tuner_count(self):
        with self._lock:
            return self._tuner_count

    @tuner_count.setter
    def tuner_count(self, value):
        with self._lock:
            if self._tuner_count != value:
                self._tuner_count = value
                self._tuner_locks = [threading.Lock() for _ in range(value)]
            else:
                self._tuner_count = value

    # -- Channels -------------------------------------------------------------

    @property
    def channels(self):
        """Return a shallow copy so callers can iterate without holding the lock."""
        with self._lock:
            return list(self._channels)

    @channels.setter
    def channels(self, value):
        with self._lock:
            self._channels = list(value)

    @property
    def channel_count(self):
        """Number of channels without copying the list."""
        with self._lock:
            return len(self._channels)

    # -- Tuner allocation (per-tuner lock) ------------------------------------

    def acquire_tuner(self):
        """Try to acquire a free tuner.  Returns the tuner index, or None.

        The caller **must** call ``release_tuner()`` when done streaming.
        """
        count = self.tuner_count
        ip = self.ip
        for i in range(count):
            if self._tuner_locks[i].acquire(blocking=False):
                # We hold the slot lock — verify the device agrees it's free.
                try:
                    result = subprocess.run(
                        [HDHOMERUN_CONFIG_PATH, ip, "get", f"/tuner{i}/status"],
                        check=True, capture_output=True, text=True, timeout=5,
                    )
                    if "ch=none" in result.stdout:
                        print(f"  Acquired tuner {i}")
                        return i
                    print(f"  Tuner {i} in use externally, skipping.")
                except Exception:
                    pass
                self._tuner_locks[i].release()

        print("  No free tuners available.")
        return None

    def release_tuner(self, index):
        """Release a previously acquired tuner slot."""
        try:
            self._tuner_locks[index].release()
        except (RuntimeError, IndexError):
            pass  # already released or index out of range


# Singleton state
device = DeviceState()

# Shutdown coordination
_shutdown_event = threading.Event()


# =============================================================================
#  Threading HTTP Server
# =============================================================================

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
        result = subprocess.run(
            [HDHOMERUN_CONFIG_PATH, "discover"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        match = re.search(r'found at (\d+\.\d+\.\d+\.\d+)', result.stdout)
        if match:
            ip = match.group(1)
            print(f"Found HDHomeRun at IP: {ip}")
            return ip
        print(f"Discovery ran but could not parse IP: {result.stdout.strip()}")
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
    if not ip:
        return False
    try:
        result = subprocess.run(
            [HDHOMERUN_CONFIG_PATH, ip, "get", "/sys/version"],
            check=True, capture_output=True, text=True, timeout=timeout,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def wake_and_recover():
    """Attempt to wake / rediscover the HDHomeRun with exponential backoff.

    On success the global IP and channel lineup are updated.
    Returns True if the device is reachable after recovery.
    """
    for attempt in range(1, WAKE_MAX_RETRIES + 1):
        print(f"Wake attempt {attempt}/{WAKE_MAX_RETRIES}: running UDP discovery...")
        new_ip = discover_hdhomerun()
        if new_ip and device_is_reachable(new_ip):
            device.ip = new_ip
            lineup = fetch_device_config_and_lineup(new_ip)
            if lineup:
                device.channels = lineup
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
    """Fetch tuner count and channel lineup from the HDHR's HTTP interface.

    Uses stdlib ``urllib`` — no external dependencies required.
    """
    print("Fetching device config and channel lineup...")
    try:
        discover_url = f"http://{hdhr_ip}/discover.json"
        print(f"  GET {discover_url}")
        with urlopen(discover_url, timeout=30) as resp:
            discover_data = json.loads(resp.read().decode("utf-8"))

        tuner_count = discover_data.get("TunerCount")
        if tuner_count:
            device.tuner_count = int(tuner_count)
            print(f"  Device reports {device.tuner_count} tuner(s).")
        else:
            print("  Warning: TunerCount missing — defaulting to 2.")
            device.tuner_count = 2

        lineup_url = discover_data.get("LineupURL")
        if not lineup_url:
            print("  Error: LineupURL not found in discover.json")
            return None

        print(f"  GET {lineup_url}")
        with urlopen(lineup_url, timeout=30) as resp:
            lineup_data = json.loads(resp.read().decode("utf-8"))
        print(f"  Fetched {len(lineup_data)} channel(s).")
        return lineup_data

    except (URLError, OSError) as e:
        print(f"  Network error fetching lineup: {e}")
        return None
    except (json.JSONDecodeError, KeyError) as e:
        print(f"  JSON parse error: {e}")
        return None


# =============================================================================
#  Tuner Helpers
# =============================================================================

def run_command(command, timeout=10):
    """Run a subprocess command.  Returns True on success, False on failure."""
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


def cleanup_tuners():
    """Release all tuners — called on startup and shutdown to clear stale locks."""
    ip = device.ip
    count = device.tuner_count
    if not ip or count == 0:
        return
    print("Releasing all tuners...")
    for i in range(count):
        try:
            run_command([HDHOMERUN_CONFIG_PATH, ip, "set",
                         f"/tuner{i}/channel", "none"])
        except Exception:
            pass
    print("All tuners released.")


def tune_to_channel(vchannel):
    """Acquire a free tuner and tune it to the given virtual channel.

    If the device is unreachable, attempts a wake/recovery cycle first.
    Returns ``(tuner_index, success)``.  On success the caller **must**
    call ``device.release_tuner(tuner_index)`` and reset the tuner channel
    when done streaming.
    """
    current_ip = device.ip

    # Pre-flight: is the device reachable?
    if not device_is_reachable(current_ip):
        print(f"Device {current_ip} unreachable — attempting wake...")
        if not wake_and_recover():
            return None, False
        current_ip = device.ip  # may have changed after recovery

    tuner_index = device.acquire_tuner()
    if tuner_index is None:
        return None, False

    channels = device.channels

    target_channel = next((c for c in channels if c["GuideNumber"] == vchannel), None)
    if not target_channel:
        print(f"  Error: Virtual channel {vchannel} not in channel list.")
        device.release_tuner(tuner_index)
        return None, False

    url = target_channel.get("URL", "")
    match = re.search(r'/ch(\d+)-(\d+)', url)
    if not match:
        print(f"  Error: Cannot parse freq/program from URL: {url}")
        device.release_tuner(tuner_index)
        return None, False

    frequency, program = match.groups()
    print(f"  Tuning tuner {tuner_index} → vchannel {vchannel} "
          f"(Freq:{frequency} Prog:{program} Mod:{MODULATION})...")

    if not run_command([HDHOMERUN_CONFIG_PATH, current_ip, "set",
                        f"/tuner{tuner_index}/channel", f"{MODULATION}:{frequency}"]):
        device.release_tuner(tuner_index)
        return None, False
    if not run_command([HDHOMERUN_CONFIG_PATH, current_ip, "set",
                        f"/tuner{tuner_index}/program", program]):
        device.release_tuner(tuner_index)
        return None, False

    print(f"  Tuner {tuner_index} locked to vchannel {vchannel}.")
    return tuner_index, True


# =============================================================================
#  Keep-Alive Thread (with auto-recovery)
# =============================================================================

def keep_alive_thread():
    """Periodically pings the HDHR to keep it active.

    On failure, attempts full rediscovery and lineup refresh.
    Uses ``_shutdown_event.wait()`` so the thread exits promptly on shutdown
    instead of sleeping through the full interval.
    """
    consecutive_failures = 0
    print(f"Keep-alive thread started (interval={KEEP_ALIVE_INTERVAL}s).")

    while not _shutdown_event.wait(timeout=KEEP_ALIVE_INTERVAL):
        current_ip = device.ip
        if not current_ip:
            continue
        try:
            # Touch /sys/version AND /tuner0/status to keep the full device engaged
            subprocess.run(
                [HDHOMERUN_CONFIG_PATH, current_ip, "get", "/sys/version"],
                check=True, capture_output=True, text=True, timeout=10,
            )
            subprocess.run(
                [HDHOMERUN_CONFIG_PATH, current_ip, "get", "/tuner0/status"],
                check=True, capture_output=True, text=True, timeout=10,
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

_CHANNEL_RE = re.compile(r'^/auto/v([\d.]+)$')


class ProxyHTTPRequestHandler(BaseHTTPRequestHandler):

    # Suppress default per-request logging; use our own format
    def log_message(self, format, *args):
        print(f"[HTTP] {self.address_string()} — {format % args}")

    def do_GET(self):
        parsed_path = urlparse(self.path)

        # --- Health check ---
        if parsed_path.path == "/health":
            self._handle_health()
            return

        # --- M3U playlist ---
        if parsed_path.path == "/lineup.m3u":
            self._handle_lineup()
            return

        # --- Stream a channel ---
        match = _CHANNEL_RE.match(parsed_path.path)
        if match:
            self._handle_stream(match.group(1))
            return

        # --- 404 ---
        self.send_response(404)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Not Found.")

    # -- /health --------------------------------------------------------------

    def _handle_health(self):
        current_ip = device.ip
        reachable = device_is_reachable(current_ip, timeout=5)
        body = json.dumps({
            "status": "ok" if reachable else "device_unreachable",
            "device_ip": current_ip,
            "tuners": device.tuner_count,
            "channels": device.channel_count,
        }).encode("utf-8")
        self.send_response(200 if reachable else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- /lineup.m3u ----------------------------------------------------------

    def _handle_lineup(self):
        host = self.headers.get("Host", "localhost")
        channels = device.channels

        # Build the body first so we can set Content-Length
        parts = ["#EXTM3U\n"]
        for chan in channels:
            gn = chan["GuideNumber"]
            name = chan["GuideName"]
            parts.append(
                f'#EXTINF:-1 tvg-id="{gn}" '
                f'tvg-chno="{gn}" '
                f'tvg-name="{name}",{name}\n'
                f'http://{host}/auto/v{gn}\n'
            )
        body = "".join(parts).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/x-mpegURL")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- /auto/v<channel> -----------------------------------------------------

    def _handle_stream(self, channel):
        tuner_index, success = tune_to_channel(channel)
        if not success:
            self.send_response(503)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Failed to tune - device may be unreachable.")
            return

        self.send_response(200)
        self.send_header("Content-Type", "video/mpeg")
        self.send_header("Connection", "close")
        self.end_headers()

        current_ip = device.ip
        print(f"Streaming tuner {tuner_index} → vchannel {channel}...")
        process = subprocess.Popen(
            [HDHOMERUN_CONFIG_PATH, current_ip, "save",
             f"/tuner{tuner_index}", "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            fd = process.stdout.fileno()
            while True:
                ready, _, _ = select.select([fd], [], [], STREAM_READ_TIMEOUT)
                if not ready:
                    print(f"Stream timeout (tuner {tuner_index}) — "
                          f"no data for {STREAM_READ_TIMEOUT}s, releasing.")
                    break
                chunk = os.read(fd, STREAM_CHUNK_SIZE)
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
            if process.stdout:
                process.stdout.close()
            print(f"Stream stopped (tuner {tuner_index}). Releasing tuner...")
            run_command([HDHOMERUN_CONFIG_PATH, current_ip, "set",
                         f"/tuner{tuner_index}/channel", "none"])
            device.release_tuner(tuner_index)


# =============================================================================
#  Graceful Shutdown
# =============================================================================

_httpd = None


def graceful_shutdown(signum, _frame):
    """Handle SIGTERM / SIGINT cleanly.

    Sets the shutdown event (so the keep-alive thread exits promptly),
    releases all tuners, and tells the HTTP server to stop accepting.
    ``_httpd.shutdown()`` is called on a separate thread because it blocks
    until ``serve_forever()`` returns — calling it from the signal handler
    on the main thread would deadlock.
    """
    sig_name = signal.Signals(signum).name
    print(f"\nReceived {sig_name} — shutting down gracefully...")
    _shutdown_event.set()
    cleanup_tuners()
    if _httpd:
        threading.Thread(target=_httpd.shutdown, daemon=True).start()


# =============================================================================
#  Main
# =============================================================================

if __name__ == "__main__":
    # Register signal handlers for clean shutdown
    signal.signal(signal.SIGTERM, graceful_shutdown)
    signal.signal(signal.SIGINT, graceful_shutdown)

    # Step 1: Discover the device (unless HDHOMERUN_IP is set via env)
    if not device.ip:
        device.ip = discover_hdhomerun()

    if not device.ip:
        print("FATAL: Could not discover HDHomeRun device. Exiting.")
        sys.exit(1)

    # Step 2: Fetch config and lineup
    lineup = fetch_device_config_and_lineup(device.ip)
    if not lineup:
        print("FATAL: Could not fetch channel lineup. Exiting.")
        sys.exit(1)

    device.channels = lineup

    # Step 3: Clean up any stale tuner locks from previous runs
    cleanup_tuners()

    # Step 4: Start keep-alive heartbeat (daemon thread)
    heartbeat = threading.Thread(target=keep_alive_thread, daemon=True)
    heartbeat.start()

    # Step 5: Start the HTTP server
    _httpd = ThreadingHTTPServer(("0.0.0.0", PROXY_PORT), ProxyHTTPRequestHandler)
    print(f"\n{'='*60}")
    print(f"  HDHomeRun Legacy Proxy v10 (Refactored)")
    print(f"  Device:      {device.ip}")
    print(f"  Tuners:      {device.tuner_count}")
    print(f"  Channels:    {device.channel_count}")
    print(f"  Modulation:  {MODULATION}")
    print(f"  Listening:   http://0.0.0.0:{PROXY_PORT}")
    print(f"  Heartbeat:   every {KEEP_ALIVE_INTERVAL}s")
    print(f"  Endpoints:   /lineup.m3u  /auto/v<channel>  /health")
    print(f"{'='*60}\n")
    _httpd.serve_forever()