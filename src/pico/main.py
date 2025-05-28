from mfrc522 import MFRC522
import utime
import _thread
import network
import time
import ntptime
from umqtt.simple import MQTTClient

# MQTT and Wi-Fi settings
topic_pub = b"rfid/scan"
MQTT_CLIENT_ID = b"pico_client"
WIFI_SSID = "w"
WIFI_PASSWORD = "12345678"
MQTT_BROKER = "192.168.90.159"
MQTT_PORT = 1883

# Rest interval settings
REST_MS = 2000
last_card = None
last_call_ts = 0

# Thread-safe queue
_request_queue = []
_queue_lock = _thread.allocate_lock()

# RF reader init
reader = MFRC522(spi_id=0, sck=6, miso=4, mosi=7, cs=5, rst=22)

# Initialize and connect Wi-Fi
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    while not wlan.isconnected():
        print("Connecting to WiFi…")
        time.sleep(1)
    print("WiFi connected, IP:", wlan.ifconfig()[0])

# MQTT client (publish only)
mqtt = MQTTClient(MQTT_CLIENT_ID, MQTT_BROKER, port=MQTT_PORT, keepalive=60)

# Enqueue/dequeue helpers
def enqueue_card(card):
    _queue_lock.acquire()
    _request_queue.append(card)
    _queue_lock.release()

def dequeue_card():
    _queue_lock.acquire()
    card = _request_queue.pop(0) if _request_queue else None
    _queue_lock.release()
    return card

# Worker thread: publish scans
def callback_worker():
    while True:
        card = dequeue_card()
        if card is not None:
            print("card ID: ", str(card))
            try:
                mqtt.publish(topic_pub, str(card), qos=1)
            except OSError:
                # reconnect and retry once
                try:
                    mqtt.connect()
                    mqtt.publish(topic_pub, str(card), qos=1)
                except Exception as e:
                    print("MQTT publish failed after retry:", e)
        else:
            utime.sleep_ms(50)

# Start worker on second core
def start_worker():
    _thread.start_new_thread(callback_worker, ())

# Main logic
def main():
    global last_card, last_call_ts
    connect_wifi()
    # sync time
    try:
        ntptime.settime()
    except:
        pass

    # ensure MQTT connection
    while True:
        try:
            mqtt.connect()
            break
        except OSError as e:
            print("MQTT connect failed, retrying…", e)
            time.sleep(2)

    start_worker()
    print("worker started")

    while True:
        reader.init()
        (stat, _) = reader.request(reader.REQIDL)
        if stat == reader.OK:
            (stat, uid) = reader.SelectTagSN()
            if stat == reader.OK:
                card = int.from_bytes(bytes(uid), "little", False)
                now = utime.ticks_ms()
                if card != last_card or utime.ticks_diff(now, last_call_ts) >= REST_MS:
                    enqueue_card(card)
                    last_card = card
                    last_call_ts = now
        utime.sleep_ms(100)

if __name__ == '__main__':
    main()