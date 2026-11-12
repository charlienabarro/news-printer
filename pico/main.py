"""Morning brief printer for a Raspberry Pi Pico W.

Joins WiFi and syncs the clock. At TRIGGER_HOUR:TRIGGER_MINUTE it asks GitHub
to build a fresh brief (GitHub's own scheduler runs hours late, so the Pico
starts the job itself). From PRINT_HOUR it downloads brief.txt and prints it
on a 58mm ESC/POS thermal printer over UART.

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

# Printer tuning. If lines still go missing, raise LINE_PAUSE_MS.
# If text is broken or dotty, lower HEAT_DOTS; if it's too faint, raise HEAT_TIME.
LINE_PAUSE_MS = 300
HEAT_DOTS = 3        # (3+1)*8 = 32 dots heated at once
HEAT_TIME = 140      # x10us
HEAT_INTERVAL = 60   # x10us
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
        # The query string stops GitHub's CDN handing back a cached old copy.
        r = urequests.get(config.BRIEF_URL + "?t=%d" % time.time())
        text = r.text if r.status_code == 200 else None
        r.close()
        return text
    except Exception as e:
        print("Fetch failed:", e)
        return None


def trigger_brief():
    """Start the GitHub Actions workflow that builds brief.txt. Returns True on success."""
    if not connect_wifi():
        return False
    url = "https://api.github.com/repos/%s/actions/workflows/brief.yml/dispatches" % config.GITHUB_REPO
    headers = {
        "Authorization": "Bearer " + config.GITHUB_TOKEN,
        "Accept": "application/vnd.github+json",
        "User-Agent": "news-printer-pico",
    }
    try:
        r = urequests.post(url, headers=headers, json={"ref": "main"})
        ok = r.status_code == 204
        if not ok:
            print("Trigger failed:", r.status_code, r.text[:200])
        r.close()
        if ok:
            print("Triggered brief build")
        return ok
    except Exception as e:
        print("Trigger failed:", e)
        return False


# ---------- printer (ESC/POS) ----------

def send(b):
    uart.write(b)


def reset():
    send(b"\x1b@")                       # initialise
    time.sleep_ms(50)
    # Heating settings: heat fewer dots at once so a 5V supply doesn't sag.
    # ESC 7 n1 n2 n3 = max heating dots ((n1+1)*8), heat time, heat interval
    send(b"\x1b7" + bytes([HEAT_DOTS, HEAT_TIME, HEAT_INTERVAL]))
    time.sleep_ms(50)


def align(n):
    send(b"\x1ba" + bytes([n]))          # 0 left, 1 centre


def bold(on):
    send(b"\x1bE" + bytes([1 if on else 0]))


def size(double_w=False, double_h=False):
    send(b"\x1d!" + bytes([(0x10 if double_w else 0) | (0x01 if double_h else 0)]))


def line(text="", pause_ms=LINE_PAUSE_MS):
    # The printer has a small buffer and no flow control, so go one line at a time.
    send(text.encode("ascii", "ignore") + b"\n")
    time.sleep_ms(pause_ms)


def feed(n):
    send(b"\x1bd" + bytes([n]))
    time.sleep_ms(LINE_PAUSE_MS * n)


def print_brief(text, stale=False):
    lines = text.strip().split("\n")
    date = lines[0][6:].strip() if lines and lines[0].startswith("@DATE") else ""
    body = lines[1:] if date else lines

    reset()
    align(1)
    bold(True)
    size(True, True)
    line("MORNING", LINE_PAUSE_MS * 3)
    line("BRIEF", LINE_PAUSE_MS * 3)
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
            line(raw[2:], LINE_PAUSE_MS * 2)
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


def is_fresh(text, today):
    return bool(text) and text.startswith("@DATE " + today)


def do_print(text, today):
    led.on()
    print_brief(text, stale=not is_fresh(text, today))
    led.off()


# ---------- main loop ----------

def main():
    connect_wifi()
    while not sync_clock():
        time.sleep(30)

    last_check = 0
    last_trigger = 0
    last_ntp = time.time()
    while True:
        now = london_now()
        today = today_str(now)

        # Manual reprint
        if rp2.bootsel_button():
            print("Button pressed, printing")
            text = fetch_brief()
            if text:
                do_print(text, today)
            time.sleep(2)

        hm = (now[3], now[4])
        started = hm >= (config.TRIGGER_HOUR, config.TRIGGER_MINUTE)
        due = hm >= (config.PRINT_HOUR, config.PRINT_MINUTE)

        # From trigger time until today's brief is printed, check every 5 minutes
        if started and read_last() != today and time.time() - last_check > 300:
            last_check = time.time()
            text = fetch_brief()
            fresh = is_fresh(text, today)

            # Not built yet: ask GitHub to build it (again every 20 minutes if needed)
            if not fresh and time.time() - last_trigger > 1200:
                if trigger_brief():
                    last_trigger = time.time()

            if due and text and (fresh or now[3] >= config.GIVE_UP_HOUR):
                do_print(text, today)
                write_last(today)
            elif due:
                print("Brief not updated yet, will retry")

        # Resync the clock once a day so it doesn't drift
        if time.time() - last_ntp > 86400:
            if sync_clock():
                last_ntp = time.time()

        time.sleep_ms(200)


main()
