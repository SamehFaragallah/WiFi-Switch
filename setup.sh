#!/bin/bash

# Installation script for Cloudflare Tunnel service
# This script installs and enables the cloudflare-tunnel systemd service

set -e

echo "=============================================="
echo "Installation"
echo "=============================================="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "❌ Error: This script must be run with sudo"
    echo "Usage: sudo ./install_cloudflare_tunnel.sh"
    exit 1
fi

# Get the actual user who ran sudo
ACTUAL_USER=${SUDO_USER:-$USER}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "📁 Script directory: $SCRIPT_DIR"
echo "👤 Installing for user: $ACTUAL_USER"
echo ""


#create venv
echo "🐍 Setting up Python virtual environment..."
python3 -m venv "$SCRIPT_DIR/venv"

# Install required Python packages
echo "📦 Installing required Python packages..."
"$SCRIPT_DIR/venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"


# Make Python script executable
chmod +x "$SCRIPT_DIR/setup1.py"

# Execute the Python script using venv
echo "🐍 Running setup1.py..."
"$SCRIPT_DIR/venv/bin/python" "$SCRIPT_DIR/setup1.py"



# Make Python script executable
chmod +x "$SCRIPT_DIR/wifi_controller.py"


# Copy service file to systemd directory
cp "$SCRIPT_DIR/wifi-controller.service" /etc/systemd/system/

# Reload systemd daemon
systemctl daemon-reload

# Enable service to start on boot
systemctl enable wifi-controller.service

systemctl start wifi-controller.service


# Check if cloudflared is installed
if ! command -v cloudflared &> /dev/null; then
    echo "⚠️  cloudflared is not installed"
    echo "📥 Installing cloudflared..."

    # Detect architecture
    ARCH=$(uname -m)
    case $ARCH in
        armv7l|armhf)
            CLOUDFLARED_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm"
            ;;
        aarch64|arm64)
            CLOUDFLARED_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64"
            ;;
        x86_64|amd64)
            CLOUDFLARED_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
            ;;
        *)
            echo "❌ Unsupported architecture: $ARCH"
            exit 1
            ;;
    esac

    echo "⬇️  Downloading cloudflared from $CLOUDFLARED_URL"
    curl -L "$CLOUDFLARED_URL" -o /usr/local/bin/cloudflared
    chmod +x /usr/local/bin/cloudflared
    echo "✅ cloudflared installed successfully"
else
    echo "✅ cloudflared is already installed at $(which cloudflared)"
fi

echo ""
echo "📋 Installing systemd service..."

# Make Python script executable
chmod +x "$SCRIPT_DIR/cloudflare_tunnel.py"

# Copy service file to systemd directory
cp "$SCRIPT_DIR/cloudflare-tunnel.service" /etc/systemd/system/

# Reload systemd daemon
systemctl daemon-reload

# Enable service to start on boot
systemctl enable cloudflare-tunnel.service

echo "✅ Service installed and enabled"
echo ""

# Ask if user wants to start the service now
echo

systemctl start cloudflare-tunnel.service

echo ""
echo "✅ Service started!"
echo ""
echo "📊 Service Status:"

systemctl status cloudflare-tunnel.service --no-pager -l

echo ""
echo "📝 To view logs: sudo journalctl -u cloudflare-tunnel.service -f"
echo "📄 Tunnel URL will be saved to: /tmp/cloudflared_tunnel_url.txt"


echo ""
echo "=============================================="
echo "Installation Complete!"
echo "=============================================="
echo ""
echo "Useful commands:"
echo "  sudo systemctl status cloudflare-tunnel     # Check service status"
echo "  sudo systemctl start cloudflare-tunnel      # Start service"
echo "  sudo systemctl stop cloudflare-tunnel       # Stop service"
echo "  sudo systemctl restart cloudflare-tunnel    # Restart service"
echo "  sudo journalctl -u cloudflare-tunnel -f     # View live logs"
echo "  cat /tmp/cloudflared_tunnel_url.txt         # Get current tunnel URL"
echo ""
