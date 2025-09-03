# HDHomeRun Legacy UDP-to-HTTP Proxy with M3U Generator
import os
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

# --- CONFIGURATION ---
# The path to your hdhomerun_config executable inside the container
HDHOMERUN_CONFIG_PATH = "hdhomerun_config"
# Port for this proxy server to run on
PROXY_PORT = 5004
# Your specific channel lineup (from your original API output)
CHANNELS = [
    {"GuideNumber": "7.1", "GuideName": "WDBJ", "URL": "hdhomerun://103DF11A/ch569000000-3"},
    {"GuideNumber": "7.2", "GuideName": "WDBJ365", "URL": "hdhomerun://103DF11A/ch569000000-4"},
    {"GuideNumber": "7.3", "GuideName": "HEROES", "URL": "hdhomerun://103DF11A/ch569000000-5"},
    {"GuideNumber": "7.4", "GuideName": "CRIME", "URL": "hdhomerun://103DF11A/ch569000000-8"},
    {"GuideNumber": "10.1", "GuideName": "WSLS-HD", "URL": "hdhomerun://103DF11A/ch593000000-3"},
    {"GuideNumber": "10.2", "GuideName": "GetTV", "URL": "hdhomerun://103DF11A/ch593000000-4"},
    {"GuideNumber": "10.3", "GuideName": "MeTV", "URL": "hdhomerun://103DF11A/ch593000000-5"},
    {"GuideNumber": "10.4", "GuideName": "StartTV", "URL": "hdhomerun://103DF11A/ch593000000-6"},
    {"GuideNumber": "10.5", "GuideName": "Movies", "URL": "hdhomerun://103DF11A/ch593000000-7"},
    {"GuideNumber": "13.1", "GuideName": "ABC", "URL": "hdhomerun://103DF11A/ch177000000-3"},
    {"GuideNumber": "13.2", "GuideName": "Charge!", "URL": "hdhomerun://103DF11A/ch177000000-4"},
    {"GuideNumber": "13.3", "GuideName": "Comet", "URL": "hdhomerun://103DF11A/ch177000000-5"},
    {"GuideNumber": "13.4", "GuideName": "ROAR", "URL": "hdhomerun://103DF11A/ch177000000-6"},
    {"GuideNumber": "21.1", "GuideName": "WWCW-HD", "URL": "hdhomerun://103DF11A/ch515000000-3"},
    {"GuideNumber": "21.2", "GuideName": "WFXR-HD", "URL": "hdhomerun://103DF11A/ch515000000-4"},
    {"GuideNumber": "21.3", "GuideName": "Rewind", "URL": "hdhomerun://103DF11A/ch515000000-5"},
    {"GuideNumber": "21.4", "GuideName": "Grit", "URL": "hdhomerun://103DF11A/ch515000000-6"},
    {"GuideNumber": "24.1", "GuideName": "WZBJ24", "URL": "hdhomerun://103DF11A/ch177000000-7"},
    {"GuideNumber": "24.2", "GuideName": "Cozi", "URL": "hdhomerun://103DF11A/ch515000000-7"},
    {"GuideNumber": "24.3", "GuideName": "Decades", "URL": "hdhomerun://103DF11A/ch515000000-8"},
    {"GuideNumber": "24.4", "GuideName": "DABL", "URL": "hdhomerun://103DF11A/ch569000000-6"},
    {"GuideNumber": "27.1", "GuideName": "WFXR-HD", "URL": "hdhomerun://103DF11A/ch605000000-3"},
    {"GuideNumber": "27.2", "GuideName": "WWCW-HD", "URL": "hdhomerun://103DF11A/ch605000000-4"},
    {"GuideNumber": "27.3", "GuideName": "Bounce", "URL": "hdhomerun://103DF11A/ch605000000-5"},
    {"GuideNumber": "27.4", "GuideName": "ANT TV", "URL": "hdhomerun://103DF11A/ch605000000-6"},
    {"GuideNumber": "38.1", "GuideName": "ION", "URL": "hdhomerun://103DF11A/ch551000000-3"},
    {"GuideNumber": "38.2", "GuideName": "CourtTV", "URL": "hdhomerun://103DF11A/ch551000000-4"},
    {"GuideNumber": "38.3", "GuideName": "Laff", "URL": "hdhomerun://103DF11A/ch551000000-5"},
    {"GuideNumber": "38.4", "GuideName": "Mystery", "URL": "hdhomerun://103DF11A/ch551000000-6"},
    {"GuideNumber": "38.5", "GuideName": "IONPlus", "URL": "hdhomerun://103DF11A/ch551000000-7"},
    {"GuideNumber": "38.6", "GuideName": "BUSTED", "URL": "hdhomerun://103DF11A/ch551000000-8"},
    {"GuideNumber": "38.7", "GuideName": "GameSho", "URL": "hdhomerun://103DF11A/ch551000000-9"},
    {"GuideNumber": "38.8", "GuideName": "HSN", "URL": "hdhomerun://103DF11A/ch551000000-10"},
    {"GuideNumber": "38.9", "GuideName": "QVC", "URL": "hdhomerun://103DF11A/ch551000000-11"}
]
# ---------------------

# Read Device ID from environment variable, with a fallback
HDHOMERUN_ID = os.getenv('HDHOMERUN_ID', '103DF11A')

def tune_to_vchannel(channel):
    print(f"Tuning to virtual channel {channel}...")
    try:
        subprocess.run([HDHOMERUN_CONFIG_PATH, HDHOMERUN_ID, "set", "/tuner0/vchannel", channel], check=True, capture_output=True)
        print(f"Successfully tuned to vchannel {channel}.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error tuning to vchannel {channel}: {e.stderr.decode().strip()}")
        return False

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
            channel = parsed_path.path.split('v')[-1]
            if not tune_to_vchannel(channel):
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'Failed to tune HDHomeRun.')
                return

            self.send_response(200)
            self.send_header('Content-Type', 'video/mpeg')
            self.end_headers()

            print(f"Starting stream for vchannel {channel}...")
            process = subprocess.Popen([HDHOMERUN_CONFIG_PATH, HDHOMERUN_ID, "save", "/tuner0", "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                while True:
                    chunk = process.stdout.read(1024 * 128) # Increased buffer size
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except BrokenPipeError:
                print("Client disconnected.")
            finally:
                process.terminate()
                print("Stream stopped.")
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not Found.')

if __name__ == '__main__':
    server_address = ('0.0.0.0', PROXY_PORT)
    httpd = HTTPServer(server_address, ProxyHTTPRequestHandler)
    print(f"HDHomeRun Legacy Proxy started on http://0.0.0.0:{PROXY_PORT}")
    httpd.serve_forever()
