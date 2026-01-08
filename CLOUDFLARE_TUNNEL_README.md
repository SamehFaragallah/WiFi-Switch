# Cloudflare Tunnel Setup Guide

This guide explains how to set up a Cloudflare Quick Tunnel for your WiFi Controller Dashboard, allowing you to access it from anywhere on the internet without port forwarding or complex configuration.

## What is Cloudflare Tunnel?

Cloudflare Quick Tunnel creates a secure, temporary public URL that tunnels to your local dashboard running on your Raspberry Pi. The URL looks like `https://random-name.trycloudflare.com`.

## Features

- ✅ Automatic startup on boot
- ✅ Captures and saves tunnel URL
- ✅ Customizable callback function when URL is ready
- ✅ Auto-restart on failure
- ✅ Detailed logging
- ✅ Integration with WiFi Controller service

## Installation

### Step 1: Make the installation script executable

```bash
cd /home/pi/Desktop/WiFi-Switch
chmod +x install_cloudflare_tunnel.sh
```

### Step 2: Run the installation script

```bash
sudo ./install_cloudflare_tunnel.sh
```

This script will:
1. Install cloudflared (if not already installed)
2. Make cloudflare_tunnel.py executable
3. Copy the systemd service file
4. Enable the service to start on boot
5. Optionally start the service immediately

### Step 3: Verify the service is running

```bash
sudo systemctl status cloudflare-tunnel
```

You should see output showing the service is active and running.

### Step 4: Get the tunnel URL

The tunnel URL is automatically saved to a file:

```bash
cat /tmp/cloudflared_tunnel_url.txt
```

Example output: `https://random-example-name.trycloudflare.com`

You can also check the logs:

```bash
sudo journalctl -u cloudflare-tunnel -f
```

## Configuration

### Customize the callback function

Edit [cloudflare_tunnel.py](cloudflare_tunnel.py) and modify the `on_tunnel_ready()` function to customize what happens when the tunnel URL is captured:

```python
def on_tunnel_ready(tunnel_url):
    """
    This function is called when the cloudflare tunnel URL is ready.
    Customize this to suit your needs!
    """
    print(f"🎉 Tunnel ready: {tunnel_url}")

    # Example: Send notification via webhook
    # import requests
    # requests.post('https://your-webhook.com/notify', json={'url': tunnel_url})

    # Example: Send email
    # send_email(f"Dashboard available at {tunnel_url}")

    # Example: Update database
    # db.update_tunnel_url(tunnel_url)
```

### Change the local port

If your dashboard runs on a different port, edit `cloudflare_tunnel.py`:

```python
LOCAL_PORT = 5000  # Change to your port
```

### Change the cloudflared path

If cloudflared is installed in a different location:

```python
CLOUDFLARED_PATH = "/usr/local/bin/cloudflared"  # Change to your path
```

## Integration with WiFi Controller Dashboard

### Option 1: Read URL from file in your Python code

Add this helper function to [wifi_controller.py](wifi_controller.py):

```python
def get_cloudflare_tunnel_url():
    """Read the current Cloudflare tunnel URL from file"""
    tunnel_url_file = '/tmp/cloudflared_tunnel_url.txt'
    try:
        if os.path.exists(tunnel_url_file):
            with open(tunnel_url_file, 'r') as f:
                return f.read().strip()
    except Exception as e:
        print(f"Error reading tunnel URL: {e}")
    return None
```

Then use it anywhere in your code:

```python
tunnel_url = get_cloudflare_tunnel_url()
if tunnel_url:
    print(f"Dashboard accessible at: {tunnel_url}")
    # Add to activity log
    activity_log.add_entry(f"Public URL: {tunnel_url}", source="system")
```

### Option 2: Display tunnel URL in dashboard

Add a section to [templates/index.html](templates/index.html) to display the public URL:

```html
<!-- Public Access Info -->
<div class="bg-green-50 border border-green-200 rounded-lg p-4 mb-4">
    <h3 class="text-lg font-semibold text-green-800 mb-2">
        🌐 Public Access
    </h3>
    <p class="text-sm text-gray-700 mb-2">
        Your dashboard is publicly accessible at:
    </p>
    <div class="bg-white px-3 py-2 rounded border border-green-300">
        <a id="tunnel-url" href="#" target="_blank" class="text-blue-600 hover:underline font-mono text-sm">
            Loading...
        </a>
    </div>
</div>
```

Then add a SocketIO event to send the URL to clients:

```python
@socketio.on('get_tunnel_url')
def handle_get_tunnel_url():
    """Send current tunnel URL to client"""
    tunnel_url = get_cloudflare_tunnel_url()
    emit('tunnel_url_update', {'url': tunnel_url or 'Not available'})
```

And JavaScript to request and display it:

```javascript
// Request tunnel URL on page load
socket.on('connect', () => {
    socket.emit('get_tunnel_url');
});

// Update tunnel URL when received
socket.on('tunnel_url_update', (data) => {
    const urlElement = document.getElementById('tunnel-url');
    if (urlElement && data.url !== 'Not available') {
        urlElement.href = data.url;
        urlElement.textContent = data.url;
    } else if (urlElement) {
        urlElement.textContent = 'Tunnel not running';
        urlElement.classList.remove('text-blue-600', 'hover:underline');
        urlElement.classList.add('text-gray-500');
    }
});
```

## Service Management Commands

```bash
# Start the tunnel
sudo systemctl start cloudflare-tunnel

# Stop the tunnel
sudo systemctl stop cloudflare-tunnel

# Restart the tunnel
sudo systemctl restart cloudflare-tunnel

# Check status
sudo systemctl status cloudflare-tunnel

# View logs (live)
sudo journalctl -u cloudflare-tunnel -f

# View logs (last 50 lines)
sudo journalctl -u cloudflare-tunnel -n 50

# Disable auto-start on boot
sudo systemctl disable cloudflare-tunnel

# Enable auto-start on boot
sudo systemctl enable cloudflare-tunnel
```

## Files Created

- **[cloudflare_tunnel.py](cloudflare_tunnel.py)** - Main Python script that manages the tunnel
- **[cloudflare-tunnel.service](cloudflare-tunnel.service)** - Systemd service file
- **[install_cloudflare_tunnel.sh](install_cloudflare_tunnel.sh)** - Installation script
- **/tmp/cloudflared_tunnel_url.txt** - File containing current tunnel URL (created at runtime)
- **/tmp/cloudflared_tunnel.log** - Tunnel output logs (created at runtime)

## Service Startup Order

The services are configured to start in this order:
1. Network becomes available
2. **cloudflare-tunnel.service** starts (captures URL)
3. **wifi-controller.service** starts (can read tunnel URL)

This ensures the tunnel URL is available when the dashboard starts.

## Security Considerations

⚠️ **Important**: The Cloudflare Quick Tunnel makes your dashboard publicly accessible to anyone with the URL. Make sure:

1. ✅ Your dashboard has strong authentication (already implemented)
2. ✅ Session cookies are secure
3. ✅ Don't share the tunnel URL publicly
4. ⚠️ Quick Tunnels are temporary - the URL changes when the service restarts
5. ⚠️ Consider using Cloudflare Access for additional security layers

For production use, consider:
- Using a **named Cloudflare Tunnel** (requires Cloudflare account)
- Implementing rate limiting
- Adding IP allowlisting
- Using Cloudflare Access for additional authentication

## Troubleshooting

### Tunnel not starting

Check if cloudflared is installed:
```bash
which cloudflared
cloudflared --version
```

If not installed, run:
```bash
sudo ./install_cloudflare_tunnel.sh
```

### URL file not found

Make sure the tunnel service is running:
```bash
sudo systemctl status cloudflare-tunnel
```

Check the logs:
```bash
sudo journalctl -u cloudflare-tunnel -n 50
```

### Port conflict

Make sure port 5000 is available and the WiFi controller is running:
```bash
sudo netstat -tulpn | grep :5000
```

### Tunnel URL keeps changing

Quick Tunnels generate a new random URL each time they start. For a persistent URL, you need to:
1. Create a Cloudflare account
2. Use a named tunnel instead of a quick tunnel
3. Configure the tunnel with a custom hostname

## Advanced: Using Named Tunnels

If you want a permanent URL (like `wifi.yourdomain.com`), you can set up a named Cloudflare Tunnel:

1. Create a Cloudflare account and add your domain
2. Install cloudflared and authenticate:
   ```bash
   cloudflared tunnel login
   ```
3. Create a named tunnel:
   ```bash
   cloudflared tunnel create wifi-controller
   ```
4. Configure DNS and routing
5. Update the service to use the named tunnel

See [Cloudflare Tunnel documentation](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/) for detailed instructions.

## Support

If you encounter issues:
1. Check service status: `sudo systemctl status cloudflare-tunnel`
2. View logs: `sudo journalctl -u cloudflare-tunnel -f`
3. Verify cloudflared is installed: `cloudflared --version`
4. Make sure port 5000 is accessible: `curl http://localhost:5000`

---

**Last Updated**: 2026-01-07
