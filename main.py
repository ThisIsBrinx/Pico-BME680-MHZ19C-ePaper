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
led = Pin('LED', Pin.OUT, value=0)
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
    if not wlan.isconnected():
        print('connecting to network...')
        wlan.active(True)
        wlan.connect(SSID, SSID_PASSWORD)
        # Versuche max. 10 Sekunden lang zu verbinden
        for _ in range(10):
            if wlan.isconnected():
                break
            print("Attempting to connect....")
            flash(500, 1)
    if wlan.isconnected():
        print('Connected! Network config:', wlan.ifconfig())
        return True
    else:
        print('Connection failed or timed out. Operating in offline mode.')
        return False


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
consecutive_sensor_errors = 0 # Sensor-Fehlerzähler

# Sensor-Fallback-Werte
co2 = 800
temp = 20.0
humi = 50.0
voc = 50000.0

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
temperature_offset = -3.3
degreecels = '\u00B0' + "C"
bme680 = None

#####
# MHZ19C
#####
mhz_tx_pin = Pin(4)
mhz_rx_pin = Pin(5)
mhz = None

#####
# ePaper-Display
#####
epd = None


##########
# Main
##########

# Verbinde initial mit dem WLAN
connectWifi()


# --- main loop ---
while True:
    # ----- 1. Zeit-Synchronisation (NTP) -----
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

    # ----- 2. Sensoren auslesen -----
    sensor_errors = 0
    
    # BME680 auslesen
    try:
        if bme680 is None:
            print("Initialisiere BME680...")
            bme680 = BME680_I2C(I2C(0, sda=bme680_i2c_sda, scl=bme680_i2c_scl, freq=100000))
            if 'sealevelpressure' in locals():
                bme680.sea_level_pressure = sealevelpressure
        
        temp = bme680.temperature + temperature_offset
        humi = bme680.humidity
        voc = bme680.gas
    except Exception as e:
        sensor_errors += 1
        print(f"Fehler beim BME680 (Initialisierung oder Lesen): {e}")
        bme680 = None
        
    # MH-Z19C auslesen
    try:
        if mhz is None:
            print("Initialisiere MH-Z19C...")
            mhz = MHZ19BSensor(tx_pin=mhz_tx_pin, rx_pin=mhz_rx_pin)
        
        co2 = mhz.measure()[0]
    except Exception as e:
        sensor_errors += 1
        print(f"Fehler beim MH-Z19C (Initialisierung oder Lesen): {e}")
        mhz = None

    # ----- 3. Daten interpretieren & Logik für Dauerbetrieb -----
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

    # ----- 4. MQTT Daten senden -----
    if wlan.isconnected():
        try:
            if mqtt_client_hass.sock is None:
                print("MQTT-Verbindung wird aufgebaut...")
                mqtt_client_hass.connect()
                print("MQTT verbunden.")
            
            mqtt_client_hass.publish(mqtt_publish_topic_hass_co2, str(co2))
            mqtt_client_hass.publish(mqtt_publish_topic_hass_voc, str(voc))
            mqtt_client_hass.publish(mqtt_publish_topic_hass_temp, str(temp))
            mqtt_client_hass.publish(mqtt_publish_topic_hass_humi, str(humi))
            print("MQTT-Daten erfolgreich übertragen.")
        except (OSError, MQTTException) as e:
            print(f"MQTT-Fehler: {e}. Verbindung wird zurückgesetzt.")
            if mqtt_client_hass.sock is not None:
                try:
                    mqtt_client_hass.sock.close()
                except OSError:
                    pass
            mqtt_client_hass.sock = None
    else:
        print("Kein WLAN vorhanden. Überspringe MQTT-Versand.")
        connectWifi()

    # ----- 5. Dashboard auf ePaper zeichnen -----
    try:
        if epd is None:
            print("Initialisiere ePaper-Display...")
            epd = EPD_2in7()
            
        epd.EPD_2IN7_Init_4Gray()
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
        epd.Sleep()
    except Exception as e:
        print(f"Fehler bei Display-Aktualisierung: {e}")
        epd = None

    # ----- 6. Systemstabilität & Konsolenausgabe -----
    print(f"CO2: {co2} ppm ({co2_bewertung}), Temp: {temp:.1f} C, rH: {humi:.1f} %, Luftguete: {luftguete_prozent:.0f}% (VOC: {voc} Ohm, Base: {voc_baseline:.0f})")
    print("============\n")

    if sensor_errors > 0:
        consecutive_sensor_errors += 1
        print(f"Warnung: Sensor-Lesefehler festgestellt ({consecutive_sensor_errors}/5).")
        if consecutive_sensor_errors >= 5:
            print("Kritischer Fehler: Sensoren antworten seit 5 Zyklen nicht. Führe Neustart durch...")
            utime.sleep(1)
            reset()
    else:
        consecutive_sensor_errors = 0

    gc.collect()
    print(f"Warte {waitingTimeinS} Sekunden bis zur nächsten Messung...")
    utime.sleep(waitingTimeinS)