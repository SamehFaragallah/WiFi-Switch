import RPi.GPIO as GPIO
import paramiko
import time

# GPIO setup
BUTTON_PIN = 17
GPIO.setmode(GPIO.BCM)
GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# SSH details
SSH_HOST = "192.168.1.100"   # target machine
SSH_USER = "username"
SSH_PASSWORD = "password"
SSH_COMMAND = "uptime"       # command to run

def send_ssh_command():
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(SSH_HOST, username=SSH_USER, password=SSH_PASSWORD)

        stdin, stdout, stderr = ssh.exec_command(SSH_COMMAND)
        print(stdout.read().decode())
        ssh.close()

    except Exception as e:
        print("SSH error:", e)

print('starting')
send_ssh_command()
print('done')
