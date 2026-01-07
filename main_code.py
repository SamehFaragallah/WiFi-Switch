import RPi.GPIO as GPIO
import time

BUTTON_PIN = 23
BUTTON_PIN2 = 24
GPIO.setmode(GPIO.BCM)
GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_PIN2, GPIO.IN, pull_up_down=GPIO.PUD_UP)

prevState = GPIO.input(BUTTON_PIN)
prevState2 = GPIO.input(BUTTON_PIN2)

print('initialized')
try:
    while True:
        time.sleep(0.01)
        buttonState = GPIO.input(BUTTON_PIN)
        if buttonState != prevState:
            print(buttonState)
            prevState = buttonState
        
        buttonState2 = GPIO.input(BUTTON_PIN2)
        if buttonState2 != prevState2:
            print(f'{buttonState2} button 2')
            prevState2 = buttonState2
            
except KeyboardInterrupt:
    GPIO.cleanup()

