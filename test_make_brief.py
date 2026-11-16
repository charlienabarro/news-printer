"""Tests for make_brief.py's filtering and memory (no network, no Gemini).

Run:  python3 test_make_brief.py
"""

import calendar
import unittest

import make_brief as mb


SAMPLE = """@DATE 2026-11-15
# UK
* Trident submarine finishes
* record patrol
HMS Vengeance concluded a seven-
month underwater deployment.

# WORLD
* Train collision in Egypt kills
* seven
At least seven people died.

* US asks about Ukraine Donbas
* withdrawal
Volodymyr Zelenskyy said the US
asked about the Donbas.
"""


class FillerFilter(unittest.TestCase):
    def test_drops_shopping_deals(self):
        for title, summary in [
            ("LG B6E OLED TV heavily discounted", "Best Buy cut it to an all-time low of $39.99"),
            ("Apple's AirPods Pro are $50 off right now", ""),
            ("The best early Black Friday deals on laptops", ""),
            ("Get 20% off a Dyson this weekend", ""),
            ("Save on Sonos: discounts on the Era 100", ""),
            ("The best robot vacuums to buy in 2026", ""),
        ]:
            self.assertTrue(mb.is_boring(title, summary), title)

    def test_keeps_real_news(self):
        for title in [
            "Andy Burnham says he will sign a new deal with the EU",
            "Metro Bank puts itself up for sale",
            "Poundland: the discount chain gets more time for a rescue",
            "Lords report warns of UK polycrisis threats",
            "Thames Water creditors offer GBP 75m tariff boost",
            "Trade deal talks between the UK and India resume",
        ]:
            self.assertFalse(mb.is_boring(title), title)


class AgeCheck(unittest.TestCase):
    def test_old_items_are_dropped(self):
        now = calendar.timegm((2026, 11, 16, 7, 40, 0))
        def entry(hours_ago):
            return {"published_parsed": __import__("time").gmtime(now - hours_ago * 3600)}
        self.assertTrue(mb.is_recent(entry(10), now))
        self.assertTrue(mb.is_recent(entry(36), now))
        self.assertFalse(mb.is_recent(entry(37), now))
        self.assertTrue(mb.is_recent({}, now))  # no date: keep, the history catches repeats


class Memory(unittest.TestCase):
    def test_reads_a_printed_brief_back(self):
        day, stories = mb.parse_brief(SAMPLE)
        self.assertEqual(day, "2026-11-15")
        self.assertEqual(stories, [
            {"section": "UK", "headline": "Trident submarine finishes record patrol",
             "text": "HMS Vengeance concluded a seven-month underwater deployment."},
            {"section": "World", "headline": "Train collision in Egypt kills seven",
             "text": "At least seven people died."},
            {"section": "World", "headline": "US asks about Ukraine Donbas withdrawal",
             "text": "Volodymyr Zelenskyy said the US asked about the Donbas."},
        ])

    def test_remembers_yesterday_not_today_and_forgets_old_days(self):
        history = {"2026-11-09": [{"x": 1}], "2026-11-12": [{"x": 2}]}
        history = mb.remember(history, SAMPLE, "2026-11-16")
        self.assertEqual(sorted(history), ["2026-11-12", "2026-11-15"])  # 9 Nov is > 5 days old
        # A brief dated today (a re-run) is not remembered
        same_day = mb.remember({}, SAMPLE, "2026-11-15")
        self.assertEqual(same_day, {})
        # A day already remembered keeps its first (printed) version
        kept = mb.remember({"2026-11-15": [{"first": True}]}, SAMPLE, "2026-11-16")
        self.assertEqual(kept["2026-11-15"], [{"first": True}])

    def test_prompt_lists_what_was_printed(self):
        history = mb.remember({}, SAMPLE, "2026-11-16")
        prompt = mb.build_prompt(["[World] Something new - details"], history)
        self.assertIn("- Sun 15 Nov, World: Train collision in Egypt kills seven. At least seven people died.", prompt)
        self.assertIn("Do not repeat those stories", prompt)
        self.assertIn("Never include shopping deals", prompt)
        self.assertIn("[World] Something new - details", prompt)
        self.assertIn("(nothing yet)", mb.build_prompt([], {}))


if __name__ == "__main__":
    unittest.main(verbosity=1)
