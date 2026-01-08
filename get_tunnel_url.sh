#!/bin/bash

# Simple script to get the current Cloudflare tunnel URL
# Usage: ./get_tunnel_url.sh

TUNNEL_URL_FILE="/tmp/cloudflared_tunnel_url.txt"

echo "=============================================="
echo "Cloudflare Tunnel URL"
echo "=============================================="
echo ""

if [ -f "$TUNNEL_URL_FILE" ]; then
    TUNNEL_URL=$(cat "$TUNNEL_URL_FILE")
    echo "📡 Current Tunnel URL:"
    echo ""
    echo "  $TUNNEL_URL"
    echo ""
    echo "🔗 Click or copy this URL to access your dashboard from anywhere!"
    echo ""

    # Check if tunnel service is running
    if systemctl is-active --quiet cloudflare-tunnel; then
        echo "✅ Tunnel service is running"
    else
        echo "⚠️  Warning: Tunnel service is not running"
        echo "   Start it with: sudo systemctl start cloudflare-tunnel"
    fi
else
    echo "❌ Tunnel URL file not found at $TUNNEL_URL_FILE"
    echo ""
    echo "Possible reasons:"
    echo "  1. Tunnel service is not running"
    echo "  2. Tunnel is still starting up (wait a few seconds)"
    echo "  3. Tunnel failed to start (check logs)"
    echo ""
    echo "Troubleshooting:"
    echo "  • Check service status: sudo systemctl status cloudflare-tunnel"
    echo "  • View logs: sudo journalctl -u cloudflare-tunnel -f"
    echo "  • Start service: sudo systemctl start cloudflare-tunnel"
fi

echo ""
echo "=============================================="
