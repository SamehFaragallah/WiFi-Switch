#!/usr/bin/env python3

import RPi.GPIO as GPIO
import paramiko
import time
import threading
import signal
import sys
import json
import os
import queue
import hashlib
from datetime import datetime, timedelta
from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from functools import wraps
from config import CONFIG

# GPIO Pin Configuration
BUTTON_PIN_ON = 23
BUTTON_PIN_OFF = 24

LED_STATUS = 27
LED_ALWAYS_ON = 17
LED_SCHEDULED = 22

# Global instances (will be initialized in main)
state_manager = None
cooldown_manager = None
auto_off_timer = None
ssh_controller = None
socketio_instance = None
activity_log = None
led_controller = None
wifi_scheduler = None
# Thread-safe queue for cross-thread SocketIO emits
emit_queue = None

# ============================================================================
# Authentication Token Management
# ============================================================================

class AuthTokenManager:
    """Manages trusted devices for persistent authentication across URL changes"""

    def __init__(self, device_file='trusted_devices.json'):
        self._device_file = device_file
        self._trusted_devices = {}
        self._lock = threading.RLock()
        self._load_trusted_devices()

    def _load_trusted_devices(self):
        """Load trusted devices from persistent storage"""
        if os.path.exists(self._device_file):
            try:
                with open(self._device_file, 'r') as f:
                    data = json.load(f)
                    with self._lock:
                        self._trusted_devices = data
                    print(f"[AuthTokenManager] Loaded {len(self._trusted_devices)} trusted devices from {self._device_file}")
            except Exception as e:
                print(f"[AuthTokenManager] Error loading trusted devices: {e}")
                self._trusted_devices = {}

    def _save_trusted_devices(self):
        """Save trusted devices to persistent storage"""
        try:
            with self._lock:
                devices_to_save = dict(self._trusted_devices)

            with open(self._device_file, 'w') as f:
                json.dump(devices_to_save, f, indent=2)
        except Exception as e:
            print(f"[AuthTokenManager] Error saving trusted devices: {e}")

    def generate_device_fingerprint(self, user_agent, accept_language):
        """Generate a device fingerprint from browser characteristics"""
        fingerprint_data = f"{user_agent}|{accept_language}"
        fingerprint = hashlib.sha256(fingerprint_data.encode()).hexdigest()
        return fingerprint

    def trust_device(self, fingerprint, username):
        """Mark a device as trusted for auto-login"""
        with self._lock:
            self._trusted_devices[fingerprint] = {
                'username': username,
                'trusted_at': datetime.now().isoformat(),
                'last_seen': datetime.now().isoformat()
            }
            self._save_trusted_devices()
        print(f"[AuthTokenManager] Device {fingerprint[:8]}... trusted for {username}")

    def is_device_trusted(self, fingerprint):
        """Check if a device is trusted and return username if so"""
        with self._lock:
            device_data = self._trusted_devices.get(fingerprint)

            if not device_data:
                return None

            # Update last seen time
            device_data['last_seen'] = datetime.now().isoformat()
            self._save_trusted_devices()

            return device_data['username']

    def untrust_device(self, fingerprint):
        """Remove a device from trusted devices"""
        with self._lock:
            if fingerprint in self._trusted_devices:
                del self._trusted_devices[fingerprint]
                self._save_trusted_devices()
                print(f"[AuthTokenManager] Device {fingerprint[:8]}... untrusted")
                return True
        return False


# Global token manager instance
auth_token_manager = None


# ============================================================================
# Config File Helper
# ============================================================================

def format_python_value(value, indent_level=0):
    """
    Format a Python value as valid Python code string.
    Handles dictionaries, lists, strings, booleans, None, numbers.
    """
    indent = '    ' * indent_level
    next_indent = '    ' * (indent_level + 1)
    
    if isinstance(value, dict):
        if not value:
            return '{}'
        items = []
        for k, v in value.items():
            key_str = f"'{k}'" if isinstance(k, str) else str(k)
            val_str = format_python_value(v, indent_level + 1)
            items.append(f"{next_indent}{key_str}: {val_str}")
        return '{\n' + ',\n'.join(items) + f'\n{indent}}}'
    elif isinstance(value, list):
        if not value:
            return '[]'
        items = []
        for item in value:
            item_str = format_python_value(item, indent_level + 1)
            items.append(f"{next_indent}{item_str}")
        return '[\n' + ',\n'.join(items) + f'\n{indent}]'
    elif isinstance(value, str):
        # Escape single quotes in strings
        escaped = value.replace("'", "\\'").replace('\n', '\\n').replace('\r', '\\r')
        return f"'{escaped}'"
    elif isinstance(value, bool):
        return 'True' if value else 'False'
    elif value is None:
        return 'None'
    elif isinstance(value, (int, float)):
        return str(value)
    else:
        # Fallback for other types - convert to string representation
        return repr(value)


def save_config_to_file(config_dict, filename='config.py'):
    """Save config dictionary to a Python file with proper formatting"""
    with open(filename, 'w') as f:
        f.write('CONFIG = ')
        f.write(format_python_value(config_dict))
        f.write('\n')


# ============================================================================
# Thread-Safe SocketIO Emit Helper for Cross-Thread Communication
# ============================================================================

def safe_emit_from_thread(socketio_instance, event, data, namespace='/'):
    """
    Safely emit SocketIO events from any thread (including GPIO thread) to eventlet context.
    Uses a thread-safe queue that is polled by an eventlet background task.
    """
    global emit_queue
    if emit_queue is not None:
        try:
            emit_queue.put({
                'event': event,
                'data': data,
                'namespace': namespace
            })
            print(f"[safe_emit] Queued emit for {event}")
        except Exception as e:
            print(f"[safe_emit] Error queuing emit for {event}: {e}")
            import traceback
            traceback.print_exc()
    elif socketio_instance:
        # Fallback: try direct emit if queue not available
        try:
            socketio_instance.emit(event, data, namespace=namespace)
            print(f"[safe_emit] Direct emit succeeded for {event} (queue not available)")
        except Exception as e:
            print(f"[safe_emit] Direct emit failed for {event}: {e}")
    else:
        print(f"[safe_emit] No socketio_instance or queue available for {event}")


def emit_queue_processor(socketio_instance):
    """
    Background task that processes emit queue in eventlet context.
    This runs continuously and processes any emits queued from other threads.
    """
    global emit_queue
    # Import eventlet here since we need it only in this function
    import eventlet
    print("[emit_queue_processor] Started")
    while True:
        try:
            # Check queue with non-blocking get
            try:
                item = emit_queue.get_nowait()
            except queue.Empty:
                # Queue is empty, yield to other greenlets and check again soon
                eventlet.sleep(0.01)  # Yield to eventlet
                continue
            
            # Emit in eventlet context
            try:
                socketio_instance.emit(
                    item['event'],
                    item['data'],
                    namespace=item['namespace']
                )
                print(f"[emit_queue_processor] Successfully emitted {item['event']} to all clients")
            except Exception as e:
                print(f"[emit_queue_processor] Error emitting {item['event']}: {e}")
                import traceback
                traceback.print_exc()
            
            # Small yield after processing to allow other tasks to run
            eventlet.sleep(0)
        except Exception as e:
            print(f"[emit_queue_processor] Error in queue processor: {e}")
            import traceback
            traceback.print_exc()
            eventlet.sleep(0.1)  # Yield before retrying

# ============================================================================
# Thread-Safe State Management Classes
# ============================================================================

class WiFiScheduler:
    """Manages WiFi schedule entries and checks if current time is within schedule"""

    def __init__(self, schedule_file='wifi_schedule.json'):
        self._schedule_file = schedule_file
        self._lock = threading.RLock()
        self._schedule_entries = []
        self.socketio = None
        self._load_schedule()

    def _load_schedule(self):
        """Load schedule from file"""
        try:
            if os.path.exists(self._schedule_file):
                with open(self._schedule_file, 'r') as f:
                    self._schedule_entries = json.load(f)
                    print(f"[WiFiScheduler] Loaded {len(self._schedule_entries)} schedule entries")
        except Exception as e:
            print(f"[WiFiScheduler] Error loading schedule: {e}")
            self._schedule_entries = []

    def _save_schedule(self):
        """Save schedule to file"""
        try:
            with open(self._schedule_file, 'w') as f:
                json.dump(self._schedule_entries, f, indent=2)
            print(f"[WiFiScheduler] Saved {len(self._schedule_entries)} schedule entries")
        except Exception as e:
            print(f"[WiFiScheduler] Error saving schedule: {e}")

    def add_entry(self, days, start_time, end_time, description=""):
        """
        Add a schedule entry
        days: list of integers (0=Monday, 6=Sunday) e.g., [0, 1, 2, 3, 4] for weekdays
        start_time: "HH:MM" format (24-hour)
        end_time: "HH:MM" format (24-hour)
        """
        with self._lock:
            entry = {
                'id': str(int(time.time() * 1000)),  # Unique ID based on timestamp
                'days': days,
                'start_time': start_time,
                'end_time': end_time,
                'description': description,
                'enabled': True
            }
            self._schedule_entries.append(entry)
            self._save_schedule()
            print(f"[WiFiScheduler] Added entry: {entry}")
            return entry

    def remove_entry(self, entry_id):
        """Remove a schedule entry by ID"""
        with self._lock:
            original_len = len(self._schedule_entries)
            self._schedule_entries = [e for e in self._schedule_entries if e['id'] != entry_id]
            if len(self._schedule_entries) < original_len:
                self._save_schedule()
                print(f"[WiFiScheduler] Removed entry: {entry_id}")
                return True
            return False

    def update_entry(self, entry_id, days=None, start_time=None, end_time=None, description=None, enabled=None):
        """Update a schedule entry"""
        with self._lock:
            for entry in self._schedule_entries:
                if entry['id'] == entry_id:
                    if days is not None:
                        entry['days'] = days
                    if start_time is not None:
                        entry['start_time'] = start_time
                    if end_time is not None:
                        entry['end_time'] = end_time
                    if description is not None:
                        entry['description'] = description
                    if enabled is not None:
                        entry['enabled'] = enabled
                    self._save_schedule()
                    print(f"[WiFiScheduler] Updated entry: {entry}")
                    return True
            return False

    def get_entries(self):
        """Get all schedule entries"""
        with self._lock:
            return self._schedule_entries.copy()

    def is_within_schedule(self):
        """Check if current time is within any enabled schedule entry"""
        with self._lock:
            now = datetime.now()
            current_day = now.weekday()  # 0=Monday, 6=Sunday
            current_time = now.strftime("%H:%M")

            for entry in self._schedule_entries:
                if not entry.get('enabled', True):
                    continue

                if current_day not in entry['days']:
                    continue

                start_time = entry['start_time']
                end_time = entry['end_time']

                # Handle time comparison
                if start_time <= end_time:
                    # Normal case: start before end (e.g., 09:00 - 17:00)
                    if start_time <= current_time <= end_time:
                        return True, entry
                else:
                    # Overnight case: end after midnight (e.g., 22:00 - 02:00)
                    if current_time >= start_time or current_time <= end_time:
                        return True, entry

            return False, None


class LEDController:
    """Manages LED brightness using PWM"""

    def __init__(self, settings_file='led_settings.json'):
        self._settings_file = settings_file
        self._lock = threading.RLock()
        self._pwm_status = None
        self._pwm_always_on = None
        self._pwm_scheduled = None
        self.socketio = None

        # Default brightness values (0-100%)
        self._brightness = {
            'status': 100,
            'always_on': 100,
            'scheduled': 100
        }

        # Track current LED states to prevent unnecessary PWM updates
        self._led_states = {
            'status': None,
            'always_on': None,
            'scheduled': None
        }

        self._load_settings()

    def _load_settings(self):
        """Load LED brightness settings from file"""
        try:
            if os.path.exists(self._settings_file):
                with open(self._settings_file, 'r') as f:
                    saved_settings = json.load(f)
                    self._brightness.update(saved_settings)
                    print(f"[LEDController] Loaded settings: {self._brightness}")
        except Exception as e:
            print(f"[LEDController] Error loading settings: {e}")

    def _save_settings(self):
        """Save LED brightness settings to file"""
        try:
            with open(self._settings_file, 'w') as f:
                json.dump(self._brightness, f, indent=2)
            print(f"[LEDController] Saved settings: {self._brightness}")
        except Exception as e:
            print(f"[LEDController] Error saving settings: {e}")

    def initialize_pwm(self):
        """Initialize PWM for all LEDs"""
        try:
            # Initialize PWM at 1000 Hz frequency
            self._pwm_status = GPIO.PWM(LED_STATUS, 1000)
            self._pwm_always_on = GPIO.PWM(LED_ALWAYS_ON, 1000)
            self._pwm_scheduled = GPIO.PWM(LED_SCHEDULED, 1000)

            # Start all LEDs OFF - they will be set correctly by WiFiStateManager and schedule checker
            self._pwm_status.start(0)
            self._pwm_always_on.start(0)
            self._pwm_scheduled.start(0)

            # Track initial states (all OFF)
            self._led_states['status'] = False
            self._led_states['always_on'] = False
            self._led_states['scheduled'] = False

            print(f"[LEDController] PWM initialized with brightness: {self._brightness}")
        except Exception as e:
            print(f"[LEDController] Error initializing PWM: {e}")

    def set_brightness(self, led_name, brightness):
        """Set brightness for a specific LED (0-100%)"""
        with self._lock:
            if led_name not in self._brightness:
                print(f"[LEDController] Invalid LED name: {led_name}")
                return False

            # Clamp brightness to 0-100
            brightness = max(0, min(100, brightness))
            self._brightness[led_name] = brightness

            try:
                # Only update PWM duty cycle if LED is currently ON
                # Otherwise just save the brightness setting for next time it turns on
                is_led_on = self._led_states.get(led_name, False)

                if is_led_on:
                    if led_name == 'status' and self._pwm_status:
                        self._pwm_status.ChangeDutyCycle(brightness)
                    elif led_name == 'always_on' and self._pwm_always_on:
                        self._pwm_always_on.ChangeDutyCycle(brightness)
                    elif led_name == 'scheduled' and self._pwm_scheduled:
                        self._pwm_scheduled.ChangeDutyCycle(brightness)

                print(f"[LEDController] Set {led_name} brightness to {brightness}% (LED is {'ON' if is_led_on else 'OFF'})")
                self._save_settings()

                # Broadcast brightness update
                if self.socketio:
                    safe_emit_from_thread(
                        self.socketio,
                        'led_brightness_updated',
                        {
                            'led': led_name,
                            'brightness': brightness,
                            'all_brightness': self._brightness.copy()
                        }
                    )

                return True
            except Exception as e:
                print(f"[LEDController] Error setting brightness: {e}")
                return False

    def get_brightness(self, led_name=None):
        """Get brightness for a specific LED or all LEDs"""
        with self._lock:
            if led_name:
                return self._brightness.get(led_name, 0)
            return self._brightness.copy()

    def set_led_state(self, led_name, enabled):
        """Turn LED on/off (uses saved brightness when on, 0 when off)"""
        with self._lock:
            try:
                # Check if state is already set to avoid unnecessary PWM updates (prevents flickering)
                if self._led_states.get(led_name) == enabled:
                    return  # State unchanged, skip update

                brightness = self._brightness[led_name] if enabled else 0

                if led_name == 'status' and self._pwm_status:
                    self._pwm_status.ChangeDutyCycle(brightness)
                    self._led_states['status'] = enabled
                elif led_name == 'always_on' and self._pwm_always_on:
                    self._pwm_always_on.ChangeDutyCycle(brightness)
                    self._led_states['always_on'] = enabled
                elif led_name == 'scheduled' and self._pwm_scheduled:
                    self._pwm_scheduled.ChangeDutyCycle(brightness)
                    self._led_states['scheduled'] = enabled

                state_text = f"ON ({self._brightness[led_name]}%)" if enabled else "OFF"
                print(f"[LEDController] Set {led_name} to {state_text}")
            except Exception as e:
                print(f"[LEDController] Error setting LED state: {e}")

    def cleanup(self):
        """Stop all PWM and cleanup"""
        try:
            if self._pwm_status:
                self._pwm_status.stop()
            if self._pwm_always_on:
                self._pwm_always_on.stop()
            if self._pwm_scheduled:
                self._pwm_scheduled.stop()
            print("[LEDController] PWM stopped")
        except Exception as e:
            print(f"[LEDController] Error during cleanup: {e}")


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

            # Control LEDs based on WiFi state
            # LED_STATUS: ON during scheduled time OR when WiFi is ALWAYS ON
            # LED_ALWAYS_ON: ON only when WiFi is ON (ALWAYS ON mode)
            # LED_SCHEDULED: ON only when WiFi state is OFF (regardless of schedule)
            global led_controller, wifi_scheduler
            if led_controller:
                try:
                    if new_state:  # WiFi is ON (ALWAYS ON mode)
                        led_controller.set_led_state('status', True)
                        led_controller.set_led_state('always_on', True)
                        led_controller.set_led_state('scheduled', False)
                        print(f"[WiFiStateManager] LEDs: STATUS=ON, ALWAYS_ON=ON, SCHEDULED=OFF (always on mode)")
                    else:  # WiFi is OFF
                        led_controller.set_led_state('always_on', False)
                        led_controller.set_led_state('scheduled', True)  # Always ON when WiFi is OFF
                        # Check current schedule and set LED_STATUS appropriately
                        if wifi_scheduler:
                            within_schedule, _ = wifi_scheduler.is_within_schedule()
                            led_controller.set_led_state('status', within_schedule)
                            print(f"[WiFiStateManager] LEDs: STATUS={'ON' if within_schedule else 'OFF'}, ALWAYS_ON=OFF, SCHEDULED=ON (wifi off)")
                        else:
                            led_controller.set_led_state('status', False)
                            print(f"[WiFiStateManager] LEDs: STATUS=OFF, ALWAYS_ON=OFF, SCHEDULED=ON (wifi off)")
                except Exception as e:
                    print(f"[WiFiStateManager] Error updating LEDs: {e}")

            # Add to activity log (only for real actions, not initial/query)
            if source not in ['initial', 'query'] and self.activity_log:
                self.activity_log.add_entry(f"WiFi turned {state_text} (via {source_text})", source=source)

            # Broadcast to all connected clients
            # Use safe_emit for cross-thread safety with eventlet
            if self.socketio:
                safe_emit_from_thread(
                    self.socketio,
                    'wifi_state_changed',
                    {
                        'state': new_state,
                        'source': source,
                        'timestamp': datetime.now().isoformat()
                    }
                )
                print(f"[WiFiStateManager] Broadcasted state change to all clients")

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

        # Emit initial countdown immediately (outside lock to avoid deadlock)
        if self._socketio:
            remaining = self.get_remaining_seconds()
            safe_emit_from_thread(
                self._socketio,
                'auto_off_countdown',
                {
                    'remaining_seconds': remaining,
                    'remaining_minutes': remaining // 60
                }
            )
            print(f"[AutoOffTimer] Emitted initial countdown: {remaining // 60} minutes")

            # Start countdown emission thread for subsequent updates
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
            safe_emit_from_thread(
                self._socketio,
                'auto_off_triggered',
                {
                    'timestamp': datetime.now().isoformat()
                }
            )

    def _emit_countdown_loop(self):
        """Emit countdown updates every 60 seconds (initial emission done in start())"""
        print("[AutoOffTimer] Countdown loop started")
        while not self._stop_countdown:
            # Sleep first since initial emission is done in start()
            print(f"[AutoOffTimer] Countdown loop sleeping for 60 seconds...")
            time.sleep(60)

            if self._stop_countdown:
                print("[AutoOffTimer] Countdown loop stopped")
                break

            remaining = self.get_remaining_seconds()
            print(f"[AutoOffTimer] Countdown loop: remaining={remaining}s ({remaining // 60}m)")

            if remaining <= 0:
                print("[AutoOffTimer] Countdown loop: timer expired, breaking")
                break

            if self._socketio:
                safe_emit_from_thread(
                    self._socketio,
                    'auto_off_countdown',
                    {
                        'remaining_seconds': remaining,
                        'remaining_minutes': remaining // 60
                    }
                )
                print(f"[AutoOffTimer] Emitted countdown update: {remaining // 60} minutes")
            else:
                print("[AutoOffTimer] No socketio instance, skipping emit")

        print("[AutoOffTimer] Countdown loop exited")


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
            print(f'SSH COMMAND: {command}')
            
            output = stdout.read().decode('utf-8')

            print(f'SSH OUTPUT: {output}')
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
            # Prepend device name to message if configured
            device_name = CONFIG.get('device', {}).get('name', '').strip()
            formatted_message = message
            if device_name:
                formatted_message = f"{device_name}: {message}"
            
            entry = {
                'message': formatted_message,
                'source': source,
                'timestamp': datetime.now().isoformat()
            }
            self._entries.append(entry)
            # Keep only last max_entries
            if len(self._entries) > self._max_entries:
                self._entries = self._entries[-self._max_entries:]
            print(f"[ActivityLog] {formatted_message}")

        # Save to file (outside lock to avoid blocking)
        try:
            self._save_to_file()
        except Exception as e:
            print(f"[ActivityLog] Error saving entry: {e}")

        # Broadcast to all clients OUTSIDE the lock
        # Use safe_emit for cross-thread safety with eventlet
        # This ensures emits from GPIO thread work correctly with eventlet
        if entry and self._socketio:
            try:
                safe_emit_from_thread(self._socketio, 'activity_log_entry', entry)
            except Exception as e:
                print(f"[ActivityLog] Error in safe_emit for activity log: {e}")
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

CORS(app, resources={
    r"/getstatus": {
        "origins": [
            "https://rock.lcbcchurch.com",
            "https://lcbcchurch.com"
        ]
    }
})

app.config['SECRET_KEY'] = CONFIG['dashboard']['secret_key']
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')


# ============================================================================
# Authentication
# ============================================================================

def login_required(f):
    """Decorator to require login for routes - uses session-based auth"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page with device fingerprinting for auto-login"""
    # Generate device fingerprint
    user_agent = request.headers.get('User-Agent', '')
    accept_language = request.headers.get('Accept-Language', '')
    device_fingerprint = None

    if auth_token_manager:
        device_fingerprint = auth_token_manager.generate_device_fingerprint(user_agent, accept_language)

        # Check if device is trusted for auto-login
        trusted_username = auth_token_manager.is_device_trusted(device_fingerprint)
        if trusted_username:
            print(f"[Auth] Auto-login for trusted device {device_fingerprint[:8]}...")
            session['authenticated'] = True
            session.permanent = True
            return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # Validate credentials
        if (username == CONFIG['dashboard']['username'] and
            password == CONFIG['dashboard']['password']):

            # Set session
            session['authenticated'] = True
            session.permanent = True

            # Trust this device for future auto-login
            if auth_token_manager and device_fingerprint:
                auth_token_manager.trust_device(device_fingerprint, username)
                print(f"[Auth] User {username} logged in (device {device_fingerprint[:8]}...)")

            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='Invalid credentials')

    return render_template('login.html')


@app.route('/logout')
def logout():
    """Logout - clear session and untrust device"""
    # Untrust device
    if auth_token_manager:
        user_agent = request.headers.get('User-Agent', '')
        accept_language = request.headers.get('Accept-Language', '')
        device_fingerprint = auth_token_manager.generate_device_fingerprint(user_agent, accept_language)
        auth_token_manager.untrust_device(device_fingerprint)

    # Clear session
    session.clear()

    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    """Main dashboard"""
    return render_template('index.html')


@app.route('/getstatus')
def getStatusAPI():
    return jsonify({'status': 'ON'}), 200


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
        'ssh_enabled': CONFIG['ssh'].get('enabled', True),
        'device_name': CONFIG.get('device', {}).get('name', '')
    })

    # Send LED brightness settings
    if led_controller:
        emit('led_brightness_settings', {
            'brightness': led_controller.get_brightness()
        })

    # Send schedule entries
    if wifi_scheduler:
        emit('schedule_updated', {
            'entries': wifi_scheduler.get_entries()
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

    # Save to file with proper Python formatting (preserves True/False/None)
    try:
        save_config_to_file(CONFIG, 'config.py')

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


@socketio.on('update_device_name')
def handle_update_device_name(data):
    """Update device name setting"""
    device_name = data.get('device_name', '').strip()
    print(f"[SocketIO] Update device name to: '{device_name}'")

    # Ensure device config exists
    if 'device' not in CONFIG:
        CONFIG['device'] = {}

    # Update config
    CONFIG['device']['name'] = device_name

    # Save to file with proper Python formatting (preserves True/False/None)
    try:
        save_config_to_file(CONFIG, 'config.py')

        # Add to activity log (without device name prepending to avoid recursion)
        if activity_log:
            activity_log.add_entry(f"Device name updated to '{device_name}'", source="dashboard")

        # Broadcast settings update to all clients
        socketio.emit('settings_updated', {
            'device_name': device_name
        }, namespace='/')
    except Exception as e:
        emit('ssh_error', {
            'error': f'Failed to save device name: {str(e)}',
            'timestamp': datetime.now().isoformat()
        })


@socketio.on('update_led_brightness')
def handle_update_led_brightness(data):
    """Update LED brightness setting"""
    global led_controller

    led_name = data.get('led')
    brightness = data.get('brightness')

    print(f"[SocketIO] Update LED brightness: {led_name} = {brightness}%")

    if not led_controller:
        emit('ssh_error', {
            'error': 'LED controller not initialized',
            'timestamp': datetime.now().isoformat()
        })
        return

    if led_name not in ['status', 'always_on', 'scheduled']:
        emit('ssh_error', {
            'error': f'Invalid LED name: {led_name}',
            'timestamp': datetime.now().isoformat()
        })
        return

    try:
        brightness = int(brightness)
        if brightness < 0 or brightness > 100:
            emit('ssh_error', {
                'error': 'Brightness must be between 0 and 100',
                'timestamp': datetime.now().isoformat()
            })
            return

        # Update LED brightness
        if led_controller.set_brightness(led_name, brightness):
            print(f"[SocketIO] LED brightness updated successfully: {led_name} = {brightness}%")
        else:
            emit('ssh_error', {
                'error': f'Failed to set LED brightness',
                'timestamp': datetime.now().isoformat()
            })
    except Exception as e:
        emit('ssh_error', {
            'error': f'Failed to update LED brightness: {str(e)}',
            'timestamp': datetime.now().isoformat()
        })


@socketio.on('get_led_brightness')
def handle_get_led_brightness():
    """Send current LED brightness settings to client"""
    global led_controller

    if led_controller:
        brightness = led_controller.get_brightness()
        emit('led_brightness_settings', {
            'brightness': brightness
        })
        print(f"[SocketIO] Sent LED brightness settings: {brightness}")


@socketio.on('add_schedule_entry')
def handle_add_schedule_entry(data):
    """Add a new schedule entry"""
    global wifi_scheduler

    try:
        days = data.get('days', [])
        start_time = data.get('start_time', '')
        end_time = data.get('end_time', '')
        description = data.get('description', '')

        if not days or not start_time or not end_time:
            emit('ssh_error', {
                'error': 'Missing required fields for schedule entry',
                'timestamp': datetime.now().isoformat()
            })
            return

        if wifi_scheduler:
            entry = wifi_scheduler.add_entry(days, start_time, end_time, description)

            # Broadcast updated schedule to all clients
            socketio.emit('schedule_updated', {
                'entries': wifi_scheduler.get_entries()
            }, namespace='/')

            print(f"[SocketIO] Added schedule entry: {entry}")
    except Exception as e:
        emit('ssh_error', {
            'error': f'Failed to add schedule entry: {str(e)}',
            'timestamp': datetime.now().isoformat()
        })


@socketio.on('remove_schedule_entry')
def handle_remove_schedule_entry(data):
    """Remove a schedule entry"""
    global wifi_scheduler

    try:
        entry_id = data.get('id')
        if not entry_id:
            emit('ssh_error', {
                'error': 'Missing entry ID',
                'timestamp': datetime.now().isoformat()
            })
            return

        if wifi_scheduler:
            if wifi_scheduler.remove_entry(entry_id):
                # Broadcast updated schedule to all clients
                socketio.emit('schedule_updated', {
                    'entries': wifi_scheduler.get_entries()
                }, namespace='/')
                print(f"[SocketIO] Removed schedule entry: {entry_id}")
            else:
                emit('ssh_error', {
                    'error': 'Schedule entry not found',
                    'timestamp': datetime.now().isoformat()
                })
    except Exception as e:
        emit('ssh_error', {
            'error': f'Failed to remove schedule entry: {str(e)}',
            'timestamp': datetime.now().isoformat()
        })


@socketio.on('update_schedule_entry')
def handle_update_schedule_entry(data):
    """Update a schedule entry"""
    global wifi_scheduler

    try:
        entry_id = data.get('id')
        if not entry_id:
            emit('ssh_error', {
                'error': 'Missing entry ID',
                'timestamp': datetime.now().isoformat()
            })
            return

        if wifi_scheduler:
            success = wifi_scheduler.update_entry(
                entry_id,
                days=data.get('days'),
                start_time=data.get('start_time'),
                end_time=data.get('end_time'),
                description=data.get('description'),
                enabled=data.get('enabled')
            )

            if success:
                # Broadcast updated schedule to all clients
                socketio.emit('schedule_updated', {
                    'entries': wifi_scheduler.get_entries()
                }, namespace='/')
                print(f"[SocketIO] Updated schedule entry: {entry_id}")
            else:
                emit('ssh_error', {
                    'error': 'Schedule entry not found',
                    'timestamp': datetime.now().isoformat()
                })
    except Exception as e:
        emit('ssh_error', {
            'error': f'Failed to update schedule entry: {str(e)}',
            'timestamp': datetime.now().isoformat()
        })


@socketio.on('get_schedule')
def handle_get_schedule():
    """Send schedule to client"""
    global wifi_scheduler

    if wifi_scheduler:
        emit('schedule_updated', {
            'entries': wifi_scheduler.get_entries()
        })
        print(f"[SocketIO] Sent schedule with {len(wifi_scheduler.get_entries())} entries")


# ============================================================================
# GPIO Monitoring
# ============================================================================

def gpio_loop():
    """GPIO monitoring loop (runs in separate thread)"""
    global state_manager, cooldown_manager, auto_off_timer, ssh_controller, led_controller

    # Initialize GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(BUTTON_PIN_ON, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(BUTTON_PIN_OFF, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    # Setup LED pins as outputs (PWM compatible)
    GPIO.setup(LED_STATUS, GPIO.OUT)
    GPIO.setup(LED_ALWAYS_ON, GPIO.OUT)
    GPIO.setup(LED_SCHEDULED, GPIO.OUT)

    # Initialize LED PWM controller
    if led_controller:
        led_controller.initialize_pwm()
        print("[GPIO] LED PWM initialized")

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
                                safe_emit_from_thread(
                                    socketio_instance,
                                    'ssh_error',
                                    {
                                        'error': f'Failed to turn WiFi ON: {message}',
                                        'timestamp': datetime.now().isoformat()
                                    }
                                )

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
                                safe_emit_from_thread(
                                    socketio_instance,
                                    'ssh_error',
                                    {
                                        'error': f'Failed to turn WiFi OFF: {message}',
                                        'timestamp': datetime.now().isoformat()
                                    }
                                )

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

def schedule_checker_loop():
    """Background thread that checks schedule and controls LED_STATUS indicator only when WiFi is OFF"""
    global wifi_scheduler, led_controller, state_manager

    print("[ScheduleChecker] Started (LED_STATUS indicator only - no SSH control)")
    last_within_schedule = None

    try:
        while True:
            time.sleep(30)  # Check every 30 seconds

            if not wifi_scheduler or not led_controller or not state_manager:
                continue

            # Only control LED_STATUS if WiFi is OFF
            # When WiFi is ON (ALWAYS ON mode), LED_STATUS is controlled by WiFiStateManager
            wifi_is_on = state_manager.get_state()

            if wifi_is_on:
                # WiFi is ON (ALWAYS ON mode) - skip schedule control, LED_STATUS already ON
                continue

            within_schedule, active_entry = wifi_scheduler.is_within_schedule()

            # Only take action if schedule status changed
            if within_schedule != last_within_schedule:
                if within_schedule:
                    # Schedule active - turn LED_STATUS ON (WiFi is OFF, so we control it)
                    print(f"[ScheduleChecker] Schedule active: {active_entry}")
                    led_controller.set_led_state('status', True)
                    print(f"[ScheduleChecker] LED_STATUS turned ON (schedule indicator)")
                else:
                    # Schedule inactive - turn LED_STATUS OFF (WiFi is OFF, so we control it)
                    print(f"[ScheduleChecker] Schedule inactive")
                    led_controller.set_led_state('status', False)
                    print(f"[ScheduleChecker] LED_STATUS turned OFF (schedule ended)")

                last_within_schedule = within_schedule

    except Exception as e:
        print(f"[ScheduleChecker] Error: {e}")
        import traceback
        traceback.print_exc()


def auto_off_callback():
    """Callback function for auto-off timer expiration"""
    print("[Main] Auto-off timer expired, turning WiFi OFF")
    state_manager.set_state(False, source='auto-off')
    ssh_controller.set_wifi_off()
    if activity_log:
        activity_log.add_entry("WiFi automatically turned OFF (timer expired)", source="auto-off")


def signal_handler(sig, frame):
    """Handle shutdown signals"""
    global led_controller
    print("\n[Main] Shutting down gracefully...")
    auto_off_timer.cancel()
    # Save activity log before exiting
    if activity_log:
        print("[Main] Saving activity log...")
        activity_log._save_to_file()
    # Cleanup LED PWM
    if led_controller:
        print("[Main] Stopping LED PWM...")
        led_controller.cleanup()
    GPIO.cleanup()
    sys.exit(0)


def main():
    """Main application entry point"""
    global state_manager, cooldown_manager, auto_off_timer, ssh_controller, socketio_instance, activity_log, emit_queue, auth_token_manager, led_controller, wifi_scheduler

    print("=" * 60)
    print("WiFi Controller Dashboard")
    print("=" * 60)

    # Initialize thread-safe queue for cross-thread SocketIO emits
    emit_queue = queue.Queue()

    # Initialize authentication manager (device fingerprinting)
    auth_token_manager = AuthTokenManager(device_file='trusted_devices.json')
    print("[Main] Device fingerprinting authentication initialized")

    # Initialize components
    activity_log = ActivityLog(max_entries=25, socketio=socketio)

    # Initialize LED controller
    led_controller = LEDController(settings_file='led_settings.json')
    led_controller.socketio = socketio
    print("[Main] LED controller initialized")

    # Initialize WiFi scheduler
    wifi_scheduler = WiFiScheduler(schedule_file='wifi_schedule.json')
    wifi_scheduler.socketio = socketio
    print("[Main] WiFi scheduler initialized")

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

    # Start emit queue processor in eventlet context
    # This processes emits queued from GPIO thread and other non-eventlet threads
    socketio.start_background_task(emit_queue_processor, socketio)
    print("[Main] Started emit queue processor")

    # Start GPIO monitoring in a separate thread
    # GPIO operations are blocking and should run in a regular thread, not eventlet
    gpio_thread = threading.Thread(target=gpio_loop)
    gpio_thread.daemon = True
    gpio_thread.start()

    # Start schedule checker in a separate thread
    schedule_thread = threading.Thread(target=schedule_checker_loop)
    schedule_thread.daemon = True
    schedule_thread.start()

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
