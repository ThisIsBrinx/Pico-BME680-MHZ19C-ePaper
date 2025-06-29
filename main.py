from machine import Pin, UART, I2C, reset # type: ignore
from mhz19c import MHZ19BSensor
from epaper import EPD_2in7
from utime import sleep, sleep_ms # type: ignore
from bme680 import *
import network # type: ignore
from mqttLib import MQTTClient, MQTTException
import gc
import private
import utime
try:
    import ntptime # type: ignore
    NTP_AVAILABLE = True
except ImportError:
    print("Warnung: ntptime Modul nicht gefunden. Zeit-Feature ist deaktiviert.")
    NTP_AVAILABLE = False

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
last_ntp_sync = 0
time_synced = False

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

# Start-Annahme für saubere Luft (als float für genauere Berechnung)
voc_baseline = 50000.0 


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
        # ----- Zeit-Synchronisation (NTP) -----
        # Synchronisiere alle 6 Stunden oder beim ersten Mal, falls noch nicht geschehen
        if NTP_AVAILABLE and (not time_synced or utime.time() - last_ntp_sync > 21600):
            if wlan.isconnected():
                try:
                    print("Synchronisiere Uhrzeit via NTP...")
                    ntptime.settime()
                    last_ntp_sync = utime.time()
                    time_synced = True
                    print("Uhrzeit synchronisiert.")
                except Exception as e:
                    print(f"NTP-Fehler: {e}")
                    time_synced = False
            else:
                print("Kein WLAN für NTP-Sync.")

        # ----- MQTT-Verbindung -----
        if mqtt_client_hass.sock is None:
            print("MQTT-Verbindung wird aufgebaut...")
            if not wlan.isconnected(): connectWifi()
            mqtt_client_hass.connect()
            print("MQTT verbunden.")

        ###
        # 1. Daten auslesen
        ###
        co2 = mhz.measure()[0]
        temp = bme680.temperature + temperature_offset
        humi = bme680.humidity
        voc = bme680.gas

        ###
        # 2. Daten interpretieren & Logik für Dauerbetrieb
        ###
        co2_bewertung = "Gut"
        if co2 > 1400: co2_bewertung = "Schlecht"
        elif co2 > 1000: co2_bewertung = "Mittel"

        lernfaktor_up = 0.05
        lernfaktor_down = 0.005
        if voc > voc_baseline:
            voc_baseline += (voc - voc_baseline) * lernfaktor_up
        else:
            voc_baseline += (voc - voc_baseline) * lernfaktor_down
        
        luftguete_prozent = min(100, (voc / voc_baseline) * 100)

        zeit_str = "--:--"
        if time_synced:
            # UTC-Zeit holen und in deutsche Zeit umrechnen (UTC+2 im Sommer)
            current_time = utime.localtime(utime.time() + 7200)
            zeit_str = f"{current_time[3]:02d}:{current_time[4]:02d}"

        ###
        # 3. Dashboard zeichnen
        ###
        epd.image4Gray.fill(epd.white)
        epd.image4Gray.text("RAUMKLIMA", 15, 8, epd.black)
        epd.image4Gray.text(zeit_str, 120, 8, epd.black)
        epd.image4Gray.hline(8, 24, 160, epd.black)
        epd.image4Gray.hline(8, 26, 160, epd.black)
        
        epd.image4Gray.text("CO2", 15, 65, epd.black)
        epd.image4Gray.text(f"{co2} ppm", 100, 65, epd.black)
        epd.image4Gray.text(f"({co2_bewertung})", 65, 85, epd.black)
        epd.image4Gray.hline(8, 110, 160, epd.black)
        y_pos = 130
        epd.image4Gray.text("Temperatur", 15, y_pos, epd.black)
        epd.image4Gray.text(f"{temp:.1f} C", 115, y_pos, epd.black)
        epd.image4Gray.hline(15, y_pos + 20, 146, epd.black)
        y_pos += 35
        epd.image4Gray.text("Feuchtigkeit", 15, y_pos, epd.black)
        epd.image4Gray.text(f"{humi:.1f} %", 115, y_pos, epd.black)
        epd.image4Gray.hline(15, y_pos + 20, 146, epd.black)
        y_pos += 35
        epd.image4Gray.text("Luftguete", 15, y_pos, epd.black)
        epd.image4Gray.text(f"{luftguete_prozent:.0f} %", 115, y_pos, epd.black)

        epd.EPD_2IN7_4Gray_Display(epd.buffer_4Gray)
        
    except (OSError, MQTTException) as e:
        print(f"Fehler aufgetreten: {e}. Setze Verbindung zurück.")
        if mqtt_client_hass.sock:
            try: mqtt_client_hass.sock.close()
            except OSError: pass
        mqtt_client_hass.sock = None
        sleep(5)

    gc.collect()
    print(f"Warte {waitingTimeinS} Sekunden bis zur nächsten Messung...")
    sleep(waitingTimeinS)