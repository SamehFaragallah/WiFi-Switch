from random import random
import RPi.GPIO as GPIO
import time

BUTTON_PIN = 23
BUTTON_PIN2 = 24
GPIO.setmode(GPIO.BCM)
GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_PIN2, GPIO.IN, pull_up_down=GPIO.PUD_UP)

prevState = GPIO.input(BUTTON_PIN)
prevState2 = GPIO.input(BUTTON_PIN2)
wiFiState = getWiFiState()

print('initialized')
try:
    while True:
        time.sleep(0.01)
        buttonState = GPIO.input(BUTTON_PIN)
        if buttonState != prevState:
            print(buttonState)
            prevState = buttonState

            if buttonState == 0:
                setWiFiOn()


        
        buttonState2 = GPIO.input(BUTTON_PIN2)
        if buttonState2 != prevState2:
            print(f'{buttonState2} button 2')
            prevState2 = buttonState2

            if buttonState2 == 0:
                setWiFiOff()
            
except KeyboardInterrupt:
    GPIO.cleanup()


def getWiFiState():
    return random.choice([True, False])

def setWiFiOn():
    print("WiFi turned ON")
    wiFiState = True
    #connect to ssh using credentials saved in file

def setWiFiOff():
    print("WiFi turned OFF")
    wiFiState = False
    #connect to ssh using credentials saved in file