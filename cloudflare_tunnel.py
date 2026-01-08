#!/usr/bin/env python3

import json
import subprocess
import re
import time
import threading
import signal
import sys
import os

import requests

# ============================================================================
# Configuration
# ============================================================================

CLOUDFLARED_PATH = "/usr/local/bin/cloudflared"  # Adjust if cloudflared is in a different location
TUNNEL_LOG_FILE = "/tmp/cloudflared_tunnel.log"
TUNNEL_URL_FILE = "/tmp/cloudflared_tunnel_url.txt"
LOCAL_PORT = 5000  # The port your WiFi controller dashboard runs on

# ============================================================================
# User-Defined Function - Called when tunnel URL is captured
# ============================================================================

def on_tunnel_ready(tunnel_url):
    """
    This function is called when the cloudflare tunnel URL is ready.

    Args:
        tunnel_url (str): The public HTTPS URL for the tunnel (e.g., https://random-name.trycloudflare.com)

    You can customize this function to:
    - Send notifications
    - Update a database
    - Display on the dashboard
    - Send to a webhook
    - etc.
    """
    print(f"🎉 Cloudflare Tunnel is ready!")
    print(f"📡 Public URL: {tunnel_url}")
    print(f"🔗 Access your dashboard at: {tunnel_url}")

    # Save URL to file for other processes to read
    try:
        with open(TUNNEL_URL_FILE, 'w') as f:
            f.write(tunnel_url)
        print(f"✅ URL saved to {TUNNEL_URL_FILE}")
    except Exception as e:
        print(f"❌ Error saving URL to file: {e}")

    data = {
        "deviceId": "5864",
        "newURL": tunnel_url
    }

    res = requests.post('https://rock.lcbcchurch.com/Webhooks/Lava.ashx/WiFiSwitchAPI', json=data)
    print(json.dumps(res.json(), indent=4))

    # Example: Send to a webhook (uncomment and customize)
    # import requests
    # try:
    #     requests.post('https://your-webhook-url.com/notify', json={'tunnel_url': tunnel_url})
    # except Exception as e:
    #     print(f"Failed to send webhook: {e}")

    # Example: Add to activity log in wifi_controller (if running)
    # You could use a shared file or database to communicate with wifi_controller.py

# ============================================================================
# Cloudflared Tunnel Manager
# ============================================================================

class CloudflareTunnelManager:
    """Manages cloudflared quick tunnel process and URL capture"""

    def __init__(self):
        self.process = None
        self.tunnel_url = None
        self.log_file = None
        self.running = False
        self.url_captured = threading.Event()

    def start_tunnel(self):
        """Start cloudflared tunnel and monitor for URL"""
        print("🚀 Starting Cloudflare Tunnel...")

        # Check if cloudflared is installed
        if not os.path.exists(CLOUDFLARED_PATH):
            print(f"❌ Error: cloudflared not found at {CLOUDFLARED_PATH}")
            print("Install with: curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm -o /usr/local/bin/cloudflared && sudo chmod +x /usr/local/bin/cloudflared")
            return False

        try:
            # Open log file
            self.log_file = open(TUNNEL_LOG_FILE, 'w')

            # Start cloudflared tunnel
            cmd = [
                CLOUDFLARED_PATH,
                'tunnel',
                '--url', f'http://localhost:{LOCAL_PORT}'
            ]

            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )

            self.running = True

            # Start thread to read output and capture URL
            monitor_thread = threading.Thread(target=self._monitor_output, daemon=True)
            monitor_thread.start()

            print(f"✅ Cloudflared process started (PID: {self.process.pid})")
            print(f"📝 Logs: {TUNNEL_LOG_FILE}")

            # Wait for URL to be captured (max 30 seconds)
            if self.url_captured.wait(timeout=30):
                return True
            else:
                print("⚠️  Warning: Tunnel URL not captured within 30 seconds")
                return False

        except Exception as e:
            print(f"❌ Error starting tunnel: {e}")
            return False

    def _monitor_output(self):
        """Monitor cloudflared output for tunnel URL"""
        url_pattern = re.compile(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com')

        try:
            for line in iter(self.process.stdout.readline, ''):
                if not line:
                    break

                # Write to log file
                if self.log_file:
                    self.log_file.write(line)
                    self.log_file.flush()

                # Print to console
                print(f"[cloudflared] {line.strip()}")

                # Search for tunnel URL
                if not self.tunnel_url:
                    match = url_pattern.search(line)
                    if match:
                        self.tunnel_url = match.group(0)
                        print(f"\n{'='*60}")
                        print(f"🎯 TUNNEL URL CAPTURED: {self.tunnel_url}")
                        print(f"{'='*60}\n")

                        # Call user-defined function
                        try:
                            on_tunnel_ready(self.tunnel_url)
                        except Exception as e:
                            print(f"❌ Error in on_tunnel_ready callback: {e}")

                        self.url_captured.set()

        except Exception as e:
            print(f"❌ Error monitoring output: {e}")
        finally:
            if self.log_file:
                self.log_file.close()

    def stop_tunnel(self):
        """Stop the cloudflared tunnel"""
        print("\n🛑 Stopping Cloudflare Tunnel...")
        self.running = False

        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
                print("✅ Tunnel stopped gracefully")
            except subprocess.TimeoutExpired:
                print("⚠️  Force killing tunnel process...")
                self.process.kill()
                self.process.wait()
            except Exception as e:
                print(f"❌ Error stopping tunnel: {e}")

        # Clean up URL file
        try:
            if os.path.exists(TUNNEL_URL_FILE):
                os.remove(TUNNEL_URL_FILE)
        except Exception as e:
            print(f"⚠️  Could not remove URL file: {e}")

    def get_tunnel_url(self):
        """Get the current tunnel URL"""
        return self.tunnel_url

    def wait_for_process(self):
        """Wait for cloudflared process to exit"""
        if self.process:
            self.process.wait()

# ============================================================================
# Signal Handlers for Graceful Shutdown
# ============================================================================

tunnel_manager = None

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    print(f"\n⚠️  Received signal {signum}")
    if tunnel_manager:
        tunnel_manager.stop_tunnel()
    sys.exit(0)

# ============================================================================
# Main
# ============================================================================

def main():
    global tunnel_manager

    print("=" * 60)
    print("Cloudflare Tunnel Manager")
    print("=" * 60)

    # Setup signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Create tunnel manager
    tunnel_manager = CloudflareTunnelManager()

    # Start tunnel
    if tunnel_manager.start_tunnel():
        print(f"\n✅ Tunnel is running. Press Ctrl+C to stop.")

        # Keep running and wait for process to exit
        try:
            tunnel_manager.wait_for_process()
        except KeyboardInterrupt:
            print("\n⚠️  Keyboard interrupt received")
    else:
        print("\n❌ Failed to start tunnel")
        return 1

    # Cleanup
    tunnel_manager.stop_tunnel()
    return 0

if __name__ == "__main__":
    sys.exit(main())
