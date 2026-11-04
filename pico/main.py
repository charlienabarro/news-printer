"""Morning brief printer for a Raspberry Pi Pico W.

Joins WiFi, syncs the clock, and at PRINT_HOUR each day downloads brief.txt
from GitHub and prints it on a 58mm ESC/POS thermal printer over UART.

Press the BOOTSEL button at any time to reprint the latest brief.
"""

import time

import network
import ntptime
import rp2
import urequests
from machine import Pin, UART

import config

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
WIDTH = 32
LAST_FILE = "last_printed.txt"

led = Pin("LED", Pin.OUT)
uart = UART(0, baudrate=config.BAUD, tx=Pin(config.UART_TX_PIN), rx=Pin(config.UART_RX_PIN))


# ---------- time ----------

def last_sunday(year, month):
    """Day of month of the last Sunday in a given month."""
    for day in range(31, 24, -1):
        try:
            t = time.mktime((year, month, day, 0, 0, 0, 0, 0))
        except OverflowError:
            continue
        lt = time.gmtime(t)
        if lt[1] == month and lt[6] == 6:  # weekday 6 = Sunday
            return day
    return 25


def london_now():
    """UK local time: UTC, plus one hour during British Summer Time."""
    utc = time.time()
    y = time.gmtime(utc)[0]
    bst_start = time.mktime((y, 3, last_sunday(y, 3), 1, 0, 0, 0, 0))
    bst_end = time.mktime((y, 10, last_sunday(y, 10), 1, 0, 0, 0, 0))
    offset = 3600 if bst_start <= utc < bst_end else 0
    return time.gmtime(utc + offset)


def today_str(t):
    return "%04d-%02d-%02d" % (t[0], t[1], t[2])


def nice_date(iso):
    y, m, d = (int(x) for x in iso.split("-"))
    wd = time.gmtime(time.mktime((y, m, d, 12, 0, 0, 0, 0)))[6]
    return "%s %d %s" % (DAYS[wd], d, MONTHS[m - 1])


# ---------- network ----------

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return True
    wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
    for _ in range(30):
        if wlan.isconnected():
            print("WiFi connected:", wlan.ifconfig()[0])
            return True
        led.toggle()
        time.sleep(1)
    print("WiFi failed")
    return False


def sync_clock():
    for _ in range(5):
        try:
            ntptime.settime()
            print("Clock synced:", today_str(london_now()))
            return True
        except Exception as e:
            print("NTP failed:", e)
            time.sleep(5)
    return False


def fetch_brief():
    if not connect_wifi():
        return None
    try:
        r = urequests.get(config.BRIEF_URL)
        text = r.text if r.status_code == 200 else None
        r.close()
        return text
    except Exception as e:
        print("Fetch failed:", e)
        return None


# ---------- printer (ESC/POS) ----------

def send(b):
    uart.write(b)


def reset():
    send(b"\x1b@")                       # initialise


def align(n):
    send(b"\x1ba" + bytes([n]))          # 0 left, 1 centre


def bold(on):
    send(b"\x1bE" + bytes([1 if on else 0]))


def size(double_w=False, double_h=False):
    send(b"\x1d!" + bytes([(0x10 if double_w else 0) | (0x01 if double_h else 0)]))


def line(text="", pause_ms=70):
    # The printer has a small buffer and no flow control, so go one line at a time.
    send(text.encode("ascii", "ignore") + b"\n")
    time.sleep_ms(pause_ms)


def feed(n):
    send(b"\x1bd" + bytes([n]))
    time.sleep_ms(60 * n)


def print_brief(text, stale=False):
    lines = text.strip().split("\n")
    date = lines[0][6:].strip() if lines and lines[0].startswith("@DATE") else ""
    body = lines[1:] if date else lines

    reset()
    align(1)
    bold(True)
    size(True, True)
    line("MORNING", 150)
    line("BRIEF", 150)
    size()
    bold(False)
    if date:
        line(nice_date(date))
    if stale:
        line("(today's brief not ready)")
    line("-" * WIDTH)
    line()
    align(0)

    for raw in body:
        if raw.startswith("# "):
            line()
            bold(True)
            size(False, True)
            line(raw[2:], 140)
            size()
            bold(False)
            line("=" * WIDTH)
        elif raw.startswith("* "):
            bold(True)
            line(raw[2:])
            bold(False)
        else:
            line(raw)

    line()
    align(1)
    line("-" * WIDTH)
    align(0)
    feed(4)  # push the end past the tear bar


# ---------- state ----------

def read_last():
    try:
        with open(LAST_FILE) as f:
            return f.read().strip()
    except OSError:
        return ""


def write_last(day):
    with open(LAST_FILE, "w") as f:
        f.write(day)


def try_print(force=False):
    """Fetch and print. Returns True once something has been printed."""
    now = london_now()
    today = today_str(now)
    text = fetch_brief()
    if not text:
        return False
    fresh = text.startswith("@DATE " + today)
    if fresh or force or now[3] >= config.GIVE_UP_HOUR:
        led.on()
        print_brief(text, stale=not fresh)
        led.off()
        return True
    print("Brief not updated yet, will retry")
    return False


# ---------- main loop ----------

def main():
    connect_wifi()
    while not sync_clock():
        time.sleep(30)

    last_check = 0
    last_ntp = time.time()
    while True:
        # Manual reprint
        if rp2.bootsel_button():
            print("Button pressed, printing")
            try_print(force=True)
            time.sleep(2)

        now = london_now()
        today = today_str(now)
        due = (now[3], now[4]) >= (config.PRINT_HOUR, config.PRINT_MINUTE)

        # Check every 10 minutes once it's due and today hasn't been printed
        if due and read_last() != today and time.time() - last_check > 600:
            last_check = time.time()
            if try_print():
                write_last(today)

        # Resync the clock once a day so it doesn't drift
        if time.time() - last_ntp > 86400:
            if sync_clock():
                last_ntp = time.time()

        time.sleep_ms(200)


main()
