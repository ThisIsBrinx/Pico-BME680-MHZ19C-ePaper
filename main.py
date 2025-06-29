from machine import Pin, UART, I2C, reset # type: ignore
from mhz19c import MHZ19BSensor
from epaper import EPD_2in7
from utime import sleep, sleep_ms # type: ignore
from bme680 import *
import network # type: ignore
from mqttLib import MQTTClient, MQTTException
import gc
import private

#####
# short init
#####


#external LED
led = machine.Pin('LED', machine.Pin.OUT, value=0) # type: ignore
led.on()

def flash(timeInMs, repeat):
    i = 0
    while i < repeat:        
        sleep_ms(timeInMs)
        led.off()
        sleep_ms(timeInMs)
        led.on()
        i= i+1

flash(100,2)


#####
# General Settings
#####
waitingTimeinS = 3
# WIFI
# country
network.country('DE')
# Client-Mode
wlan = network.WLAN(network.STA_IF)

SSID = private.SSID
SSID_PASSWORD = private.SSID_PASSWORD

# MQTT
mqtt_client_id = private.mqtt_client_id
## HomeAssistant config
mqtt_host_hass = private.mqtt_host_hass
mqtt_username_hass = private.mqtt_username_hass
mqtt_password_hass = private.mqtt_password_hass
### topics
mqtt_publish_topic_hass_co2 = "/mobile/co2/"
mqtt_publish_topic_hass_voc = "/mobile/voc/"
mqtt_publish_topic_hass_temp = "/mobile/temp/"
mqtt_publish_topic_hass_pres = "/mobile/pres/"
mqtt_publish_topic_hass_humi = "/mobile/humi/"
### init
# Initialize our HomeAssistant MQTTClient and connect to the MQTT server
mqtt_client_hass = MQTTClient(
    client_id=mqtt_client_id,
    server=mqtt_host_hass,
    user=mqtt_username_hass,
    password=mqtt_password_hass)


#####
# BME 680
#####
bme680_i2c_sda = Pin(20)
bme680_i2c_scl = Pin(21)
# change this to match the location's pressure (hPa) at sea level
sealevelpressure = 1012.25
# Initialize BME680 Sensor
bme680 = BME680_I2C(I2C(0, sda=bme680_i2c_sda,scl=bme680_i2c_scl))


# You will usually have to add an offset to account for the temperature of
# the sensor. This is usually around 5 degrees but varies by use. Use a
# separate temperature sensor to calibrate this one.
temperature_offset = -3.3

# set sealevel if it is conffigured
if 'sealevelpressure' in locals():
    bme680.sea_level_pressure = sealevelpressure

# degree symbol decleration
degreecels = '\u00B0' + "C"


#####
# MHZ19C
#####
mhz_tx_pin = Pin(4)
mhz_rx_pin = Pin(5)
mhz = MHZ19BSensor(tx_pin=mhz_tx_pin, rx_pin=mhz_rx_pin)

#####
# ePaper-Display
#####
epd = EPD_2in7()
epd.EPD_2IN7_Init()

##########
# Functions
##########

# WiFi
def connectWifi():
    
    timerBeforeRestart = 20
    
    if not wlan.isconnected():
        print('connecting to network...', SSID)
        wlan.active(True)
        wlan.connect(SSID, SSID_PASSWORD)
        while not wlan.isconnected():
            if timerBeforeRestart <=0:
                reset()
            print("Attempting to connect....")
            flash(1000,1)
            timerBeforeRestart = timerBeforeRestart -1
    print('Connected! Network config:', wlan.ifconfig())


while True:
    try:
        # Prüfen, ob eine MQTT-Verbindung besteht. Wenn nicht, verbinden.
        # Wir prüfen das interne ".sock"-Attribut, da die Bibliothek keine "is_connected()"-Methode hat.
        if mqtt_client_hass.sock is None:
            print("MQTT-Verbindung wird aufgebaut...")
            mqtt_client_hass.connect()
            print("MQTT verbunden.")

        ###
        # meassure
        ###

        # CO2
        co2 = mhz.measure()[0]
        # temperature
        temp = bme680.temperature + temperature_offset
        # humidity
        humi = bme680.humidity
        # VOC
        voc = bme680.gas
        # Pressure
        pressure = bme680.pressure
        # Altitude
        altitude = bme680.altitude


        #####
        # Output via Mqtt
        #####
        print("Sende Daten via MQTT...")
        mqtt_client_hass.publish(mqtt_publish_topic_hass_co2, str(co2))
        mqtt_client_hass.publish(mqtt_publish_topic_hass_voc, str(voc))
        mqtt_client_hass.publish(mqtt_publish_topic_hass_temp, str(temp))
        mqtt_client_hass.publish(mqtt_publish_topic_hass_humi, str(humi))
        mqtt_client_hass.publish(mqtt_publish_topic_hass_pres, str(pressure))
        print("Daten gesendet.")

        # HINWEIS: Füge hier deinen Code für die ePaper-Anzeige ein, falls noch nicht geschehen!


        #####
        # Helper
        #####
        if 'voc_max' in globals():
            voc_max = max(voc_max, voc)
        else:
            voc_max = voc
        if 'voc_min' in globals():
            voc_min = min(voc_min, voc)
        else:
            voc_min = voc

        print("CO2: %d ppm" % co2)
        print("Gas: %d ohm" % voc)
        print("Humidity: %0.1f %%" % humi)
        print("temp ", temp)
        print("Pressure: %0.3f hPa" % pressure)
        print("Altitude = %0.2f meters" % altitude)
        print("")
        print("Gas minimum: %d ohm - Schlechteste Luft" % voc_min)
        print("Gas maximum: %d ohm - Beste Luft" % voc_max)
        print("============\n")

    except (OSError, MQTTException) as e:
        print(f"Fehler aufgetreten: {e}. Setze MQTT-Verbindung zurück.")
        # Wenn ein Fehler auftritt, könnte die Verbindung unterbrochen sein.
        # Wir schliessen die Verbindung und setzen sie auf None,
        # damit sie im nächsten Schleifendurchlauf neu aufgebaut wird.
        if mqtt_client_hass.sock:
            mqtt_client_hass.sock.close()
        mqtt_client_hass.sock = None
        # Kurze Pause vor dem nächsten Versuch
        sleep(5)


    # Speicherbereinigung und warten
    gc.collect()
    sleep(waitingTimeinS)