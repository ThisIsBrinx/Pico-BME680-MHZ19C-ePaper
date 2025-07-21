from machine import Pin, UART, I2C, reset
from mhz19c import MHZ19BSensor
from epaper import EPD_2in7
from utime import sleep, sleep_ms
from bme680 import *
import network # type: ignore
from mqttLib import MQTTClient, MQTTException
import gc
import private
import utime

# Robuster Import für ntptime
try:
    import ntptime # type: ignore
    NTP_AVAILABLE = True
except ImportError:
    print("Warnung: ntptime Modul nicht gefunden. Zeit-Feature ist deaktiviert.")
    NTP_AVAILABLE = False


#####
# short init
#####
led = machine.Pin('LED', machine.Pin.OUT, value=0)
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

##########
# Functions
##########

# --- Funktionen zum Speichern und Laden des VOC-Basiswerts ---
def save_baseline(baseline):
    """Speichert den VOC-Basiswert in eine Datei."""
    try:
        with open('baseline.txt', 'w') as f:
            f.write(str(baseline))
        print(f"Basiswert {baseline} erfolgreich in 'baseline.txt' gespeichert.")
    except Exception as e:
        print(f"Fehler beim Speichern des Basiswerts: {e}")

def load_baseline():
    """Lädt den VOC-Basiswert aus einer Datei."""
    try:
        with open('baseline.txt', 'r') as f:
            baseline_str = f.read()
            print(f"Basiswert aus 'baseline.txt' geladen: {baseline_str}")
            return float(baseline_str)
    except (OSError, ValueError) as e:
        print(f"Keine gültige 'baseline.txt' gefunden oder Lesefehler: {e}. Starte mit Standardwert.")
        return 50000.0 # Standard-Startwert

# --- Wifi ---
def connectWifi():
    timerBeforeRestart = 10
    if not wlan.isconnected():
        print('connecting to network...')
        wlan.active(True)
        wlan.connect(SSID, SSID_PASSWORD)
        while not wlan.isconnected():
            if timerBeforeRestart <=0:
                reset()
            print("Attempting to connect....")
            flash(1000,1)
            timerBeforeRestart = timerBeforeRestart -1
    print('Connected! Network config:', wlan.ifconfig())


#####
# General Settings
#####
waitingTimeinS = 60
network.country('DE')
wlan = network.WLAN(network.STA_IF)

SSID = private.SSID
SSID_PASSWORD = private.SSID_PASSWORD

mqtt_client_id = private.mqtt_client_id
mqtt_host_hass = private.mqtt_host_hass
mqtt_username_hass = private.mqtt_username_hass
mqtt_password_hass = private.mqtt_password_hass
mqtt_publish_topic_hass_co2 = "/office/co2/"
mqtt_publish_topic_hass_voc = "/office/voc/"
mqtt_publish_topic_hass_temp = "/office/temp/"
mqtt_publish_topic_hass_pres = "/office/pres/"
mqtt_publish_topic_hass_humi = "/office/humi/"
mqtt_client_hass = MQTTClient(
    client_id=mqtt_client_id,
    server=mqtt_host_hass,
    user=mqtt_username_hass,
    password=mqtt_password_hass)

# Variablen für die intelligente Logik
voc_baseline = load_baseline() # Lade den gespeicherten Basiswert beim Start
last_ntp_sync = 0
time_synced = False
last_save_time = utime.time() # Zeit des letzten Speicherns merken
consecutive_errors = 0 # Fehlerzähler

# --- Trends ---
# Wir speichern die letzten 5 Messwerte
history_size = 5
co2_history = []
voc_history = []
temp_history = []
humi_history = []

# Start-Annahme für die Trends
co2_trend = '→'
voc_trend = '→'
temp_trend = '→'
humi_trend = '→'


#####
# BME 680
#####
bme680_i2c_sda = Pin(20)
bme680_i2c_scl = Pin(21)
sealevelpressure = 1012.25
# Stelle sicher, dass du den richtigen I2C-Bus (0 oder 1) verwendest
bme680 = BME680_I2C(I2C(0, sda=bme680_i2c_sda, scl=bme680_i2c_scl, freq=100000))
temperature_offset = -3.3
if 'sealevelpressure' in locals():
    bme680.sea_level_pressure = sealevelpressure
degreecels = '\u00B0' + "C"


#####
# MHZ19C
#####
mhz_tx_pin = Pin(4)
mhz_rx_pin = Pin(5)
# Stelle sicher, dass du den richtigen UART-Bus (0 oder 1) verwendest
mhz = MHZ19BSensor(tx_pin=mhz_tx_pin, rx_pin=mhz_rx_pin)

#####
# ePaper-Display
#####
epd = EPD_2in7()


##########
# Main
##########

# Verbinde initial mit dem WLAN
connectWifi()


# --- main loop ---
while True:
    try:
        # ----- Zeit-Synchronisation (NTP) -----
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
            current_time = utime.localtime(utime.time() + 7200) # UTC+2 für Sommerzeit
            zeit_str = f"{current_time[3]:02d}:{current_time[4]:02d}"
        
        # Periodisches Speichern des Basiswerts
        if utime.time() - last_save_time > 1800: # Alle 30 Minuten
            save_baseline(voc_baseline)
            last_save_time = utime.time()

        # --- ERWEITERT: Stabile Trend-Analyse für alle Werte ---
        # 1. Historien aktualisieren
        co2_history.append(co2)
        voc_history.append(luftguete_prozent)
        temp_history.append(temp)
        humi_history.append(humi)
        
        # Sicherstellen, dass die Historien nicht zu lang werden
        if len(co2_history) > history_size:
            co2_history.pop(0)
            voc_history.pop(0)
            temp_history.pop(0)
            humi_history.pop(0)

        # 2. Trends nur berechnen, wenn wir genug Daten haben
        if len(co2_history) == history_size:
            co2_avg = sum(co2_history) / history_size
            voc_avg = sum(voc_history) / history_size
            temp_avg = sum(temp_history) / history_size
            humi_avg = sum(humi_history) / history_size
            
            # Hysterese-Schwellen definieren
            co2_hysteresis = 20
            voc_hysteresis = 5
            temp_hysteresis = 0.5 # Grad Celsius
            humi_hysteresis = 2   # Prozentpunkte
            
            # CO2-Trend
            if co2 > co2_avg + co2_hysteresis: co2_trend = '↑'
            elif co2 < co2_avg - co2_hysteresis: co2_trend = '↓'
            else: co2_trend = '→'

            # VOC-Trend
            if luftguete_prozent < voc_avg - voc_hysteresis: voc_trend = '↓'
            elif luftguete_prozent > voc_avg + voc_hysteresis: voc_trend = '↑'
            else: voc_trend = '→'
            
            # Temperatur-Trend
            if temp > temp_avg + temp_hysteresis: temp_trend = '↑'
            elif temp < temp_avg - temp_hysteresis: temp_trend = '↓'
            else: temp_trend = '→'

            # Feuchtigkeits-Trend
            if humi > humi_avg + humi_hysteresis: humi_trend = '↑'
            elif humi < humi_avg - humi_hysteresis: humi_trend = '↓'
            else: humi_trend = '→'




        ###
        # 3. MQTT Daten senden
        ###
        mqtt_client_hass.publish(mqtt_publish_topic_hass_co2, str(co2))
        mqtt_client_hass.publish(mqtt_publish_topic_hass_voc, str(voc))
        mqtt_client_hass.publish(mqtt_publish_topic_hass_temp, str(temp))
        mqtt_client_hass.publish(mqtt_publish_topic_hass_humi, str(humi))

        ###
        # 4. Dashboard zeichnen
        ###
        epd.image4Gray.fill(epd.white)
        epd.image4Gray.text("RAUMKLIMA", 15, 8, epd.black)
        epd.image4Gray.text(zeit_str, 120, 8, epd.black)
        epd.image4Gray.hline(8, 24, 160, epd.black)
        
        epd.image4Gray.text(f"CO2 ({co2_trend})", 15, 45, epd.black)
        epd.image4Gray.text(f"{co2} ppm", 100, 45, epd.black)
        epd.image4Gray.text(f"({co2_bewertung})", 65, 65, epd.black)
        epd.image4Gray.hline(8, 90, 160, epd.black)
        
        y_pos = 110
        epd.image4Gray.text(f"Temperatur ({temp_trend})", 15, y_pos, epd.black)
        epd.image4Gray.text(f"{temp:.1f} C", 115, y_pos, epd.black)
        epd.image4Gray.hline(15, y_pos + 20, 146, epd.black)
        
        y_pos += 35
        epd.image4Gray.text(f"Feuchtigkeit ({humi_trend})", 15, y_pos, epd.black)
        epd.image4Gray.text(f"{humi:.1f} %", 115, y_pos, epd.black)
        epd.image4Gray.hline(15, y_pos + 20, 146, epd.black)
        
        y_pos += 35
        epd.image4Gray.text(f"Luftguete ({voc_trend})", 15, y_pos, epd.black)
        epd.image4Gray.text(f"{luftguete_prozent:.0f} %", 115, y_pos, epd.black)

        epd.EPD_2IN7_4Gray_Display(epd.buffer_4Gray)
        print("Dashboard aktualisiert.")
        
        print(f"CO2: {co2} ppm ({co2_bewertung}), Temp: {temp:.1f} C, rH: {humi:.1f} %, Luftguete: {luftguete_prozent:.0f}% (VOC: {voc} Ohm, Base: {voc_baseline:.0f})")
        print("============\n")

        consecutive_errors = 0

    except (OSError, MQTTException) as e:
        consecutive_errors += 1
        print(f"FEHLER ({consecutive_errors}/3): {e}.")

        if consecutive_errors >= 3:
            print("Drei aufeinanderfolgende Fehler. Führe einen Neustart durch...")
            sleep(1)
            reset()
        else:
            print("Fehler wird vorübergehend ignoriert. Nächster Versuch in Kürze.")
        
        # Setze die MQTT-Verbindung zurück, damit sie im nächsten Durchlauf neu versucht wird
        if mqtt_client_hass is not None and mqtt_client_hass.sock is not None:
            try:
                mqtt_client_hass.sock.close()
            except OSError:
                pass
        mqtt_client_hass.sock = None
        sleep(3)

    gc.collect()
    print(f"Warte {waitingTimeinS} Sekunden bis zur nächsten Messung...")
    sleep(waitingTimeinS)