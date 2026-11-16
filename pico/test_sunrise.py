"""Mac-side tests for the sunrise lamp logic in main.py. Not copied to the Pico.

Run:  python3 pico/test_sunrise.py

main.py is MicroPython code, so the Pico-only modules are replaced with stand-ins,
the final main() call is skipped, and the bulb is a fake. Every expected answer
below was worked out by hand (UTC times; the UK is on GMT in November and BST in June).
"""

import calendar
import os
import sys
import tempfile
import time as real_time
import traceback
import types
import unittest
from pathlib import Path
from unittest import mock


def utc(y, m, d, hh, mm):
    return calendar.timegm((y, m, d, hh, mm, 0))


class FakeTime:
    """MicroPython-style time module whose clock the tests control."""
    now = 0

    def time(self):
        return self.now

    def gmtime(self, t):
        return tuple(real_time.gmtime(t))

    def mktime(self, tup):
        return calendar.timegm(tuple(tup[:6]))

    def sleep(self, s):
        self.slept = s

    def sleep_ms(self, ms):
        pass

    def ticks_ms(self):
        return int(self.now * 1000)

    def ticks_diff(self, a, b):
        return a - b


class FakeBulb:
    def __init__(self):
        self.state, self.dimming, self.temp = False, 100, 4000
        self.answers = True
        self.sets = []

    def set_light(self, on, dimming=100, temp=4000):
        if not self.answers:
            return False
        self.state = on
        if on:
            self.dimming, self.temp = dimming, temp
        self.sets.append((on, dimming, temp) if on else (False,))
        return True

    def get_light(self):
        if not self.answers:
            return None
        pilot = {"state": self.state, "dimming": self.dimming, "temp": self.temp}
        if getattr(self, "scene", 0):
            pilot["sceneId"] = self.scene
        return pilot

    def set_pilot(self, params):
        if not self.answers:
            return False
        self.state, self.dimming = params.get("state", True), params.get("dimming", self.dimming)
        self.scene = params.get("sceneId", 0)
        self.temp = params.get("temp", self.temp)
        self.sets.append(dict(params))
        return True


def load_main(clock):
    for name in ("network", "ntptime", "rp2", "urequests", "machine"):
        sys.modules[name] = mock.MagicMock()
    sys.modules["config"] = types.SimpleNamespace(
        BAUD=9600, UART_TX_PIN=0, UART_RX_PIN=1, WIFI_SSID="wifi", WIFI_PASSWORD="pw",
        BRIEF_URL="https://example.com/brief.txt", GITHUB_TOKEN="token", GITHUB_REPO="me/repo",
        TRIGGER_HOUR=7, TRIGGER_MINUTE=40, PRINT_HOUR=8, PRINT_MINUTE=0, GIVE_UP_HOUR=10)
    src = (Path(__file__).parent / "main.py").read_text().rstrip()
    assert src.endswith("\nrun()")
    ns = {"__name__": "main_under_test"}
    with mock.patch.dict(sys.modules, {"time": clock}):
        exec(compile(src[:-len("run()")], "main.py", "exec"), ns)
    # MicroPython's sys.print_exception, done with CPython's traceback module
    ns["sys"] = types.SimpleNamespace(
        print_exception=lambda e, f: traceback.print_exception(type(e), e, e.__traceback__, file=f))
    return ns


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code, self.text = status_code, text

    def close(self):
        pass


class LampTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeTime()
        self.m = m = load_main(self.clock)
        self.bulb = FakeBulb()
        m["set_light"] = self.bulb.set_light
        m["get_light"] = self.bulb.get_light
        m["set_pilot"] = self.bulb.set_pilot
        m["min_dim"] = 10                      # the bulb's lowest brightness (asked from the bulb on the Pico)
        tmp = tempfile.mkdtemp()
        for key, name in (("STATE_FILE", "state.json"), ("SETTINGS_FILE", "settings.json"),
                          ("STATUS_FILE", "status.json"), ("LAST_FILE", "last_printed.txt")):
            m[key] = os.path.join(tmp, name)

    def at(self, t):
        self.clock.now = t
        return t

    def get(self, path):
        return self.m["route"](path, self.clock.now)

    def next_wake(self):
        return self.m["upcoming"](self.clock.now)[0]

    def run_until(self, end):
        """Call tick_lamp once a second up to `end`, like the main loop does."""
        while self.clock.now < end:
            self.clock.now += 1
            self.m["tick_lamp"](self.clock.now)

    # --- UK time ---

    def test_uk_clock_time_to_utc(self):
        to_utc = self.m["london_to_utc"]
        self.assertEqual(to_utc((2026, 11, 16), 8 * 60 + 30), utc(2026, 11, 16, 8, 30))  # GMT
        self.assertEqual(to_utc((2026, 6, 25), 8 * 60 + 30), utc(2026, 6, 25, 7, 30))    # BST
        self.assertEqual(to_utc((2026, 3, 29), 8 * 60 + 30), utc(2026, 3, 29, 7, 30))    # BST starts 01:00 UTC
        self.assertEqual(to_utc((2026, 10, 25), 8 * 60 + 30), utc(2026, 10, 25, 8, 30))  # BST ended 01:00 UTC

    def test_uk_date_after_midnight_in_summer(self):
        # 23:30 UTC on 24 June is 00:30 BST on 25 June
        self.assertEqual(self.m["london_day"](utc(2026, 6, 24, 23, 30)), (2026, 6, 25))
        self.assertEqual(self.m["london_day"](utc(2026, 6, 24, 23, 30), 1), (2026, 6, 26))

    def test_time_parsing(self):
        hm = self.m["hm"]
        self.assertEqual(hm("08:30"), 510)
        self.assertEqual(hm("0915"), 555)
        self.assertEqual(hm("915"), 555)
        for bad in ("", "none", "25:00", "08:61"):
            with self.assertRaises(ValueError):
                hm(bad)

    # --- the wake-up rules (default settings) ---

    def wake(self, day, first):
        mins = self.m["rule_wake"](day, None if first is None else self.m["hm"](first))[0]
        return None if mins is None else self.m["fmt_hm"](mins)

    def test_weekday_rules(self):
        mon = (2026, 11, 16)
        self.assertEqual(self.wake(mon, None), "08:30")     # no event
        self.assertEqual(self.wake(mon, "09:00"), "08:00")  # event before 09:30: 1 h before
        self.assertEqual(self.wake(mon, "07:15"), "06:15")
        self.assertEqual(self.wake(mon, "09:30"), "08:30")  # 1 h before is 08:30 anyway
        self.assertEqual(self.wake(mon, "10:00"), "08:30")  # later event: usual time
        self.assertEqual(self.wake(mon, "03:00"), "08:30")  # night-time event ignored

    def test_weekend_rules(self):
        sat, sun = (2026, 11, 21), (2026, 11, 22)
        self.assertIsNone(self.wake(sat, None))             # no event: no wake-up
        self.assertEqual(self.wake(sat, "10:30"), "09:30")
        self.assertEqual(self.wake(sun, "10:59"), "09:59")
        self.assertIsNone(self.wake(sat, "11:00"))          # not before 11:00
        self.assertIsNone(self.wake(sun, "19:00"))

    def test_changed_settings_apply(self):
        self.m["apply_settings"]({"weekday": "07:45", "lead": "45"})
        mon = (2026, 11, 16)
        self.assertEqual(self.wake(mon, None), "07:45")
        self.assertEqual(self.wake(mon, "08:15"), "07:30")
        self.assertEqual(self.wake(mon, "08:30"), "07:45")

    # --- ramp ---

    def test_sunrise_goes_red_orange_amber_then_warm_white(self):
        p = self.m["sunrise_params"]
        def px(r, g, b, c, w, dim):
            return {"r": r, "g": g, "b": b, "c": c, "w": w, "dimming": dim}
        self.assertEqual(p(0), px(255, 20, 0, 0, 0, 10))       # deep red, dimmest
        self.assertEqual(p(0.175), px(255, 55, 0, 0, 0, 12))   # 20+70/2, 10+5/2
        self.assertEqual(p(0.35), px(255, 90, 0, 0, 0, 15))    # orange
        self.assertEqual(p(0.6), px(255, 150, 30, 0, 0, 30))   # amber: no white yet, no jump
        # halfway through the last part: white only a quarter in (0.5 squared), 30+40/2 = 50%
        self.assertEqual(p(0.8), px(191, 112, 22, 0, 63, 50))  # 255*0.75, 150*0.75, 30*0.75, 255*0.25
        self.assertEqual(p(1), px(0, 0, 0, 0, 255, 70))        # warm-white LED only, 70%
        self.assertEqual(p(-1), p(0))
        self.assertEqual(p(2), p(1))
        self.assertEqual(p(0, low=1)["dimming"], 1)   # bulbs that go down to 1% start at 1%

    def test_brightness_only_rises_one_percent_at_a_time(self):
        p = self.m["sunrise_params"]
        dims = [p(i / 1800, low=1)["dimming"] for i in range(1801)]
        steps = [b - a for a, b in zip(dims, dims[1:])]
        self.assertEqual((min(steps), max(steps)), (0, 1))
        self.assertEqual((dims[0], dims[-1]), (1, 70))

    def test_wake_up_brightness_and_warmth_are_settings(self):
        self.m["apply_settings"]({"end_bright": "40", "end_temp": "3000"})
        # 3000K: (3000-2700)/3800 = 0.079 of the way to cool white: c = 20, w = 234
        self.assertEqual(self.m["sunrise_params"](1), {"r": 0, "g": 0, "b": 0, "c": 20, "w": 234, "dimming": 40})
        self.m["apply_settings"]({"end_bright": "5", "end_temp": "1000"})       # clamped to 10% / 2200K
        self.assertEqual(self.m["sunrise_params"](1), {"r": 160, "g": 50, "b": 0, "c": 0, "w": 255, "dimming": 10})

    def test_no_jump_where_the_white_comes_in(self):
        p = self.m["sunrise_params"]
        before, after = p(0.5999), p(0.6001)
        for key in ("r", "g", "b", "c", "w"):
            self.assertLessEqual(abs(after[key] - before[key]), 1, key)
        self.assertLessEqual(abs(after["dimming"] - before["dimming"]), 1)

    # --- web commands ---

    def test_nightly_plan(self):
        self.at(utc(2026, 11, 15, 23, 0))  # Sunday 23:00
        reply = self.get("/plan?first=0900")
        self.assertEqual(reply[2], "Mon 16 Nov: wake-up 08:00 (60 min before 09:00 event)")
        self.assertEqual(self.next_wake(), utc(2026, 11, 16, 8, 0))
        reply = self.get("/plan?first=none")
        self.assertEqual(reply[2], "Mon 16 Nov: wake-up 08:30 (usual weekday time)")

    def test_weekend_plan(self):
        self.at(utc(2026, 11, 20, 23, 0))  # Friday 23:00
        self.assertIn("no wake-up", self.get("/plan?first=none")[2])
        self.assertIsNone(self.next_wake())
        self.assertEqual(self.get("/plan?first=1030")[2], "Sat 21 Nov: wake-up 09:30 (60 min before 10:30 event)")

    def test_no_plan_still_wakes_on_weekdays(self):
        self.at(utc(2026, 11, 16, 6, 0))  # Monday 06:00, the phone never sent anything
        self.assertEqual(self.next_wake(), utc(2026, 11, 16, 8, 30))

    def test_missed_wake_up_is_not_replayed(self):
        self.at(utc(2026, 11, 16, 14, 0))  # Pico boots at 14:00 on Monday
        self.assertEqual(self.next_wake(), utc(2026, 11, 17, 8, 30))
        self.run_until(self.clock.now + 5)
        self.assertEqual(self.bulb.sets, [])

    def test_one_off_survives_the_nightly_plan(self):
        self.at(utc(2026, 11, 15, 21, 0))  # Sunday 21:00
        self.assertEqual(self.get("/wake?at=07%3A00")[0], "303 See Other")
        self.at(utc(2026, 11, 15, 23, 0))
        self.assertIn("07:00 (one-off)", self.get("/plan?first=none")[2])
        self.assertEqual(self.next_wake(), utc(2026, 11, 16, 7, 0))
        self.at(utc(2026, 11, 16, 23, 0))  # Monday night: back to the rules for Tuesday
        self.get("/plan?first=none")
        self.assertEqual(self.next_wake(), utc(2026, 11, 17, 8, 30))

    def test_one_off_time_already_passed_today_means_tomorrow(self):
        self.at(utc(2026, 11, 16, 9, 0))  # Monday 09:00
        self.get("/wake?at=07:00")
        self.assertEqual(self.next_wake(), utc(2026, 11, 17, 7, 0))

    def test_cancel_and_back_to_normal(self):
        self.at(utc(2026, 11, 15, 21, 0))
        self.get("/cancel")
        self.at(utc(2026, 11, 15, 23, 0))
        self.assertIn("no wake-up (cancelled", self.get("/plan?first=0900")[2])
        self.assertIsNone(self.next_wake())
        self.get("/normal")
        self.assertEqual(self.next_wake(), utc(2026, 11, 16, 8, 0))

    def test_wake_in_seconds_accepts_decimals_and_minus_one(self):
        self.at(utc(2026, 11, 15, 23, 0))
        reply = self.get("/wake?in=34200.0")  # 9.5 hours
        self.assertEqual(reply[2], "Mon 16 Nov: wake-up 08:30 (one-off)")
        reply = self.get("/wake?in=-1")
        self.assertEqual(reply[2], "Mon 16 Nov: no wake-up (cancelled for this morning)")

    def test_bad_values_are_rejected(self):
        self.at(utc(2026, 11, 15, 23, 0))
        with self.assertRaises(ValueError):
            self.get("/plan?first=banana")
        with self.assertRaises(ValueError):
            self.get("/wake")

    def test_settings_page_saves_and_reloads(self):
        self.get("/save?weekday=07%3A45&lead=abc&ramp=500&off_after=0&weekend=10%3A00")
        s = self.m["settings"]
        self.assertEqual((s["weekday"], s["lead"], s["ramp"], s["off_after"], s["weekend"]),
                         ("07:45", 60, 120, 0, "10:00"))  # bad lead kept, ramp capped at 120
        self.m["settings"].update(self.m["DEFAULTS"])
        self.m["load_lamp"]()
        self.assertEqual(self.m["settings"]["weekday"], "07:45")

    def test_page_shows_next_wake_up(self):
        self.at(utc(2026, 11, 15, 23, 0))
        status, ctype, body = self.get("/")
        self.assertEqual((status, ctype), ("200 OK", "text/html"))
        self.assertIn("Mon 16 Nov: wake-up 08:30", body)
        self.assertIn("name=weekday value=08:30", body)
        self.assertEqual(self.get("/favicon.ico")[0], "404 Not Found")

    # --- the sunrise itself, second by second ---

    def test_full_sunrise_then_auto_off(self):
        self.at(utc(2026, 11, 15, 23, 0))
        self.get("/plan?first=none")                     # wake Monday 08:30
        self.at(utc(2026, 11, 16, 7, 59))
        self.run_until(utc(2026, 11, 16, 7, 59) + 59)
        self.assertEqual(self.bulb.sets, [])             # nothing before 08:00
        self.run_until(utc(2026, 11, 16, 8, 0))
        self.assertEqual(self.bulb.sets[-1], {"state": True, "r": 255, "g": 20, "b": 0, "c": 0, "w": 0, "dimming": 10})
        self.run_until(utc(2026, 11, 16, 8, 18))         # 60% of the way: amber, white about to come in
        self.assertEqual(self.bulb.sets[-1], {"state": True, "r": 255, "g": 150, "b": 30, "c": 0, "w": 0, "dimming": 30})
        self.run_until(utc(2026, 11, 16, 8, 30))
        self.assertEqual(self.bulb.sets[-1], {"state": True, "r": 0, "g": 0, "b": 0, "c": 0, "w": 255, "dimming": 70})
        dims = [s["dimming"] for s in self.bulb.sets]
        self.assertEqual(max(b - a for a, b in zip(dims, dims[1:])), 1)   # never more than 1% a step
        self.assertEqual(self.m["done"], [2026, 11, 16])
        self.assertEqual(self.m["off_at"], utc(2026, 11, 16, 10, 0))  # 08:30 + 90 min
        self.run_until(utc(2026, 11, 16, 10, 0))
        self.assertEqual(self.bulb.sets[-1], (False,))
        self.assertIsNone(self.m["off_at"])
        self.assertEqual(self.next_wake(), utc(2026, 11, 17, 8, 30))

    def test_reboot_mid_sunrise_picks_up_where_it_should_be(self):
        self.at(utc(2026, 11, 16, 8, 20))  # boots at 08:20, 10 min before wake-up
        self.run_until(self.clock.now + 1)
        # 1 - 599/1800 = 0.6672 of the way: t = (0.6672-0.6)/0.4 = 0.168, white in by t*t = 0.028
        self.assertEqual(self.bulb.sets[0], {"state": True, "r": 247, "g": 145, "b": 29, "c": 0, "w": 7, "dimming": 36})

    def test_touching_the_lamp_stops_the_sunrise(self):
        self.at(utc(2026, 11, 16, 8, 0))
        self.run_until(utc(2026, 11, 16, 8, 5))
        self.bulb.state = False                      # turned off in the WiZ app
        count = len(self.bulb.sets)
        self.run_until(utc(2026, 11, 16, 8, 40))
        self.assertEqual(len(self.bulb.sets), count)  # never turned back on
        self.assertEqual(self.m["done"], [2026, 11, 16])
        self.assertIsNone(self.m["off_at"])

    def test_no_auto_off_if_lamp_was_changed(self):
        self.at(utc(2026, 11, 16, 8, 0))
        self.run_until(utc(2026, 11, 16, 8, 30))
        self.bulb.dimming = 40                       # dimmed by hand after waking
        self.run_until(utc(2026, 11, 16, 10, 1))
        self.assertTrue(self.bulb.state)
        self.assertIsNone(self.m["off_at"])

    def test_bulb_not_answering_is_retried(self):
        self.at(utc(2026, 11, 16, 8, 0))
        self.bulb.answers = False
        self.run_until(utc(2026, 11, 16, 8, 1))
        self.assertEqual(self.bulb.sets, [])
        self.bulb.answers = True
        self.run_until(utc(2026, 11, 16, 8, 1) + 11)  # next try within 10 s
        self.assertTrue(self.bulb.sets)

    def test_test_sunrise_and_off_button(self):
        self.at(utc(2026, 11, 15, 19, 0))
        self.get("/test")
        self.run_until(self.clock.now + 60)
        self.assertEqual(self.bulb.sets[-1], {"state": True, "r": 0, "g": 0, "b": 0, "c": 0, "w": 255, "dimming": 70})
        self.run_until(self.clock.now + 61)           # test turns itself off a minute later
        self.assertEqual(self.bulb.sets[-1], (False,))
        self.get("/test")
        self.run_until(self.clock.now + 10)
        self.get("/off")
        self.run_until(self.clock.now + 60)
        self.assertEqual(self.bulb.sets[-1], (False,))

    def test_test_sunrise_puts_a_lamp_that_was_on_back_as_it_was(self):
        self.at(utc(2026, 11, 15, 19, 37))
        self.bulb.state, self.bulb.dimming, self.bulb.scene = True, 100, 12   # Daylight scene
        self.get("/test")
        self.run_until(self.clock.now + 60)
        self.assertEqual(self.bulb.sets[-1], {"state": True, "r": 0, "g": 0, "b": 0, "c": 0, "w": 255, "dimming": 70})  # end of test
        self.run_until(self.clock.now + 61)
        self.assertEqual(self.bulb.sets[-1], {"state": True, "dimming": 100, "sceneId": 12})
        self.assertEqual(self.m["last_event"]("lamp")[3], "put back as it was before the test")
        self.assertIsNone(self.m["restore_to"])

    def test_test_sunrise_restores_warm_white_too(self):
        self.at(utc(2026, 11, 15, 19, 37))
        self.bulb.state, self.bulb.dimming, self.bulb.temp = True, 40, 2700
        self.get("/test")
        self.run_until(self.clock.now + 121)
        self.assertEqual(self.bulb.sets[-1], {"state": True, "dimming": 40, "temp": 2700})

    def test_off_button_stops_morning_sunrise(self):
        self.at(utc(2026, 11, 16, 8, 0))
        self.run_until(utc(2026, 11, 16, 8, 10))
        self.get("/off")
        self.run_until(utc(2026, 11, 16, 8, 40))
        self.assertEqual(self.bulb.sets[-1], (False,))
        self.assertEqual(self.m["done"], [2026, 11, 16])

    # --- status log and printer messages ---

    def test_log_merges_repeats_keeps_the_newest_and_survives_restart(self):
        note = self.m["note"]
        self.at(utc(2026, 11, 16, 7, 40))
        note("wifi", False, "couldn't connect")
        self.at(utc(2026, 11, 16, 7, 41))
        note("wifi", False, "couldn't connect")
        log = self.m["status"]["log"]
        self.assertEqual(len(log), 1)
        self.assertEqual((log[0][0], log[0][4]), (utc(2026, 11, 16, 7, 41), 2))  # newest time, x2
        for i in range(30):
            note("lamp", True, "event %d" % i)
        self.assertEqual(len(log), 25)
        self.assertEqual(log[-1][3], "event 29")
        saved = [e[:] for e in log]
        self.m["status"]["log"] = []
        self.m["load_status"]()
        self.assertEqual(self.m["status"]["log"][0][3], saved[0][3])

    def test_printer_summary_through_the_morning(self):
        summary = self.m["printer_summary"]
        self.at(utc(2026, 11, 16, 6, 0))   # Monday, GMT, usual 08:30 wake-up
        self.assertEqual(summary(self.clock.now)[:2], ("Next brief: built at 08:10, printed at 08:30", True))
        self.at(utc(2026, 11, 16, 8, 15))
        self.assertEqual(summary(self.clock.now)[:2], ("Building today's brief, prints at 08:30", True))
        self.at(utc(2026, 11, 16, 7, 40))
        self.m["note"]("build", False, "GitHub refused (HTTP 401) Bad credentials")
        self.at(utc(2026, 11, 16, 9, 0))
        head, ok, lines = summary(self.clock.now)
        self.assertEqual((head, ok), ("Today's brief hasn't printed yet", False))
        self.assertIn("Last problem: 07:40, build: GitHub refused (HTTP 401) Bad credentials", lines)
        self.at(utc(2026, 11, 16, 9, 5))
        self.m["note"]("print", True, "sent to the printer")
        self.m["write_last"]("2026-11-16")
        head, ok, lines = summary(self.clock.now)
        self.assertEqual((head, ok), ("Today's brief printed at 09:05", True))
        self.assertIn("Print: 09:05, sent to the printer", lines)

    def test_news_prints_at_the_wake_up_time(self):
        pt = self.m["print_time"]
        self.at(utc(2026, 11, 15, 23, 0))
        self.get("/plan?first=0900")                    # Monday: event at 09:00
        self.at(utc(2026, 11, 16, 6, 0))
        self.assertEqual(self.m["fmt_hm"](pt()), "08:00")
        self.at(utc(2026, 11, 20, 23, 0))
        self.get("/plan?first=none")                    # Saturday: no event, no wake-up
        self.at(utc(2026, 11, 21, 7, 0))
        self.assertEqual(self.m["fmt_hm"](pt()), "10:00")   # the "no wake-up" print time
        self.get("/save?print_fallback=09%3A15")
        self.assertEqual(self.m["fmt_hm"](pt()), "09:15")
        self.at(utc(2026, 6, 25, 5, 0))                 # Thursday in summer (BST), no plan
        self.assertEqual(self.m["fmt_hm"](pt()), "08:30")   # UK clock time, not UTC

    def test_build_and_print_schedule(self):
        sched = self.m["brief_schedule"]
        self.at(utc(2026, 11, 16, 6, 0))                # Monday, wake-up 08:30
        self.assertEqual(sched((2026, 11, 16, 8, 9)), (False, False, False))
        self.assertEqual(sched((2026, 11, 16, 8, 10)), (True, False, False))   # build 20 min before
        self.assertEqual(sched((2026, 11, 16, 8, 30)), (True, True, False))    # print at wake-up
        self.assertEqual(sched((2026, 11, 16, 10, 30)), (True, True, True))    # 2 h later: print old one

    def test_cancelled_morning_prints_at_the_no_wake_up_time(self):
        self.at(utc(2026, 11, 15, 21, 0))
        self.get("/cancel")                             # no wake-up on Monday
        self.at(utc(2026, 11, 16, 6, 0))
        self.assertEqual(self.m["fmt_hm"](self.m["print_time"]()), "10:00")

    def test_github_refusal_is_explained(self):
        self.at(utc(2026, 11, 16, 7, 40))
        urequests = self.m["urequests"]
        urequests.post.return_value = FakeResponse(401, '{"message": "Bad credentials"}')
        self.assertFalse(self.m["trigger_brief"]())
        self.assertEqual(self.m["last_event"]("build")[3], "GitHub refused (HTTP 401) Bad credentials")
        urequests.post.side_effect = OSError(-2)
        self.m["trigger_brief"]()
        self.assertEqual(self.m["last_event"]("build")[3], "couldn't reach GitHub: -2")
        urequests.post.side_effect = None
        urequests.post.return_value = FakeResponse(204)
        self.assertTrue(self.m["trigger_brief"]())
        self.assertEqual(self.m["last_event"]("build")[2:4], [True, "started on GitHub"])

    def test_download_failure_is_explained(self):
        self.m["urequests"].get.return_value = FakeResponse(404, "Not Found")
        self.assertIsNone(self.m["fetch_brief"]())
        self.assertEqual(self.m["last_event"]("download")[3], "GitHub answered HTTP 404")

    def test_page_and_status_show_the_printer(self):
        self.at(utc(2026, 11, 16, 9, 0))
        self.m["note"]("download", False, "failed: <odd> & error")
        body = self.get("/")[2]
        self.assertIn('<h2>Printer</h2><div class="card bad"><b>Today\'s brief hasn\'t printed yet</b>', body)
        self.assertIn("failed: &lt;odd&gt; &amp; error", body)
        self.assertNotIn("<odd>", body)
        text = self.get("/status")[2]
        self.assertIn("Printer: Today's brief hasn't printed yet", text)

    def test_crash_is_saved_and_the_pico_restarts_with_growing_waits(self):
        self.at(utc(2026, 11, 16, 7, 41))
        def broken_main():
            raise RuntimeError("boom")
        self.m["main"] = broken_main
        machine = self.m["machine"]
        self.m["run"]()
        self.assertTrue(machine.reset.called)
        self.assertEqual(self.clock.slept, 30)
        crash = self.m["status"]["crash"]
        self.assertIn("RuntimeError: boom", crash[1])
        self.assertEqual(self.m["last_event"]("crash")[3], "RuntimeError: boom (restarting in 30 s)")
        self.m["run"]()                    # crashes again after the restart
        self.assertEqual(self.clock.slept, 60)
        self.assertIn("Last crash, 07:41:", self.get("/")[2])

    def test_ctrl_c_still_stops_it(self):
        def interrupted():
            raise KeyboardInterrupt
        self.m["main"] = interrupted
        with self.assertRaises(KeyboardInterrupt):
            self.m["run"]()

    def test_url_decoding(self):
        self.assertEqual(self.m["parse_path"]("/save?weekday=08%3A30&lead=60&x"),
                         ("/save", {"weekday": "08:30", "lead": "60", "x": ""}))


if __name__ == "__main__":
    unittest.main(verbosity=1)
