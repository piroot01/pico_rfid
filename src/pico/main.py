from mfrc522 import MFRC522
import utime
import _thread
import network
import time
import ntptime
from umqtt.simple import MQTTClient

# MQTT and Wi-Fi settings
topic_pub      = b"rfid/scan"
control_sub    = b"rfid/control"
MQTT_CLIENT_ID = b"pico_client"
WIFI_SSID      = "w"
WIFI_PASSWORD  = "12345678"
MQTT_BROKER    = "192.168.90.159"
MQTT_PORT      = 1883

# Debounce interval
REST_MS = 2000
last_card     = None
last_call_ts  = 0

# Thread-safe queue
_request_queue = []
_queue_lock    = _thread.allocate_lock()

# RFID init
reader = MFRC522(spi_id=0, sck=6, miso=4, mosi=7, cs=5, rst=22)

scanning_enabled = False

# MQTT client
mqtt = MQTTClient(MQTT_CLIENT_ID, MQTT_BROKER, port=MQTT_PORT, keepalive=60)

def control_cb(topic, msg):
    global scanning_enabled
    print("Control msg:", msg)
    scanning_enabled = (msg == b"start")

mqtt.set_callback(control_cb)

# Wi-Fi connect
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    while not wlan.isconnected():
        print("Connecting to WiFi…")
        time.sleep(1)
    print("WiFi connected, IP:", wlan.ifconfig()[0])

# Enqueue/dequeue helpers
def enqueue_card(card):
    with _queue_lock:
        _request_queue.append(card)

def dequeue_card():
    with _queue_lock:
        return _request_queue.pop(0) if _request_queue else None

# Worker: publishes queued cards with QoS0 to avoid blocking on PUBACK
def callback_worker():
    global mqtt
    while True:
        card = dequeue_card()
        if card is not None:
            print("Publishing card:", card)
            try:
                # Use QoS 0 to avoid wait_msg blocking
                mqtt.publish(topic_pub, str(card), qos=0)
            except OSError as e:
                print("Publish failed, reconnecting…", e)
                try:
                    mqtt.disconnect()
                except:
                    pass
                # Reconnect and resubscribe control topic
                while True:
                    try:
                        mqtt.connect()
                        mqtt.subscribe(control_sub)
                        break
                    except Exception as e2:
                        print("Reconnect failed, retrying…", e2)
                        time.sleep(2)
                # retry publish
                try:
                    mqtt.publish(topic_pub, str(card), qos=0)
                except Exception as e3:
                    print("Publish retry failed:", e3)
        else:
            utime.sleep_ms(200)

# Start worker on second core
def start_worker():
    _thread.start_new_thread(callback_worker, ())

# Main loop
def main():
    global last_card, last_call_ts

    connect_wifi()
    try:
        ntptime.settime()
    except:
        pass

    # Connect and subscribe
    while True:
        try:
            mqtt.connect()
            mqtt.subscribe(control_sub)
            break
        except Exception as e:
            print("MQTT connect/subscribe failed, retrying…", e)
            time.sleep(2)

    start_worker()
    print("Worker thread started, entering main loop")

    while True:
        # pump control messages
        try:
            mqtt.check_msg()
        except:
            pass

        # read RFID
        reader.init()
        stat, _ = reader.request(reader.REQIDL)
        if stat == reader.OK and scanning_enabled:
            stat, uid = reader.SelectTagSN()
            if stat == reader.OK:
                card = int.from_bytes(bytes(uid), "little")
                now = utime.ticks_ms()
                if card != last_card or utime.ticks_diff(now, last_call_ts) >= REST_MS:
                    enqueue_card(card)
                    last_card    = card
                    last_call_ts = now

        utime.sleep_ms(200)

if __name__ == '__main__':
    main()
