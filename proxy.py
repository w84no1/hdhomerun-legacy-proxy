# HDHomeRun Legacy UDP-to-HTTP Proxy (v8 - Keep-Alive Heartbeat)
import os
import re
import sys
import json
import subprocess
import requests
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlparse

# --- CONFIGURATION ---
HDHOMERUN_CONFIG_PATH = "hdhomerun_config"
PROXY_PORT = 5004
# Set the interval in seconds for the keep-alive ping
KEEP_ALIVE_INTERVAL = 300  # 5 minutes
# These will now be discovered automatically
TUNER_COUNT = 0 
CHANNELS = []
HDHOMERUN_IP = None
# ---------------------

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in a separate thread."""
    pass

def keep_alive_thread():
    """Periodically pings the HDHomeRun to keep the connection alive."""
    print("Keep-alive thread started.")
    while True:
        time.sleep(KEEP_ALIVE_INTERVAL)
        print("Keep-alive: Sending status ping to HDHomeRun...")
        try:
            # Use a lightweight command that doesn't require a tuner lock
            status_cmd = [HDHOMERUN_CONFIG_PATH, HDHOMERUN_IP, "get", "/sys/version"]
            subprocess.run(status_cmd, check=True, capture_output=True, text=True, timeout=10)
            print("Keep-alive: Ping successful.")
        except Exception as e:
            print(f"Keep-alive: Ping failed. Will retry in {KEEP_ALIVE_INTERVAL} seconds. Error: {e}")

def discover_hdhomerun():
    # ... (This function is identical to v7.1) ...
    print("Discovering HDHomeRun device on the network...")
    try:
        discover_cmd = [HDHOMERUN_CONFIG_PATH, "discover"]
        result = subprocess.run(discover_cmd, check=True, capture_output=True, text=True, timeout=10)
        match = re.search(r'found at (\d+\.\d+\.\d+\.\d+)', result.stdout)
        if match:
            ip_address = match.group(1)
            print(f"Found HDHomeRun at IP: {ip_address}")
            return ip_address
        else:
            print("Error: Discovery ran, but could not parse IP address from output:")
            print(result.stdout)
            return None
    except FileNotFoundError:
        print(f"Error: '{HDHOMERUN_CONFIG_PATH}' command not found. Is it installed in the container?")
        return None
    except subprocess.TimeoutExpired:
        print("Error: Discovery command timed out. No HDHomeRun devices found on the network.")
        return None
    except subprocess.CalledProcessError as e:
        print(f"Error: Discovery command failed: {e.stderr}")
        return None

def fetch_device_config_and_lineup(hdhr_ip):
    # ... (This function is identical to v7.1) ...
    global TUNER_COUNT
    print("Attempting to fetch device config and channel lineup...")
    try:
        discover_url = f"http://{hdhr_ip}/discover.json"
        print(f"Fetching discover URL: {discover_url}")
        discover_resp = requests.get(discover_url, timeout=30)
        discover_resp.raise_for_status()
        discover_data = discover_resp.json()
        tuner_count = discover_data.get('TunerCount')
        if tuner_count:
            TUNER_COUNT = int(tuner_count)
            print(f"Device reports {TUNER_COUNT} tuners.")
        else:
            print("Warning: Could not determine tuner count. Defaulting to 2.")
            TUNER_COUNT = 2
        lineup_url = discover_data.get('LineupURL')
        if not lineup_url:
            print("Error: LineupURL not found in discover.json")
            return None
        print(f"Fetching lineup URL: {lineup_url}")
        lineup_resp = requests.get(lineup_url, timeout=30)
        lineup_resp.raise_for_status()
        lineup_data = lineup_resp.json()
        print(f"Successfully fetched {len(lineup_data)} channels.")
        return lineup_data
    except requests.exceptions.RequestException as e:
        print(f"Error: Network error fetching lineup: {e}")
        return None
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Error: Could not parse JSON response: {e}")
        return None

def find_free_tuner():
    # ... (This function is identical to v7.1) ...
    for i in range(TUNER_COUNT):
        try:
            status_cmd = [HDHOMERUN_CONFIG_PATH, HDHOMERUN_IP, "get", f"/tuner{i}/status"]
            result = subprocess.run(status_cmd, check=True, capture_output=True, text=True)
            if "ch=none" in result.stdout:
                print(f"Found free tuner: {i}")
                return i
        except subprocess.CalledProcessError:
            continue
    print("Error: No free tuners available.")
    return None

def run_command(command):
    # ... (This function is identical to v7.1) ...
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        error_output = e.stderr.strip() if e.stderr else "No error output from command."
        print(f"Command failed: {' '.join(command)}\nError: {error_output}")
        return False

def tune_to_channel(vchannel):
    # ... (This function is identical to v7.1) ...
    tuner_index = find_free_tuner()
    if tuner_index is None:
        return None, False
    target_channel = next((c for c in CHANNELS if c['GuideNumber'] == vchannel), None)
    if not target_channel:
        print(f"Error: Virtual channel {vchannel} not found in channel list.")
        return None, False
    match = re.search(r'/ch(\d+)-(\d+)', target_channel['URL'])
    if not match:
        print(f"Error: Could not parse frequency/program from URL for channel {vchannel}: {target_channel['URL']}")
        return None, False
    frequency, program = match.groups()
    print(f"Using tuner {tuner_index} to tune {HDHOMERUN_IP} to vchannel {vchannel} (Freq: {frequency}, Prog: {program})...")
    if not run_command([HDHOMERUN_CONFIG_PATH, HDHOMERUN_IP, "set", f"/tuner{tuner_index}/channel", f"8vsb:{frequency}"]):
        return None, False
    if not run_command([HDHOMERUN_CONFIG_PATH, HDHOMERUN_IP, "set", f"/tuner{tuner_index}/program", program]):
        return None, False
    print(f"Successfully tuned tuner {tuner_index} to vchannel {vchannel}.")
    return tuner_index, True

class ProxyHTTPRequestHandler(BaseHTTPRequestHandler):
    # ... (This class is identical to v7.1) ...
    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == '/lineup.m3u':
            self.send_response(200)
            self.send_header('Content-Type', 'application/x-mpegURL')
            self.end_headers()
            host = self.headers['Host']
            m3u_content = "#EXTM3U\n"
            for chan in CHANNELS:
                m3u_content += f'#EXTINF:-1 tvg-id="{chan["GuideNumber"]}" tvg-chno="{chan["GuideNumber"]}" tvg-name="{chan["GuideName"]}",{chan["GuideName"]}\n'
                m3u_content += f'http://{host}/auto/v{chan["GuideNumber"]}\n'
            self.wfile.write(m3u_content.encode('utf-8'))
            return

        if parsed_path.path.startswith('/auto/v'):
            channel = parsed_path.path.split('v')[-1]
            tuner_index, success = tune_to_channel(channel)
            if not success:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'Failed to find or tune a free tuner.')
                return

            self.send_response(200)
            self.send_header('Content-Type', 'video/mpeg')
            self.end_headers()

            print(f"Starting stream from tuner {tuner_index} for vchannel {channel}...")
            process = subprocess.Popen([HDHOMERUN_CONFIG_PATH, HDHOMERUN_IP, "save", f"/tuner{tuner_index}", "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                while True:
                    chunk = process.stdout.read(1024 * 128)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                print(f"Client disconnected gracefully (Tuner {tuner_index}).")
            finally:
                process.terminate()
                print(f"Stream stopped (Tuner {tuner_index}).")
                run_command([HDHOMERUN_CONFIG_PATH, HDHOMERUN_IP, "set", f"/tuner{tuner_index}/channel", "none"])
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not Found.')

if __name__ == '__main__':
    HDHOMERUN_IP = discover_hdhomerun()
    if HDHOMERUN_IP:
        lineup = fetch_device_config_and_lineup(HDHOMERUN_IP)
        if lineup:
            CHANNELS = lineup
            
            # Start the keep-alive thread in the background
            heartbeat = threading.Thread(target=keep_alive_thread)
            heartbeat.daemon = True  # Allows main program to exit even if thread is running
            heartbeat.start()

            server_address = ('0.0.0.0', PROXY_PORT)
            httpd = ThreadingHTTPServer(server_address, ProxyHTTPRequestHandler)
            print(f"HDHomeRun Legacy Proxy (v8 - Heartbeat) started on http://0.0.0.0:{PROXY_PORT}")
            httpd.serve_forever()
        else:
            print("FATAL ERROR: Could not fetch channel lineup. Exiting.")
            sys.exit(1)
    else:
        print("FATAL ERROR: Could not discover HDHomeRun device. Exiting.")
        sys.exit(1)