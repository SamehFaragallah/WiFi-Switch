# WiFi Controller Dashboard

A Raspberry Pi-based WiFi controller with physical buttons and a modern web dashboard. Control WiFi on/off via physical GPIO buttons or through a web interface with real-time updates.

## Features

- **Physical Button Control**: Two GPIO buttons for WiFi ON/OFF
- **Modern Web Dashboard**: Real-time status updates using WebSockets
- **Auto-off Timer**: Automatically turns WiFi OFF after configurable duration (default: 180 minutes)
- **Configurable Settings**: Adjust auto-off duration via dashboard
- **Button Cooldown**: 5-second cooldown per button to prevent accidental multiple presses
- **SSH Control**: Sends commands to router/AP via SSH
- **Authentication**: Secure login with 30-day session duration
- **Real-time Sync**: All connected dashboards stay synchronized
- **Activity Log**: Track all WiFi state changes
- **Slack Notifications**: Send activity log entries to Slack channel (optional, can be enabled/disabled via dashboard)

## Hardware Requirements

- Raspberry Pi 3B (or compatible)
- 2 x Push buttons connected to GPIO pins:
  - GPIO 23: WiFi ON button
  - GPIO 24: WiFi OFF button
- Pull-up resistors (or use internal pull-ups)

## Software Requirements

- Python 3.7+
- Raspberry Pi OS (Raspbian)
- Network access to router/AP via SSH

## Installation

### 1. Clone or Copy Project

```bash
cd ~
# If you have git:
git clone <your-repo-url> WiFi-Switch
# Or copy the project files to ~/WiFi-Switch
```

### 2. Create Virtual Environment

```bash
cd ~/WiFi-Switch
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure SSH Credentials

Edit `config.py` and update the SSH settings:

```python
CONFIG = {
    'ssh': {
        'host': '192.168.1.1',  # Your router IP
        'port': 22,
        'username': 'admin',     # SSH username
        'password': 'your-password',  # SSH password
        'wifi_on_command': 'your command to enable WiFi',
        'wifi_off_command': 'your command to disable WiFi',
    },
    # ...
}
```

**Common Router Commands:**

- **OpenWRT**:
  - ON: `uci set wireless.radio0.disabled=0 && wifi`
  - OFF: `uci set wireless.radio0.disabled=1 && wifi`
- **DD-WRT**:
  - ON: `nvram set wl0_radio=1 && nvram commit && reboot`
  - OFF: `nvram set wl0_radio=0 && nvram commit && reboot`

### 5. Set File Permissions

```bash
chmod 600 config.py  # Protect credentials
chmod +x wifi_controller.py
```

### 6. Test Manually

```bash
python3 wifi_controller.py
```

Visit `http://raspberry-pi-ip:5000` in your browser.

**Login Credentials:**
- Username: `digitaladmin`
- Password: `WeWillRockyou!`

Press `Ctrl+C` to stop.

## Systemd Service Setup (Auto-start on Boot)

### 1. Update Service File Path

Edit `wifi-controller.service` and update the paths to match your installation:

```ini
WorkingDirectory=/home/pi/WiFi-Switch
ExecStart=/home/pi/WiFi-Switch/venv/bin/python3 /home/pi/WiFi-Switch/wifi_controller.py
```

### 2. Install Service

```bash
sudo cp wifi-controller.service /etc/systemd/system/
sudo systemctl daemon-reload
```

### 3. Enable and Start

```bash
sudo systemctl enable wifi-controller.service
sudo systemctl start wifi-controller.service
```

### 4. Check Status

```bash
sudo systemctl status wifi-controller.service
```

### 5. View Logs

```bash
sudo journalctl -u wifi-controller.service -f
```

## Usage

### Physical Buttons

- **GPIO 23 Button**: Press to turn WiFi ON
- **GPIO 24 Button**: Press to turn WiFi OFF
- **Cooldown**: Each button has a 5-second cooldown after press

### Web Dashboard

1. Open browser and navigate to: `http://raspberry-pi-ip:5000`
2. Login with credentials (username: `digitaladmin`, password: `WeWillRockyou!`)
3. Use the toggle button to turn WiFi ON/OFF
4. View auto-off countdown when WiFi is ON
5. Adjust auto-off duration in Settings panel
6. Monitor activity log for all events

### Auto-off Timer

- When WiFi is turned ON (button or dashboard), auto-off timer starts
- Default duration: 180 minutes (3 hours)
- WiFi will automatically turn OFF when timer expires
- Timer cancels if WiFi manually turned OFF before expiration
- Configure duration via dashboard Settings panel

## Configuration

### Dashboard Settings

Edit `config.py`:

```python
CONFIG = {
    'flask': {
        'host': '0.0.0.0',  # Listen on all interfaces
        'port': 5000,       # Web server port
        'debug': False      # Set True for development only
    },
    'auto_off': {
        'enabled': True,
        'duration_minutes': 180  # 3 hours default
    }
}
```

### Slack Notifications

The WiFi Controller can send activity log notifications to a Slack channel.

#### Setup Instructions

1. **Create a Slack App** (if you haven't already):
   - Go to https://api.slack.com/apps
   - Click "Create New App" → "From scratch"
   - Give it a name (e.g., "WiFi Controller") and select your workspace

2. **Configure Bot Token Scopes**:
   - Navigate to "OAuth & Permissions"
   - Under "Scopes" → "Bot Token Scopes", add:
     - `chat:write` - Send messages to channels
     - `chat:write.public` - Send messages to channels without joining

3. **Install App to Workspace**:
   - Click "Install to Workspace"
   - Authorize the app
   - Copy the "Bot User OAuth Token" (starts with `xoxb-`)

4. **Add Bot to Channel**:
   - In Slack, invite the bot to your desired channel:
     ```
     /invite @WiFi Controller
     ```

5. **Configure in config.py**:
   ```python
   CONFIG = {
       # ... other settings ...
       'slack': {
           'enabled': False,  # Set to True to enable
           'bot_token': 'xoxb-your-bot-token-here',
           'channel_id': 'GRZFEPVDE'  # Your channel ID
       }
   }
   ```

6. **Enable via Dashboard**:
   - Log in to the web dashboard
   - Open "Settings" panel
   - Toggle "Slack Notifications" to Enabled
   - All activity log entries will now be sent to Slack

#### Finding Your Channel ID

To find your Slack channel ID:
1. Right-click the channel name in Slack
2. Select "View channel details"
3. Scroll to the bottom - the Channel ID is shown there

#### Disabling Notifications

You can disable Slack notifications in two ways:
- **Dashboard**: Toggle "Slack Notifications" to Disabled in Settings
- **Config file**: Set `'enabled': False` in config.py

### Change Login Credentials

To change the password:

1. Generate new password hash:
```bash
python3 -c "import bcrypt; print(bcrypt.hashpw(b'YourNewPassword', bcrypt.gensalt()).decode('utf-8'))"
```

2. Update `config.py`:
```python
'dashboard': {
    'username': 'your-username',
    'password': 'YourNewPassword',
    'password_hash': 'paste-bcrypt-hash-here',
    # ...
}
```

### Change GPIO Pins

Edit `wifi_controller.py`:

```python
BUTTON_PIN_ON = 23   # Change to your ON button pin
BUTTON_PIN_OFF = 24  # Change to your OFF button pin
```

## Troubleshooting

### Service Won't Start

```bash
# Check logs
sudo journalctl -u wifi-controller.service -n 50

# Check file permissions
ls -l ~/WiFi-Switch/config.py
ls -l ~/WiFi-Switch/wifi_controller.py

# Test manually
cd ~/WiFi-Switch
source venv/bin/activate
python3 wifi_controller.py
```

### SSH Connection Fails

- Verify router IP address and SSH credentials
- Test SSH connection manually:
```bash
ssh admin@192.168.1.1
```
- Check if SSH is enabled on router
- Verify firewall rules

### Buttons Not Working

- Check GPIO pin numbers in code match your wiring
- Test GPIO pins:
```bash
python3 -c "import RPi.GPIO as GPIO; GPIO.setmode(GPIO.BCM); GPIO.setup(23, GPIO.IN, pull_up_down=GPIO.PUD_UP); print(GPIO.input(23))"
```
- Verify pull-up resistors or internal pull-ups enabled

### Dashboard Not Accessible

- Check if service is running: `sudo systemctl status wifi-controller.service`
- Verify Raspberry Pi IP address: `hostname -I`
- Check firewall: `sudo ufw status`
- Test port: `curl http://localhost:5000`

### WebSocket Connection Issues

- Check browser console for errors (F12)
- Verify Flask-SocketIO version: `pip show Flask-SocketIO`
- Try different browser
- Check for CORS issues in logs

## File Structure

```
WiFi-Switch/
├── wifi_controller.py      # Main application
├── config.py               # Configuration (gitignored)
├── requirements.txt        # Python dependencies
├── .gitignore             # Git ignore rules
├── wifi-controller.service # Systemd service file
├── README.md              # This file
├── main_code.py           # Original code (reference)
├── ssh_example.py         # SSH example (reference)
└── templates/
    ├── index.html         # Dashboard UI
    └── login.html         # Login page
```

## Security Notes

1. **config.py** is in `.gitignore` - never commit credentials
2. Set `chmod 600 config.py` to restrict access
3. Use HTTPS for external access (via cloudflared or reverse proxy)
4. Consider SSH keys instead of passwords
5. Change default dashboard password
6. Keep system updated: `sudo apt update && sudo apt upgrade`

## Integration with Cloudflared

To access dashboard externally via Cloudflare Tunnel:

1. Install cloudflared on Raspberry Pi
2. Create tunnel: `cloudflared tunnel create wifi-controller`
3. Configure tunnel to forward to `localhost:5000`
4. Start tunnel: `cloudflared tunnel run wifi-controller`
5. Access via: `https://your-tunnel-name.trycloudflare.com`

## Future Enhancements

- WiFi network scanning and selection
- Historical state logging to database
- Mobile app integration
- Scheduled WiFi on/off times
- Multiple WiFi networks support
- Email/SMS notifications

## License

MIT License - Feel free to modify and distribute

## Support

For issues or questions, check the logs:
```bash
sudo journalctl -u wifi-controller.service -f
```
