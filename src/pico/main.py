from mfrc522 import MFRC522
import utime
import _thread

# ——————————————————————————————————————————————
# Shared queue + lock for passing card IDs to the callback thread
# ——————————————————————————————————————————————
_request_queue = []
_queue_lock = _thread.allocate_lock()

def enqueue_card(card_id):
    """Thread-safe enqueue of a card ID."""
    _queue_lock.acquire()
    _request_queue.append(card_id)
    _queue_lock.release()

def dequeue_card():
    """Thread-safe pop of a card ID, or None if empty."""
    _queue_lock.acquire()
    card = _request_queue.pop(0) if _request_queue else None
    _queue_lock.release()
    return card

# ——————————————————————————————————————————————
# Your callback worker — runs on the other core
# ——————————————————————————————————————————————
def callback_worker():
    while True:
        card = dequeue_card()
        if card is not None:
            # <-- this runs on core 1
            print("CALLBACK: CARD ID →", card)
            # in the future, replace the above with your MQTT publish
        else:
            # nothing to do — yield a bit of time
            utime.sleep_ms(50)

# start the callback thread on the 2nd core
_thread.start_new_thread(callback_worker, ())


# ——————————————————————————————————————————————
# Main loop: scan + enqueue with “rest timer”
# ——————————————————————————————————————————————
reader = MFRC522(spi_id=0, sck=6, miso=4, mosi=7, cs=5, rst=22)

REST_MS       = 2000    # rest interval for the same card
last_card     = None
last_call_ts  = 0       # utime.ticks_ms()

print("Bring TAG closer…\n")

while True:
    reader.init()
    (stat, tag_type) = reader.request(reader.REQIDL)
    if stat == reader.OK:
        (stat, uid) = reader.SelectTagSN()
        if stat == reader.OK:
            card = int.from_bytes(bytes(uid), "little", False)
            now = utime.ticks_ms()

            # if brand-new card, or rest interval has elapsed:
            if (card != last_card) or (utime.ticks_diff(now, last_call_ts) >= REST_MS):
                enqueue_card(card)
                last_card    = card
                last_call_ts = now

    # small delay to avoid thrashing the SPI bus
    utime.sleep_ms(50)

