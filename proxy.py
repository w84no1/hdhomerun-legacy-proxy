# HDHomeRun Legacy UDP-to-HTTP Proxy (v5.1 - Increased Timeout)
import os
import re
import sys
import json
import subprocess
import requests 
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

# --- CONFIGURATION ---
HDHOMERUN_CONFIG_PATH = "hdhomerun_config"
PROXY_PORT = 5004
TUNER_COUNT = 2 
CHANNELS = []
# ---------------------

HDHOMERUN_IP = os.getenv('HDHOMERUN_IP')

def fetch_channel_lineup(hdhr_ip):
    """Connects to the HDHomeRun to discover and fetch the channel lineup."""
    print("Attempting to fetch channel lineup...")
    try:
        # Step 1: Discover the lineup URL
        discover_url = f"http://{hdhr_ip}/discover.json"
        print(f"Fetching discover URL: {discover_url}")
        # Increased timeout from 5 to 30 seconds
        discover_resp = requests.get(discover_url, timeout=30) 
        discover_resp.raise_for_status()
        discover_data = discover_resp.json()
        lineup_url = discover_data.get('LineupURL')

        if not lineup_url:
            print("Error: LineupURL not found in discover.json")
            return None

        # Step 2: Fetch the actual lineup from the URL
        print(f"Fetching lineup URL: {lineup_url}")
        # Increased timeout from 5 to 30 seconds
        lineup_resp = requests.get(lineup_url, timeout=30) 
        lineup_resp.raise_for_status()
        lineup_data = lineup_resp.json()
        
        print(f"Successfully fetched {len(lineup_data)} channels.")
        return lineup_data

    except requests.exceptions.RequestException as e:
        print(f"Error: Network error fetching lineup: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"Error: Could not parse JSON response: {e}")
        return None

# --- The rest of the script is identical to the previous version ---

def find_free_tuner():
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
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        error_output = e.stderr.strip() if e.stderr else "No error output from command."
        print(f"Command failed: {' '.join(command)}\nError: {error_output}")
        return False

def tune_to_channel(vchannel):
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
            if not HDHOMERUN_IP:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'HDHOMERUN_IP environment variable not set on server.')
                return

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
            except BrokenPipeError:
                print("Client disconnected.")
            finally:
                process.terminate()
                print("Stream stopped.")
                run_command([HDHOMERUN_CONFIG_PATH, HDHOMERUN_IP, "set", f"/tuner{tuner_index}/channel", "none"])
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not Found.')

if __name__ == '__main__':
    if not HDHOMERUN_IP:
        print("FATAL ERROR: HDHOMERUN_IP environment variable is not set. Exiting.")
        sys.exit(1)
    
    lineup = fetch_channel_lineup(HDHOMERUN_IP)
    if lineup:
        CHANNELS = lineup
        server_address = ('0.0.0.0', PROXY_PORT)
        httpd = HTTPServer(server_address, ProxyHTTPRequestHandler)
        print(f"HDHomeRun Legacy Proxy (v5.1) started on http://0.0.0.0:{PROXY_PORT}")
        print(f"Targeting HDHomeRun at IP: {HDHOMERUN_IP}")
        httpd.serve_forever()
    else:
        print("FATAL ERROR: Could not fetch channel lineup. Exiting.")
        sys.exit(1)