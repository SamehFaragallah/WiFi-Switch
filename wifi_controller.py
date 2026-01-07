#!/usr/bin/env python3

import RPi.GPIO as GPIO
import paramiko
import time
import threading
import signal
import sys
import json
import os
from datetime import datetime, timedelta
from flask import Flask, render_template, request, session, redirect, url_for
from flask_socketio import SocketIO, emit
from functools import wraps
from config import CONFIG

# GPIO Pin Configuration
BUTTON_PIN_ON = 23
BUTTON_PIN_OFF = 24

# Global instances (will be initialized in main)
state_manager = None
cooldown_manager = None
auto_off_timer = None
ssh_controller = None
socketio_instance = None
activity_log = None

# ============================================================================
# Thread-Safe State Management Classes
# ============================================================================

class WiFiStateManager:
    """Manages WiFi ON/OFF state with thread-safe operations"""

    def __init__(self):
        self._state = False
        self._lock = threading.RLock()
        self.socketio = None
        self.activity_log = None

    def get_state(self):
        """Get current WiFi state"""
        with self._lock:
            return self._state

    def set_state(self, new_state, source="unknown"):
        """
        Set WiFi state and emit SocketIO event if changed
        Returns True if state changed, False otherwise
        """
        state_changed = False
        with self._lock:
            if self._state != new_state:
                self._state = new_state
                state_changed = True
                print(f"[WiFiStateManager] State changed to {new_state} (source: {source})")

        # Emit SocketIO event and log activity OUTSIDE the lock to avoid blocking
        if state_changed:
            state_text = "ON" if new_state else "OFF"
            source_text = "physical button" if source == "gpio" else "dashboard"

            # Add to activity log (only for real actions, not initial/query)
            if source not in ['initial', 'query'] and self.activity_log:
                self.activity_log.add_entry(f"WiFi turned {state_text} (via {source_text})", source=source)

            # Broadcast to all connected clients
            # Omit 'to' parameter to broadcast to all clients
            if self.socketio:
                try:
                    self.socketio.emit('wifi_state_changed', {
                        'state': new_state,
                        'source': source,
                        'timestamp': datetime.now().isoformat()
                    }, namespace='/')
                    print(f"[WiFiStateManager] Broadcasted state change to all clients")
                except Exception as e:
                    print(f"[WiFiStateManager] Error emitting state change: {e}")
                    import traceback
                    traceback.print_exc()

        return state_changed


class ButtonCooldownManager:
    """Manages per-button cooldown to prevent rapid button presses"""

    def __init__(self, cooldown_seconds=5):
        self._cooldown_seconds = cooldown_seconds
        self._last_press = {}
        self._lock = threading.RLock()

    def can_press(self, pin):
        """Check if button can be pressed (cooldown expired)"""
        with self._lock:
            last_time = self._last_press.get(pin, 0)
            current_time = time.time()
            return (current_time - last_time) >= self._cooldown_seconds

    def register_press(self, pin):
        """Register a button press"""
        with self._lock:
            self._last_press[pin] = time.time()

    def get_remaining_cooldown(self, pin):
        """Get remaining cooldown time in seconds"""
        with self._lock:
            last_time = self._last_press.get(pin, 0)
            elapsed = time.time() - last_time
            remaining = max(0, self._cooldown_seconds - elapsed)
            return remaining


class AutoOffTimer:
    """Manages automatic WiFi off timer with configurable duration"""

    def __init__(self, callback, socketio=None):
        self._timer = None
        self._end_time = None
        self._lock = threading.RLock()
        self._callback = callback
        self._socketio = socketio
        self._countdown_thread = None
        self._stop_countdown = False

    def start(self, duration_minutes):
        """Start auto-off timer"""
        with self._lock:
            self.cancel()
            self._end_time = time.time() + (duration_minutes * 60)
            self._timer = threading.Timer(duration_minutes * 60, self._on_timer_expired)
            self._timer.start()
            self._stop_countdown = False
            print(f"[AutoOffTimer] Started {duration_minutes} minute timer")

            # Start countdown emission thread
            if self._socketio:
                self._countdown_thread = threading.Thread(target=self._emit_countdown_loop)
                self._countdown_thread.daemon = True
                self._countdown_thread.start()

    def cancel(self):
        """Cancel active timer"""
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
                self._end_time = None
                self._stop_countdown = True
                print("[AutoOffTimer] Timer cancelled")

    def get_remaining_seconds(self):
        """Get remaining time in seconds"""
        with self._lock:
            if self._end_time:
                return max(0, int(self._end_time - time.time()))
            return 0

    def is_active(self):
        """Check if timer is active"""
        with self._lock:
            return self._timer is not None

    def _on_timer_expired(self):
        """Called when timer expires"""
        print("[AutoOffTimer] Timer expired, turning WiFi OFF")
        self._callback()
        if self._socketio:
            try:
                self._socketio.emit('auto_off_triggered', {
                    'timestamp': datetime.now().isoformat()
                }, namespace='/')
            except Exception as e:
                print(f"[AutoOffTimer] Error emitting auto_off_triggered: {e}")

    def _emit_countdown_loop(self):
        """Emit countdown updates every 60 seconds"""
        while not self._stop_countdown:
            remaining = self.get_remaining_seconds()
            if remaining <= 0:
                break

            if self._socketio:
                try:
                    self._socketio.emit('auto_off_countdown', {
                        'remaining_seconds': remaining,
                        'remaining_minutes': remaining // 60
                    }, namespace='/')
                except Exception as e:
                    print(f"[AutoOffTimer] Error emitting countdown: {e}")

            time.sleep(60)


class SSHController:
    """Handles SSH connections and command execution to router/AP"""

    def __init__(self, config):
        self.config = config

    def execute_command(self, command):
        """Execute SSH command on remote host"""
        # Check if SSH is enabled (for testing)
        if not self.config.get('enabled', True):
            print(f"[SSHController] SSH disabled (test mode), would execute: {command}")
            return True, "SSH disabled (test mode)"

        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            ssh.connect(
                self.config['host'],
                port=self.config.get('port', 22),
                username=self.config['username'],
                password=self.config['password'],
                timeout=10
            )

            stdin, stdout, stderr = ssh.exec_command(command)
            output = stdout.read().decode('utf-8')
            error = stderr.read().decode('utf-8')

            ssh.close()

            if error:
                print(f"[SSHController] Command error: {error}")
                return False, error

            print(f"[SSHController] Command executed: {command}")
            return True, output

        except Exception as e:
            print(f"[SSHController] SSH error: {str(e)}")
            return False, str(e)

    def set_wifi_on(self):
        """Turn WiFi ON via SSH"""
        # Check if SSH is enabled
        if not self.config.get('enabled', True):
            print("[SSHController] SSH disabled (test mode), would turn WiFi ON")
            return True, "SSH disabled (test mode)"

        command = self.config.get('wifi_on_command', '')
        if not command or command.startswith('#'):
            print("[SSHController] WiFi ON command not configured (placeholder)")
            return True, "Command not configured"
        return self.execute_command(command)

    def set_wifi_off(self):
        """Turn WiFi OFF via SSH"""
        # Check if SSH is enabled
        if not self.config.get('enabled', True):
            print("[SSHController] SSH disabled (test mode), would turn WiFi OFF")
            return True, "SSH disabled (test mode)"

        command = self.config.get('wifi_off_command', '')
        if not command or command.startswith('#'):
            print("[SSHController] WiFi OFF command not configured (placeholder)")
            return True, "Command not configured"
        return self.execute_command(command)


class ActivityLog:
    """Manages global activity log with persistence across client connections and script restarts"""

    def __init__(self, max_entries=25, socketio=None, log_file='activity_log.json'):
        self._max_entries = max_entries
        self._entries = []
        self._lock = threading.RLock()
        self._socketio = socketio
        self._log_file = log_file
        # Load existing entries from file
        self._load_from_file()

    def _load_from_file(self):
        """Load activity log entries from persistent storage"""
        if os.path.exists(self._log_file):
            try:
                with open(self._log_file, 'r') as f:
                    loaded_entries = json.load(f)
                    with self._lock:
                        # Keep only the most recent entries up to max_entries
                        self._entries = loaded_entries[-self._max_entries:]
                    print(f"[ActivityLog] Loaded {len(self._entries)} entries from {self._log_file}")
            except Exception as e:
                print(f"[ActivityLog] Error loading from file: {e}")
                self._entries = []

    def _save_to_file(self):
        """Save activity log entries to persistent storage"""
        try:
            with self._lock:
                entries_to_save = list(self._entries)
            
            with open(self._log_file, 'w') as f:
                json.dump(entries_to_save, f, indent=2)
        except Exception as e:
            print(f"[ActivityLog] Error saving to file: {e}")

    def add_entry(self, message, source="system"):
        """Add an entry to the activity log and broadcast to all clients"""
        entry = None
        with self._lock:
            entry = {
                'message': message,
                'source': source,
                'timestamp': datetime.now().isoformat()
            }
            self._entries.append(entry)
            # Keep only last max_entries
            if len(self._entries) > self._max_entries:
                self._entries = self._entries[-self._max_entries:]
            print(f"[ActivityLog] {message}")

        # Save to file (outside lock to avoid blocking)
        try:
            self._save_to_file()
        except Exception as e:
            print(f"[ActivityLog] Error saving entry: {e}")

        # Broadcast to all clients OUTSIDE the lock
        # Omit 'to' parameter to broadcast to all clients
        if entry and self._socketio:
            try:
                self._socketio.emit('activity_log_entry', entry, namespace='/')
            except Exception as e:
                print(f"[ActivityLog] Error broadcasting entry: {e}")
                import traceback
                traceback.print_exc()

    def get_entries(self):
        """Get all log entries"""
        with self._lock:
            return list(self._entries)


# ============================================================================
# Flask Application Setup
# ============================================================================

app = Flask(__name__)
app.config['SECRET_KEY'] = CONFIG['dashboard']['secret_key']
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')


# ============================================================================
# Authentication
# ============================================================================

def login_required(f):
    """Decorator to require login for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # Validate credentials (plain password comparison)
        if (username == CONFIG['dashboard']['username'] and
            password == CONFIG['dashboard']['password']):
            session['authenticated'] = True
            session.permanent = True
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='Invalid credentials')

    return render_template('login.html')


@app.route('/logout')
def logout():
    """Logout and clear session"""
    session.clear()
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    """Main dashboard"""
    return render_template('index.html')


# ============================================================================
# SocketIO Event Handlers
# ============================================================================

@socketio.on('connect')
def handle_connect():
    """Client connected"""
    print(f"[SocketIO] Client connected")
    # Send current state immediately
    emit('wifi_state_changed', {
        'state': state_manager.get_state(),
        'source': 'initial',
        'timestamp': datetime.now().isoformat()
    })

    # Send timer info if active
    if auto_off_timer.is_active():
        emit('auto_off_countdown', {
            'remaining_seconds': auto_off_timer.get_remaining_seconds(),
            'remaining_minutes': auto_off_timer.get_remaining_seconds() // 60
        })

    # Send current settings without notification
    emit('current_settings', {
        'auto_off_duration_minutes': CONFIG['auto_off']['duration_minutes'],
        'ssh_enabled': CONFIG['ssh'].get('enabled', True)
    })

    # Send activity log history
    if activity_log:
        emit('activity_log_history', {
            'entries': activity_log.get_entries()
        })


@socketio.on('disconnect')
def handle_disconnect():
    """Client disconnected"""
    print("[SocketIO] Client disconnected")


@socketio.on('get_current_state')
def handle_get_current_state():
    """Send current WiFi state and timer info"""
    emit('wifi_state_changed', {
        'state': state_manager.get_state(),
        'source': 'query',
        'timestamp': datetime.now().isoformat()
    })

    if auto_off_timer.is_active():
        emit('auto_off_countdown', {
            'remaining_seconds': auto_off_timer.get_remaining_seconds(),
            'remaining_minutes': auto_off_timer.get_remaining_seconds() // 60
        })


@socketio.on('toggle_wifi')
def handle_toggle_wifi(data):
    """Handle WiFi toggle request from dashboard"""
    desired_state = data.get('desired_state', False)
    print(f"[SocketIO] Toggle WiFi request: {desired_state}")

    try:
        print(f"[SocketIO] Setting state to {desired_state}")
        # Update state
        if state_manager.set_state(desired_state, source='dashboard'):
            print(f"[SocketIO] State changed successfully")
            # Execute SSH command
            if desired_state:
                print(f"[SocketIO] Calling set_wifi_on()")
                success, message = ssh_controller.set_wifi_on()
                print(f"[SocketIO] set_wifi_on() returned: {success}, {message}")
                if not success:
                    emit('ssh_error', {
                        'error': f'Failed to turn WiFi ON: {message}',
                        'timestamp': datetime.now().isoformat()
                    })

                # Start auto-off timer
                print(f"[SocketIO] Starting auto-off timer")
                if CONFIG['auto_off']['enabled']:
                    auto_off_timer.start(CONFIG['auto_off']['duration_minutes'])
                print(f"[SocketIO] Auto-off timer started")
            else:
                print(f"[SocketIO] Calling set_wifi_off()")
                success, message = ssh_controller.set_wifi_off()
                print(f"[SocketIO] set_wifi_off() returned: {success}, {message}")
                if not success:
                    emit('ssh_error', {
                        'error': f'Failed to turn WiFi OFF: {message}',
                        'timestamp': datetime.now().isoformat()
                    })

                # Cancel auto-off timer
                print(f"[SocketIO] Cancelling auto-off timer")
                auto_off_timer.cancel()
                print(f"[SocketIO] Auto-off timer cancelled")

        print(f"[SocketIO] Toggle WiFi completed successfully")
    except Exception as e:
        print(f"[SocketIO] Error in toggle_wifi: {str(e)}")
        import traceback
        traceback.print_exc()
        emit('ssh_error', {
            'error': f'Error toggling WiFi: {str(e)}',
            'timestamp': datetime.now().isoformat()
        })


@socketio.on('update_auto_off_duration')
def handle_update_auto_off_duration(data):
    """Update auto-off duration setting"""
    duration_minutes = data.get('duration_minutes', 180)
    print(f"[SocketIO] Update auto-off duration to {duration_minutes} minutes")

    # Update config
    CONFIG['auto_off']['duration_minutes'] = duration_minutes

    # Save to file
    try:
        with open('config.py', 'w') as f:
            f.write(f"CONFIG = {json.dumps(CONFIG, indent=4)}\n")

        # Add to activity log
        if activity_log:
            activity_log.add_entry(f"Auto-off duration updated to {duration_minutes} minutes", source="dashboard")

        # Broadcast settings update to all clients
        socketio.emit('settings_updated', {
            'auto_off_duration_minutes': duration_minutes
        }, namespace='/')
    except Exception as e:
        emit('ssh_error', {
            'error': f'Failed to save settings: {str(e)}',
            'timestamp': datetime.now().isoformat()
        })


# ============================================================================
# GPIO Monitoring
# ============================================================================

def gpio_loop():
    """GPIO monitoring loop (runs in separate thread)"""
    global state_manager, cooldown_manager, auto_off_timer, ssh_controller

    # Initialize GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(BUTTON_PIN_ON, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(BUTTON_PIN_OFF, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    prevState_on = GPIO.input(BUTTON_PIN_ON)
    prevState_off = GPIO.input(BUTTON_PIN_OFF)

    print("[GPIO] Monitoring started")

    try:
        while True:
            time.sleep(0.01)

            # Check ON button (GPIO 23)
            buttonState_on = GPIO.input(BUTTON_PIN_ON)
            if buttonState_on != prevState_on:
                prevState_on = buttonState_on

                if buttonState_on == 0:  # Button pressed
                    if cooldown_manager.can_press(BUTTON_PIN_ON):
                        cooldown_manager.register_press(BUTTON_PIN_ON)
                        print("[GPIO] ON button pressed")

                        # Turn WiFi ON
                        if state_manager.set_state(True, source='gpio'):
                            success, message = ssh_controller.set_wifi_on()
                            if not success and socketio_instance:
                                try:
                                    socketio_instance.emit('ssh_error', {
                                        'error': f'Failed to turn WiFi ON: {message}',
                                        'timestamp': datetime.now().isoformat()
                                    }, namespace='/')
                                except Exception as e:
                                    print(f"[GPIO] Error emitting ssh_error: {e}")

                            # Start auto-off timer
                            if CONFIG['auto_off']['enabled']:
                                auto_off_timer.start(CONFIG['auto_off']['duration_minutes'])
                    else:
                        remaining = cooldown_manager.get_remaining_cooldown(BUTTON_PIN_ON)
                        print(f"[GPIO] ON button on cooldown ({remaining:.1f}s remaining)")

            # Check OFF button (GPIO 24)
            buttonState_off = GPIO.input(BUTTON_PIN_OFF)
            if buttonState_off != prevState_off:
                prevState_off = buttonState_off

                if buttonState_off == 0:  # Button pressed
                    if cooldown_manager.can_press(BUTTON_PIN_OFF):
                        cooldown_manager.register_press(BUTTON_PIN_OFF)
                        print("[GPIO] OFF button pressed")

                        # Turn WiFi OFF
                        if state_manager.set_state(False, source='gpio'):
                            success, message = ssh_controller.set_wifi_off()
                            if not success and socketio_instance:
                                try:
                                    socketio_instance.emit('ssh_error', {
                                        'error': f'Failed to turn WiFi OFF: {message}',
                                        'timestamp': datetime.now().isoformat()
                                    }, namespace='/')
                                except Exception as e:
                                    print(f"[GPIO] Error emitting ssh_error: {e}")

                            # Cancel auto-off timer
                            auto_off_timer.cancel()
                    else:
                        remaining = cooldown_manager.get_remaining_cooldown(BUTTON_PIN_OFF)
                        print(f"[GPIO] OFF button on cooldown ({remaining:.1f}s remaining)")

    except Exception as e:
        print(f"[GPIO] Error: {str(e)}")
    finally:
        GPIO.cleanup()


# ============================================================================
# Main Application
# ============================================================================

def auto_off_callback():
    """Callback function for auto-off timer expiration"""
    print("[Main] Auto-off timer expired, turning WiFi OFF")
    state_manager.set_state(False, source='auto-off')
    ssh_controller.set_wifi_off()
    if activity_log:
        activity_log.add_entry("WiFi automatically turned OFF (timer expired)", source="auto-off")


def signal_handler(sig, frame):
    """Handle shutdown signals"""
    print("\n[Main] Shutting down gracefully...")
    auto_off_timer.cancel()
    # Save activity log before exiting
    if activity_log:
        print("[Main] Saving activity log...")
        activity_log._save_to_file()
    GPIO.cleanup()
    sys.exit(0)


def main():
    """Main application entry point"""
    global state_manager, cooldown_manager, auto_off_timer, ssh_controller, socketio_instance, activity_log

    print("=" * 60)
    print("WiFi Controller Dashboard")
    print("=" * 60)

    # Initialize components
    activity_log = ActivityLog(max_entries=25, socketio=socketio)

    state_manager = WiFiStateManager()
    state_manager.socketio = socketio
    state_manager.activity_log = activity_log

    cooldown_manager = ButtonCooldownManager(cooldown_seconds=5)

    auto_off_timer = AutoOffTimer(callback=auto_off_callback, socketio=socketio)

    ssh_controller = SSHController(CONFIG['ssh'])

    # Display SSH status
    if CONFIG['ssh'].get('enabled', True):
        print("[Main] SSH: ENABLED - Will execute commands on router")
    else:
        print("[Main] SSH: DISABLED (Test Mode) - Commands will be logged but not executed")

    socketio_instance = socketio

    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start GPIO monitoring in a separate thread
    # GPIO operations are blocking and should run in a regular thread, not eventlet
    gpio_thread = threading.Thread(target=gpio_loop)
    gpio_thread.daemon = True
    gpio_thread.start()

    print(f"[Main] Dashboard starting on {CONFIG['flask']['host']}:{CONFIG['flask']['port']}")
    print(f"[Main] Login credentials: {CONFIG['dashboard']['username']} / {CONFIG['dashboard']['password']}")
    print("[Main] Press Ctrl+C to stop")

    # Start Flask-SocketIO server
    socketio.run(
        app,
        host=CONFIG['flask']['host'],
        port=CONFIG['flask']['port'],
        debug=CONFIG['flask']['debug']
    )


if __name__ == '__main__':
    main()
