"""Morning brief printer for a Raspberry Pi Pico W.

Joins WiFi and syncs the clock. At TRIGGER_HOUR:TRIGGER_MINUTE it asks GitHub
to build a fresh brief (GitHub's own scheduler runs hours late, so the Pico
starts the job itself). From PRINT_HOUR it downloads brief.txt and prints it
on a 58mm ESC/POS thermal printer over UART.

Press the BOOTSEL button at any time to reprint the latest brief.

Also runs the sunrise lamp: a WiZ bulb fades up over the 30 minutes before
each wake-up. An iPhone Shortcut sends tomorrow's first calendar event every
night (/plan?first=0915) and the Pico works out the wake-up time from that.
Open http://sunrise.local on a phone to see the next wake-up, change the
settings, set a one-off time, run a 1-minute test sunrise or turn the lamp off.
"""

import io
import json
import socket
import sys
import time

import machine
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
    try:
        network.hostname("sunrise")      # reachable as sunrise.local
    except Exception:
        pass
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    try:
        # No WiFi power saving, so the lamp's web page and the bulb's replies get through
        wlan.config(pm=getattr(network.WLAN, "PM_NONE", 0xa11140))
    except Exception:
        pass
    if wlan.isconnected():
        return True
    wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
    for _ in range(30):
        if wlan.isconnected():
            note("wifi", True, "connected (IP %s)" % wlan.ifconfig()[0])
            return True
        led.toggle()
        time.sleep(1)
    note("wifi", False, "couldn't connect (WiFi status %s)" % wlan.status())
    return False


def sync_clock():
    for _ in range(5):
        try:
            ntptime.settime()
            print("Clock synced:", today_str(london_now()))
            return True
        except Exception as e:
            note("clock", False, "time sync failed: %s" % e)
            time.sleep(5)
    return False


def fetch_brief():
    if not connect_wifi():
        return None
    try:
        # The query string stops GitHub's CDN handing back a cached old copy.
        r = urequests.get(config.BRIEF_URL + "?t=%d" % time.time())
        text = r.text if r.status_code == 200 else None
        if text is None:
            note("download", False, "GitHub answered HTTP %d" % r.status_code)
        r.close()
        return text
    except Exception as e:
        note("download", False, "failed: %s" % e)
        return None


def trigger_brief():
    """Start the GitHub Actions workflow that builds brief.txt. Returns True on success."""
    if not connect_wifi():
        return False
    url = "https://api.github.com/repos/%s/actions/workflows/brief.yml/dispatches" % config.GITHUB_REPO
    headers = {
        "Authorization": "Bearer " + config.GITHUB_TOKEN,
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "news-printer-pico",
    }
    try:
        r = urequests.post(url, headers=headers, json={"ref": "main"})
        ok = r.status_code == 204
        if not ok:
            try:
                why = json.loads(r.text).get("message", "")  # e.g. "Bad credentials"
            except Exception:
                why = r.text[:120]
            note("build", False, "GitHub refused (HTTP %d) %s" % (r.status_code, why))
        r.close()
        if ok:
            note("build", True, "started on GitHub")
        return ok
    except Exception as e:
        note("build", False, "couldn't reach GitHub: %s" % e)
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
    stale = not is_fresh(text, today)
    print_brief(text, stale=stale)
    led.off()
    # The printer can't report back, so this only means the Pico sent it
    note("print", True, "sent to the printer" + (" (an old brief)" if stale else ""))


# ---------- status log (shown at http://sunrise.local) ----------
#
# A short record of what happened, so a problem can be seen from a phone without plugging
# the Pico into a laptop. Saved to STATUS_FILE so it survives a restart.

STATUS_FILE = "status.json"
LOG_SIZE = 25
status = {"log": [], "crashes": 0, "crash": None}  # log entries: [time, kind, ok, message, count]
started_ms = time.ticks_ms()


def note(kind, ok, msg):
    """Record an event and print it to the serial console."""
    print(("" if ok else "PROBLEM ") + kind + ": " + msg)
    log = status["log"]
    if log and log[-1][1] == kind and log[-1][3] == msg:
        log[-1][0] = time.time()  # the same thing again: just update the time and count
        log[-1][4] += 1
        return
    log.append([time.time(), kind, ok, msg, 1])
    while len(log) > LOG_SIZE:
        log.pop(0)
    save_status()


def save_status():
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(status, f)
    except OSError as e:
        print("Couldn't save status:", e)


def load_status():
    try:
        with open(STATUS_FILE) as f:
            status.update(json.load(f))
    except (OSError, ValueError):
        pass


def last_event(kind=None, ok=None):
    """The newest log entry of that kind and/or outcome, or None."""
    for entry in reversed(status["log"]):
        if (kind is None or entry[1] == kind) and (ok is None or entry[2] == ok):
            return entry
    return None


def when(t, now):
    """'07:40' for today, 'Sun 15 Nov 19:13' for another day."""
    if t < 1735689600:  # before 2025: logged before the clock was set
        return "at start-up"
    lt = time.gmtime(t + london_offset(t))
    day = (lt[0], lt[1], lt[2])
    hhmm = "%02d:%02d" % (lt[3], lt[4])
    return hhmm if day == london_day(now) else fmt_day(day) + " " + hhmm


def printer_summary(now):
    """(headline, all well?, detail lines) about the printer, for the page and /status."""
    t = london_now()
    hm_now = (t[3], t[4])
    if read_last() == today_str(t):
        p = last_event("print")
        head, ok = "Today's brief printed" + (" at " + when(p[0], now) if p else ""), True
    elif hm_now < (config.TRIGGER_HOUR, config.TRIGGER_MINUTE):
        head, ok = "Next brief: built at %02d:%02d, printed at %02d:%02d" % (
            config.TRIGGER_HOUR, config.TRIGGER_MINUTE, config.PRINT_HOUR, config.PRINT_MINUTE), True
    elif hm_now < (config.PRINT_HOUR, config.PRINT_MINUTE):
        head, ok = "Building today's brief, prints at %02d:%02d" % (config.PRINT_HOUR, config.PRINT_MINUTE), True
    else:
        head, ok = "Today's brief hasn't printed yet", False
    lines = []
    for kind, label in (("build", "Build"), ("download", "Download"), ("print", "Print")):
        e = last_event(kind)
        if e:
            lines.append("%s: %s, %s" % (label, when(e[0], now), e[3]))
    bad = last_event(ok=False)
    if bad:
        lines.append("Last problem: %s, %s: %s%s" % (
            when(bad[0], now), bad[1], bad[3], " (x%d)" % bad[4] if bad[4] > 1 else ""))
    return head, ok, lines


def reset_reason():
    cause = machine.reset_cause()
    if cause == getattr(machine, "PWRON_RESET", None):
        return "power on"
    if cause == getattr(machine, "WDT_RESET", None):
        return "restart"
    return "reset code %s" % cause


# ---------- sunrise lamp (WiZ bulb) ----------
#
# How the wake-up time is chosen (UK time; change the settings at http://sunrise.local):
#   weekdays: the usual time (08:30), or `lead` minutes before the first event if that is earlier
#   weekends: `lead` minutes before the first event, but only if it starts before 11:00
# Every night at 23:00 an iPhone Shortcut sends tomorrow's first timed calendar event
# (/plan?first=0915, or first=none). If the phone never sends it, weekdays still use the
# usual time. A one-off time set on the page replaces the rule for that one morning.
# The wake-up time is worked out afresh each time, so a changed setting applies at once.

BULB_MAC = "9877d5f10ef2"
WIZ_PORT = 38899
STATE_FILE = "lamp_state.json"
SETTINGS_FILE = "lamp_settings.json"
EARLIEST_EVENT = 4 * 60   # ignore events before 04:00, so a 00:30 event can't wake you at 23:30
LATE_GRACE = 600          # a wake-up more than 10 minutes in the past counts as missed
TEST_SECS = 60

DEFAULTS = {
    "weekday": "08:30",   # usual weekday wake-up
    "weekend": "11:00",   # weekends: only wake for an event that starts before this
    "lead": 60,           # minutes between wake-up and the first event
    "ramp": 30,           # sunrise length in minutes
    "off_after": 90,      # minutes after wake-up to turn the bulb off (0 = leave it on)
    "end_bright": 70,     # brightness (%) the sunrise finishes at
    "end_temp": 2700,     # warmth (K) the sunrise finishes at: 2200 candle, 2700 warm, 4000 daylight
}
LIMITS = {"lead": (0, 240), "ramp": (1, 120), "off_after": (0, 720),
          "end_bright": (10, 100), "end_temp": (2200, 6500)}

settings = dict(DEFAULTS)
plan = None       # [y, m, d, first event in minutes or None], sent by the Shortcut for that day
manual = None     # [y, m, d, wake-up in minutes or None], a one-off for that day
done = None       # [y, m, d] of the last morning whose sunrise finished or was stopped
off_at = None     # when to turn the bulb off after a wake-up
test_end = None   # end of a test sunrise
restore_to = None # how the lamp was before a test, to put it back afterwards (None = off)
sun_end = None    # end of the sunrise the bulb is following now
sun_level = None  # last settings the bulb confirmed during that sunrise
min_dim = None    # the bulb's lowest brightness (%), asked once
next_try = 0      # after the bulb fails to answer, wait until this time
bulb_ip = None
server = None
last_wifi = 0
last_tick = 0


# --- UK time helpers ---

def london_offset(utc):
    """Seconds to add to UTC to get UK clock time at that moment."""
    y = time.gmtime(utc)[0]
    bst_start = time.mktime((y, 3, last_sunday(y, 3), 1, 0, 0, 0, 0))
    bst_end = time.mktime((y, 10, last_sunday(y, 10), 1, 0, 0, 0, 0))
    return 3600 if bst_start <= utc < bst_end else 0


def london_day(utc, add=0):
    """UK date (y, m, d) at `utc`, moved on by `add` days."""
    t = time.gmtime(utc + london_offset(utc))
    return tuple(time.gmtime(time.mktime((t[0], t[1], t[2], 12, 0, 0, 0, 0)) + add * 86400)[:3])


def weekday(day):
    return time.gmtime(time.mktime((day[0], day[1], day[2], 12, 0, 0, 0, 0)))[6]  # 0 = Monday


def london_to_utc(day, mins):
    """UTC time of a UK clock time (minutes after midnight) on `day`."""
    t = time.mktime((day[0], day[1], day[2], mins // 60, mins % 60, 0, 0, 0))
    return t - london_offset(t - 3600)


def hm(text):
    """'08:30', '0830' or '830' -> minutes after midnight."""
    text = str(text).strip().replace(":", "")
    h, m = int(text[:-2] or "0"), int(text[-2:])
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError("not a time: " + text)
    return h * 60 + m


def fmt_hm(mins):
    return "%02d:%02d" % (mins // 60, mins % 60)


def fmt_day(day):
    return "%s %d %s" % (DAYS[weekday(day)][:3], day[2], MONTHS[day[1] - 1][:3])


# --- choosing the wake-up time ---

def rule_wake(day, first):
    """(minutes, reason) for `day` from the settings. `first` = first timed event, or None."""
    if first is not None and first < EARLIEST_EVENT:
        first = None
    lead = settings["lead"]
    before = "%d min before %s event" % (lead, fmt_hm(first)) if first is not None else ""
    if weekday(day) >= 5:
        if first is not None and first < hm(settings["weekend"]):
            return first - lead, before
        return None, "weekend, no event before " + settings["weekend"]
    usual = hm(settings["weekday"])
    if first is not None and first - lead < usual:
        return first - lead, before
    return usual, "usual weekday time"


def wake_for(day):
    """(minutes or None, reason) for one morning: a one-off wins, then the rule."""
    if manual and tuple(manual[:3]) == day:
        if manual[3] is None:
            return None, "cancelled for this morning"
        return manual[3], "one-off"
    if plan and tuple(plan[:3]) == day:
        return rule_wake(day, plan[3])
    mins, why = rule_wake(day, None)
    return mins, why + ", no calendar info yet"


def is_done(day):
    return done is not None and tuple(done) == day


def upcoming(now):
    """(UTC time or None, day) of the next wake-up: today's if still to come, else tomorrow's."""
    day = london_day(now)
    mins = wake_for(day)[0]
    if mins is not None and not is_done(day):
        t = london_to_utc(day, mins)
        if t >= now - LATE_GRACE:
            return t, day
    day = london_day(now, 1)
    mins = wake_for(day)[0]
    return (None if mins is None else london_to_utc(day, mins)), day


def describe(day):
    mins, why = wake_for(day)
    if mins is None:
        return "%s: no wake-up (%s)" % (fmt_day(day), why)
    return "%s: wake-up %s (%s)" % (fmt_day(day), fmt_hm(mins), why)


# The sunrise: deep red at the bulb's lowest brightness, slowly through orange and amber,
# then warm white rising 1% at a time to the brightness and warmth set on the page.
RED, ORANGE, AMBER = (255, 20, 0), (255, 90, 0), (255, 150, 30)


def mix(a, b, t):
    return a + (b - a) * t


def sunrise_params(f, low=10):
    """setPilot settings f of the way (0 to 1) through a sunrise. `low` = lowest brightness."""
    f = min(1, max(0, f))
    end = settings["end_bright"]
    glow = max(low, min(15, end))   # brightness by the time it's orange
    turn = max(glow, min(30, end))  # brightness when the colour turns to white
    if f < 0.35:  # deep red to orange, barely brightening
        t = f / 0.35
        colour, dim = [mix(a, b, t) for a, b in zip(RED, ORANGE)], mix(low, glow, t)
    elif f < 0.6:  # orange to amber
        t = (f - 0.35) / 0.25
        colour, dim = [mix(a, b, t) for a, b in zip(ORANGE, AMBER)], mix(glow, turn, t)
    else:  # warm white, brightening to the wake-up level
        t = (f - 0.6) / 0.4
        return {"temp": int(mix(2200, settings["end_temp"], t) / 10) * 10, "dimming": int(mix(turn, end, t))}
    return {"r": int(colour[0]), "g": int(colour[1]), "b": int(colour[2]), "dimming": int(dim)}


def sunrise_at(now, end, length, low=10):
    return sunrise_params(1 - (end - now) / length, low)


# --- talking to the bulb (UDP JSON on port 38899) ---

def broadcast_addrs():
    addrs = ["255.255.255.255"]
    try:
        ip, mask = network.WLAN(network.STA_IF).ifconfig()[:2]
        ip = [int(x) for x in ip.split(".")]
        mask = [int(x) for x in mask.split(".")]
        addrs.append(".".join([str(ip[i] | (255 - mask[i])) for i in range(4)]))
    except Exception:
        pass
    return addrs


def find_bulb():
    """Ask every WiZ bulb on the network to answer, and return the IP of ours (by MAC)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        try:
            s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_BROADCAST", 0x20), 1)
        except OSError:
            pass
        s.settimeout(0.5)
        msg = json.dumps({"method": "getPilot", "params": {}}).encode()
        for _ in range(4):
            for dest in broadcast_addrs():
                try:
                    s.sendto(msg, (dest, WIZ_PORT))
                except OSError:
                    pass
            try:
                while True:
                    data, addr = s.recvfrom(1024)
                    if json.loads(data).get("result", {}).get("mac") == BULB_MAC:
                        print("Bulb found:", addr[0])
                        return addr[0]
            except (OSError, ValueError):
                pass
        return None
    finally:
        s.close()


def wiz(method, params=None):
    """Send one command to the bulb, up to 3 tries (its signal is weak). Returns the reply or None."""
    global bulb_ip
    msg = json.dumps({"method": method, "params": params or {}}).encode()
    for attempt in range(3):
        if bulb_ip is None:
            bulb_ip = find_bulb()
            if bulb_ip is None:
                note("lamp", False, "bulb not found on the network")
                return None
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(1)
            s.sendto(msg, (bulb_ip, WIZ_PORT))
            return json.loads(s.recvfrom(1024)[0])
        except (OSError, ValueError):
            if attempt == 1:
                bulb_ip = None  # missed twice: its IP may have changed, so look it up again
        finally:
            s.close()
    return None


def set_light(on, dimming=100, temp=4000):
    params = {"state": on}
    if on:
        params["dimming"] = dimming
        params["temp"] = temp
    reply = wiz("setPilot", params)
    return bool(reply and reply.get("result", {}).get("success"))


def restore_params(pilot):
    """setPilot settings that put the lamp back as `pilot` describes, or None if it was off."""
    if not pilot or not pilot.get("state"):
        return None
    params = {"state": True, "dimming": pilot.get("dimming", 100)}
    if pilot.get("sceneId"):
        params["sceneId"] = pilot["sceneId"]
    elif pilot.get("temp"):
        params["temp"] = pilot["temp"]
    elif "r" in pilot:
        for key in ("r", "g", "b", "c", "w"):
            if key in pilot:
                params[key] = pilot[key]
    return params


def set_pilot(params):
    reply = wiz("setPilot", params)
    return bool(reply and reply.get("result", {}).get("success"))


def min_dimming():
    """The bulb's lowest brightness. Newer WiZ firmware reports it (some go down to 1%)."""
    global min_dim
    if min_dim is None:
        reply = wiz("getModelConfig")
        if reply is None:
            return 10  # no answer: assume the usual 10% and ask again next time
        try:
            min_dim = min(10, max(1, int(reply.get("result", {}).get("minDimLevel", 10))))
        except (TypeError, ValueError):
            min_dim = 10
    return min_dim


def get_light():
    """The bulb's state, e.g. {'state': True, 'dimming': 55, 'temp': 3100, ...}, or None."""
    reply = wiz("getPilot")
    return reply.get("result") if reply else None


# --- running the sunrise ---

def follow_sunrise(now, end, length):
    """Move the bulb to where the sunrise should be now.
    Returns "ok", "fail" (no answer from the bulb) or "stopped" (lamp changed by hand)."""
    global sun_end, sun_level
    if sun_end != end:
        sun_end, sun_level = end, None
        note("lamp", True, "sunrise started")
    level = sunrise_at(now, end, length, min_dimming())
    if level == sun_level:
        return "ok"
    if sun_level is not None:
        last = sun_level["dimming"]
        pilot = get_light()
        if pilot and (not pilot.get("state") or abs(pilot.get("dimming", last) - last) > 2):
            return "stopped"
    params = dict(level)
    params["state"] = True
    if not set_pilot(params):
        return "fail"
    sun_level = level
    return "ok"


def tick_lamp(now):
    """Run the sunrise and the auto-off. Called about once a second."""
    global test_end, done, off_at, next_try, sun_end, restore_to
    if now < next_try:
        return
    if test_end is not None and now > test_end + LATE_GRACE:
        test_end = None  # the bulb never answered: give up on the test
    if test_end is not None:
        end, length, day = test_end, TEST_SECS, None
    else:
        end, day = upcoming(now)
        length = settings["ramp"] * 60
        if end is not None and now < end - length:
            end = None
    if end is not None:
        result = follow_sunrise(now, end, length)
        if result == "fail":
            next_try = now + 10
        elif result == "stopped" or now >= end:
            note("lamp", True, "sunrise stopped: lamp changed by hand" if result == "stopped" else "sunrise finished")
            if day is None:
                test_end = None
                off_at = now + TEST_SECS if result == "ok" else None  # hold full brightness a minute
                if result != "ok":
                    restore_to = None
            else:
                done = list(day)
                mins = settings["off_after"]
                off_at = end + mins * 60 if mins and result == "ok" else None
            sun_end = None
            save_state()
        return
    if off_at is not None and now >= off_at:
        pilot = get_light()
        if pilot is None and now < off_at + LATE_GRACE:
            next_try = now + 10
            return
        # Only touch it if nobody has changed it since the sunrise finished
        if pilot and pilot.get("state") and pilot.get("dimming") == settings["end_bright"]:
            ok = set_pilot(restore_to) if restore_to else set_light(False)
            if not ok and now < off_at + LATE_GRACE:
                next_try = now + 10
                return
            note("lamp", True, "put back as it was before the test" if restore_to else "turned off automatically")
        off_at = None
        restore_to = None
        save_state()


# --- saved state and settings ---

def apply_settings(new):
    """Copy valid settings from `new`; anything invalid keeps its old value."""
    for key in DEFAULTS:
        if key not in new:
            continue
        try:
            if key in LIMITS:
                lo, hi = LIMITS[key]
                settings[key] = min(hi, max(lo, int(float(new[key]))))
            else:
                settings[key] = fmt_hm(hm(new[key]))
        except (ValueError, TypeError):
            pass


def save_settings():
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f)
    except OSError as e:
        print("Couldn't save lamp settings:", e)


def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"plan": plan, "manual": manual, "done": done, "off_at": off_at,
                       "restore": restore_to}, f)
    except OSError as e:
        print("Couldn't save lamp state:", e)


def load_lamp():
    global plan, manual, done, off_at, restore_to
    try:
        with open(SETTINGS_FILE) as f:
            apply_settings(json.load(f))
    except (OSError, ValueError):
        pass
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
        plan, manual, done, off_at = s.get("plan"), s.get("manual"), s.get("done"), s.get("off_at")
        restore_to = s.get("restore")
    except (OSError, ValueError, AttributeError):
        pass
    if off_at is not None and time.time() > off_at + LATE_GRACE:
        off_at = None  # long past (the Pico was off), so don't switch the lamp off out of the blue


# --- web page and commands ---

PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Sunrise lamp</title><style>
body{font:17px -apple-system,system-ui,sans-serif;max-width:30em;margin:0 auto;padding:1em;color:#222;background:#fdf8f2}
h1{font-size:1.4em}h2{font-size:1.05em;margin:1.6em 0 .4em}
.card{background:#fff;border-radius:12px;padding:.8em 1em;box-shadow:0 1px 3px #0002}
label{display:flex;justify-content:space-between;align-items:center;gap:1em;margin:.6em 0}
input{font-size:1em;width:6.5em}small{color:#666}
button,a.b{font:inherit;padding:.45em .9em;border-radius:8px;border:1px solid #d9a77a;background:#fff;
color:#222;text-decoration:none;display:inline-block;margin:.3em .3em 0 0}
button{background:#e8833a;border-color:#e8833a;color:#fff}
.ok{border-left:5px solid #4a4}.bad{border-left:5px solid #d33}
summary{margin-top:.6em;color:#666}ul{padding-left:1.2em;font-size:.85em;color:#444}
pre{font-size:.75em;white-space:pre-wrap;background:#fff;padding:.5em;border-radius:8px}
</style></head><body>
<h1>Sunrise lamp</h1>
<div class=card><b>%s</b><br><small>%s</small></div>
%s
<h2>Just for the next morning</h2>
<form action=/wake><label>Wake me at <input type=time name=at required></label><button>Set</button></form>
<a class=b href=/cancel>No wake-up</a><a class=b href=/normal>Back to normal</a>
<h2>Usual settings</h2>
<form action=/save>
<label>Weekday wake-up <input type=time name=weekday value=%s></label>
<label>Minutes before an early event <input type=number name=lead min=0 max=240 value=%d></label>
<label>Weekends: only for events before <input type=time name=weekend value=%s></label>
<label>Sunrise length (minutes) <input type=number name=ramp min=1 max=120 value=%d></label>
<label>Turn off after wake-up (minutes, 0 = never) <input type=number name=off_after min=0 max=720 value=%d></label>
<label>Brightness at wake-up (%%) <input type=number name=end_bright min=10 max=100 value=%d></label>
<label>Warmth at wake-up (K) <input type=number name=end_temp min=2200 max=6500 step=100 value=%d></label>
<small>Warmth: 2200 candlelight, 2700 warm white, 4000 daylight. The sunrise starts deep red.</small>
<button>Save</button></form>
<h2>Lamp</h2>
<a class=b href=/test>Test sunrise (1 min)</a><a class=b href=/off>Turn off</a>
</body></html>"""

REDIRECT = ("303 See Other", "text/plain", "")


def esc(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def printer_html(now):
    """The Printer card, recent events and any recent crash, for the page."""
    head, ok, lines = printer_summary(now)
    rows = "".join(["<br><small>%s</small>" % esc(line) for line in lines])
    events = "".join(["<li>%s %s %s: %s%s</li>" % (
        when(e[0], now), "&#10003;" if e[2] else "&#10007;", esc(e[1]), esc(e[3]),
        " (x%d)" % e[4] if e[4] > 1 else "") for e in reversed(status["log"])])
    crash = ""
    if status.get("crash") and now - status["crash"][0] < 7 * 86400:
        crash = "<p><small>Last crash, %s:</small></p><pre>%s</pre>" % (
            when(status["crash"][0], now), esc(status["crash"][1]))
    return ('<h2>Printer</h2><div class="card %s"><b>%s</b>%s</div>'
            '<details><summary>Recent events</summary><ul>%s</ul>%s</details>') % (
        "ok" if ok else "bad", esc(head), rows, events or "<li>Nothing yet</li>", crash)


def unquote(text):
    """Undo URL encoding, e.g. '08%3A30' -> '08:30'."""
    parts = text.replace("+", " ").split("%")
    out = parts[0]
    for p in parts[1:]:
        try:
            out += chr(int(p[:2], 16)) + p[2:]
        except ValueError:
            out += "%" + p
    return out


def parse_path(path):
    """'/save?ramp=30&weekday=08%3A30' -> ('/save', {'ramp': '30', 'weekday': '08:30'})"""
    parts = path.split("?", 1)
    query = {}
    if len(parts) > 1:
        for item in parts[1].split("&"):
            kv = item.split("=", 1)
            if kv[0]:
                query[kv[0]] = unquote(kv[1]) if len(kv) > 1 else ""
    return parts[0], query


def clock(utc):
    t = time.gmtime(utc + london_offset(utc))
    return "%02d:%02d" % (t[3], t[4])


def status_lines(now):
    """Two lines for the page and /status: the next wake-up, then the detail."""
    t, day = upcoming(now)
    mins, why = wake_for(day)
    head = "%s: %s" % (fmt_day(day), "no wake-up" if mins is None else "wake-up " + fmt_hm(mins))
    if test_end is not None:
        why = "Test sunrise running"
    elif t is not None and now >= t - settings["ramp"] * 60:
        why = "Sunrise running: %d%%" % sunrise_at(now, t, settings["ramp"] * 60, min_dim or 10)["dimming"]
    elif off_at is not None:
        why += "; lamp turns off at " + clock(off_at)
    return head, why


def cancel_next(now):
    """No wake-up on the next morning (the one /status shows)."""
    global manual
    day = upcoming(now)[1]
    manual = list(day) + [None]
    save_state()
    return day


def route(path, now):
    """Carry out one web request. Returns (status, content type, body)."""
    global plan, manual, done, off_at, test_end, sun_end, restore_to
    page, q = parse_path(path)

    if page == "/plan":  # from the nightly Shortcut: tomorrow's first timed event
        first = q.get("first", "").strip().lower()
        day = london_day(now, 1)
        plan = list(day) + [None if first in ("", "none", "-1") else hm(first)]
        save_state()
        return "200 OK", "text/plain", describe(day)

    if page == "/wake":
        if "at" in q:  # one-off: the next time the clock shows this
            mins = hm(q["at"])
            day = london_day(now)
            if london_to_utc(day, mins) <= now:
                day = london_day(now, 1)
            manual = list(day) + [mins]
        elif "in" in q:  # /wake?in=SECONDS (0 or less cancels the next wake-up)
            secs = int(float(q["in"]))
            if secs <= 0:
                return "200 OK", "text/plain", describe(cancel_next(now))
            t = now + secs
            lt = time.gmtime(t + london_offset(t))
            day = (lt[0], lt[1], lt[2])
            manual = list(day) + [lt[3] * 60 + lt[4]]
        else:
            raise ValueError("use /wake?at=HH:MM or /wake?in=SECONDS")
        if is_done(day):
            done = None
        save_state()
        return REDIRECT if "at" in q else ("200 OK", "text/plain", describe(day))

    if page == "/cancel":
        cancel_next(now)
        return REDIRECT

    if page == "/normal":
        manual = None
        save_state()
        return REDIRECT

    if page == "/save":
        apply_settings(q)
        save_settings()
        return REDIRECT

    if page == "/test":
        if test_end is None and off_at is None:  # not already in a test: remember the lamp as it is
            restore_to = restore_params(get_light())
        test_end = now + TEST_SECS
        off_at = None
        return REDIRECT

    if page == "/off":
        if test_end is not None:
            test_end = None
        else:
            t, day = upcoming(now)
            if t is not None and now >= t - settings["ramp"] * 60:
                done = list(day)  # stop this morning's sunrise as well
        sun_end = None
        off_at = None
        restore_to = None
        save_state()
        set_light(False)
        return REDIRECT

    if page == "/status":
        head, ok, lines = printer_summary(now)
        return "200 OK", "text/plain", "\n".join(status_lines(now) + ("", "Printer: " + head) + tuple(lines))

    if page == "/":
        s = settings
        return "200 OK", "text/html", PAGE % (status_lines(now) + (printer_html(now),
            s["weekday"], s["lead"], s["weekend"], s["ramp"], s["off_after"], s["end_bright"], s["end_temp"]))

    return "404 Not Found", "text/plain", "Not found"


def start_server():
    global server
    try:
        if server:
            server.close()
        # Listen on IPv6 as well as IPv4: phones and Macs often reach sunrise.local over IPv6,
        # and an IPv4-only server resets those connections.
        try:
            server = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("::", 80))
        except (AttributeError, OSError):
            if server:
                server.close()
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("0.0.0.0", 80))
        server.listen(2)
        server.setblocking(False)
        print("Web server running")
    except OSError as e:
        note("lamp", False, "web page couldn't start: %s" % e)
        server = None


def handle_requests(now):
    """Answer at most one waiting web request without holding up the main loop."""
    if server is None:
        start_server()
        return
    try:
        client, _ = server.accept()
    except OSError as e:
        if e.args and e.args[0] != 11:  # 11 = EAGAIN, nobody waiting
            print("Web accept failed:", e)
        return
    try:
        client.settimeout(2)
        request = client.recv(1024).decode()
        words = request.split(" ")
        path = words[1] if len(words) > 2 else "/"
        try:
            code, ctype, body = route(path, now)
        except ValueError as e:
            code, ctype, body = "400 Bad Request", "text/plain", "Bad value: %s" % e
        head = "HTTP/1.0 %s\r\nContent-Type: %s; charset=utf-8\r\nCache-Control: no-store\r\n" % (code, ctype)
        if code.startswith("303"):
            head += "Location: /\r\n"
        client.sendall((head + "Connection: close\r\n\r\n" + body).encode())
        print("Web:", path, "->", code, body if ctype == "text/plain" else "")
    except Exception as e:
        print("Web request failed:", e)
    finally:
        client.close()


def lamp_loop():
    """Everything the lamp needs on each pass of the main loop."""
    global last_wifi, last_tick
    now = time.time()
    if now - last_wifi > 60:  # the printer only reconnects in the morning, the lamp needs WiFi always
        last_wifi = now
        if not network.WLAN(network.STA_IF).isconnected():
            note("wifi", False, "dropped, reconnecting")
            connect_wifi()
    handle_requests(now)
    now = time.time()
    if now != last_tick:
        last_tick = now
        tick_lamp(now)


# ---------- main loop ----------

def main():
    # Keep retrying WiFi as well as the clock: a failed first join used to leave it stuck here
    while not (connect_wifi() and sync_clock()):
        time.sleep(30)
    note("start", True, "Pico started (%s)" % reset_reason())
    try:
        load_lamp()
        start_server()
    except Exception as e:
        note("lamp", False, "setup failed: %s" % e)

    last_check = 0
    last_trigger = 0
    last_ntp = time.time()
    while True:
        now = london_now()
        today = today_str(now)

        # Sunrise lamp. Wrapped so that a lamp problem can never stop the printer.
        try:
            lamp_loop()
        except Exception as e:
            note("lamp", False, "error: %s" % e)

        # Manual reprint
        if rp2.bootsel_button():
            note("button", True, "pressed: reprinting the latest brief")
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
            if text:
                note("download", True, "got today's brief" if fresh else
                     "only an older brief so far (%s)" % text[6:16].strip())

            # Not built yet: ask GitHub to build it (again every 20 minutes if needed)
            if not fresh and time.time() - last_trigger > 1200:
                if trigger_brief():
                    last_trigger = time.time()

            if due and text and (fresh or now[3] >= config.GIVE_UP_HOUR):
                do_print(text, today)
                write_last(today)
            elif due:
                note("download", True, "today's brief not ready yet, will retry in 5 min")

        # Resync the clock once a day so it doesn't drift
        if time.time() - last_ntp > 86400:
            if sync_clock():
                last_ntp = time.time()

        # After an hour of running smoothly, forget earlier crashes
        if status["crashes"] and time.ticks_diff(time.ticks_ms(), started_ms) > 3600000:
            status["crashes"] = 0
            save_status()

        time.sleep_ms(200)


def run():
    """Run main(). If it ever crashes, save the error and restart the Pico, waiting longer
    after each crash in a row so a fault that keeps happening can't hammer GitHub."""
    load_status()
    try:
        main()
    except KeyboardInterrupt:
        raise  # Ctrl-C from Thonny or mpremote
    except Exception as e:
        buf = io.StringIO()
        sys.print_exception(e, buf)
        status["crash"] = [time.time(), buf.getvalue()[-800:]]
        status["crashes"] = status.get("crashes", 0) + 1
        wait = min(1800, 30 * 2 ** (status["crashes"] - 1))
        note("crash", False, "%s: %s (restarting in %d s)" % (type(e).__name__, e, wait))
        print(buf.getvalue())
        time.sleep(wait)
        machine.reset()


run()
