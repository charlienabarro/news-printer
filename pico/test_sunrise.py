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
        pass

    def sleep_ms(self, ms):
        pass


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
        return {"state": self.state, "dimming": self.dimming, "temp": self.temp}


def load_main():
    for name in ("network", "ntptime", "rp2", "urequests", "machine"):
        sys.modules[name] = mock.MagicMock()
    sys.modules["config"] = types.SimpleNamespace(BAUD=9600, UART_TX_PIN=0, UART_RX_PIN=1)
    src = (Path(__file__).parent / "main.py").read_text().rstrip()
    assert src.endswith("main()")
    ns = {"__name__": "main_under_test"}
    exec(compile(src[:-len("main()")], "main.py", "exec"), ns)
    return ns


class LampTest(unittest.TestCase):
    def setUp(self):
        self.m = m = load_main()
        self.clock = FakeTime()
        m["time"] = self.clock
        self.bulb = FakeBulb()
        m["set_light"] = self.bulb.set_light
        m["get_light"] = self.bulb.get_light
        tmp = tempfile.mkdtemp()
        m["STATE_FILE"] = os.path.join(tmp, "state.json")
        m["SETTINGS_FILE"] = os.path.join(tmp, "settings.json")

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

    def test_ramp_levels(self):
        level = self.m["ramp_level"]
        end = utc(2026, 11, 16, 8, 30)
        self.assertEqual(level(end - 1800, end, 1800), (10, 2200))   # start
        self.assertEqual(level(end - 900, end, 1800), (55, 3100))    # halfway: 10+45, 2200+900
        self.assertEqual(level(end, end, 1800), (100, 4000))         # end
        self.assertEqual(level(end - 3600, end, 1800), (10, 2200))   # before start
        self.assertEqual(level(end + 60, end, 1800), (100, 4000))    # after end

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
        self.assertEqual(self.bulb.sets[-1], (True, 10, 2200))
        self.run_until(utc(2026, 11, 16, 8, 15))
        self.assertEqual(self.bulb.sets[-1], (True, 55, 3100))
        self.run_until(utc(2026, 11, 16, 8, 30))
        self.assertEqual(self.bulb.sets[-1], (True, 100, 4000))
        self.assertEqual(len(self.bulb.sets), 91)        # every level from 10% to 100%
        self.assertEqual(self.m["done"], [2026, 11, 16])
        self.assertEqual(self.m["off_at"], utc(2026, 11, 16, 10, 0))  # 08:30 + 90 min
        self.run_until(utc(2026, 11, 16, 10, 0))
        self.assertEqual(self.bulb.sets[-1], (False,))
        self.assertIsNone(self.m["off_at"])
        self.assertEqual(self.next_wake(), utc(2026, 11, 17, 8, 30))

    def test_reboot_mid_sunrise_picks_up_where_it_should_be(self):
        self.at(utc(2026, 11, 16, 8, 20))  # boots at 08:20, 10 min before wake-up
        self.run_until(self.clock.now + 1)
        self.assertEqual(self.bulb.sets[0][1], 70)   # 10 + 90 * 20/30

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
        self.assertEqual(self.bulb.sets[-1], (True, 100, 4000))
        self.run_until(self.clock.now + 61)           # test turns itself off a minute later
        self.assertEqual(self.bulb.sets[-1], (False,))
        self.get("/test")
        self.run_until(self.clock.now + 10)
        self.get("/off")
        self.run_until(self.clock.now + 60)
        self.assertEqual(self.bulb.sets[-1], (False,))

    def test_off_button_stops_morning_sunrise(self):
        self.at(utc(2026, 11, 16, 8, 0))
        self.run_until(utc(2026, 11, 16, 8, 10))
        self.get("/off")
        self.run_until(utc(2026, 11, 16, 8, 40))
        self.assertEqual(self.bulb.sets[-1], (False,))
        self.assertEqual(self.m["done"], [2026, 11, 16])

    def test_url_decoding(self):
        self.assertEqual(self.m["parse_path"]("/save?weekday=08%3A30&lead=60&x"),
                         ("/save", {"weekday": "08:30", "lead": "60", "x": ""}))


if __name__ == "__main__":
    unittest.main(verbosity=1)
