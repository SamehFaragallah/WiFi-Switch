import RPi.GPIO as GPIO
import time

# Use BCM GPIO numbering
GPIO.setmode(GPIO.BCM)

LED_PIN = 17
GPIO.setup(LED_PIN, GPIO.OUT)

try:
    while True:
        GPIO.output(LED_PIN, GPIO.HIGH)  # LED ON
        time.sleep(0.5)
        GPIO.output(LED_PIN, GPIO.LOW)   # LED OFF
        time.sleep(0.5)

except KeyboardInterrupt:
    print("Exiting program")

finally:
    GPIO.cleanup()
