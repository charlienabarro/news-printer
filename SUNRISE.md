# Sunrise lamp

The Pico W that prints the morning brief also runs a wake-up light. A Philips WiZ bulb fades from
10% warm light (2200K) up to 100% daylight (4000K) over the 30 minutes before you wake up.
90 minutes after the wake-up it turns itself off.

## How the wake-up time is chosen

| Day | No early event | Event tomorrow |
|---|---|---|
| Mon–Fri | 08:30 | 1 hour before the first event, if that is earlier than 08:30 |
| Sat–Sun | no wake-up | 1 hour before the first event, only if it starts before 11:00 |

- Every night at 23:00 an iPhone Shortcut sends the start of tomorrow's first timed event
  (all-day events don't count). The Pico does the rest.
- If the phone doesn't send anything, weekdays still wake at 08:30.
- Events before 04:00 are ignored, so a 00:30 event can't switch the lamp on at 23:30.

## The settings page: http://sunrise.local

Open it in Safari on your phone (same Wi-Fi). Add it to your Home Screen for one-tap access:
Share button → **Add to Home Screen**.

- **Top card:** the next wake-up and why it was chosen.
- **Just for the next morning:** pick a time and tap **Set** for a one-off.
  **No wake-up** skips the next morning. **Back to normal** undoes either.
  One-offs survive the 23:00 Shortcut.
- **Usual settings:** weekday time, minutes before an event, weekend cut-off, sunrise length,
  auto-off. Tap **Save**. Changes apply straight away, including tomorrow's wake-up.
- **Printer:** today's brief at a glance. It shows the last build, download and print, plus
  the last problem in plain words (e.g. *"07:40, build: GitHub refused (HTTP 401) Bad
  credentials"*). A green edge means all is well, red means today's brief hasn't printed
  after 08:00. Tap **Recent events** for the last 25 things that happened. "Print: sent to
  the printer" means the Pico sent it; the printer can't confirm paper came out.
- **Lamp:** **Test sunrise (1 min)** and **Turn off**. A test holds full brightness for a
  minute, then puts the lamp back as it was before (or off, if it was off).

If the program ever crashes, the Pico saves the error and restarts itself. The error then appears
under **Recent events** as "Last crash". The wait before restarting doubles after each crash in
a row (30 s, 1 min, 2 min, … up to 30 min), so a fault that keeps happening can't flood GitHub
with builds.

If you touch the lamp during a sunrise (WiZ app, or switching it off), the sunrise stops for that
morning. The auto-off also leaves the lamp alone if you've changed it since.

If `sunrise.local` doesn't open, use the Pico's IP instead (currently `http://192.168.0.118`;
your router may change it).

Plain-text commands, handy for testing from a Mac:

```
curl http://sunrise.local/status
curl "http://sunrise.local/plan?first=0900"   # what the Shortcut sends (first=none if no event)
curl "http://sunrise.local/wake?in=3600"      # one-off in N seconds; in=-1 skips the next morning
```

## iPhone Shortcut (tap by tap)

**What it does:** at 23:00 it finds the next calendar event in the coming day (not all-day) and
sends its start time to the Pico, e.g. `first=0915`. If there's no event it sends `first=` with
nothing after it. The Pico works out the wake-up time from your settings, so the Shortcut never
needs changing.

It's 3 actions under a 23:00 trigger. Your phone may show the trigger as **At 23:00** at the top
of the shortcut (newer iOS) or under the **Automation** tab (older iOS).

**1. The trigger.** Make a new shortcut with an **At 23:00** trigger (older iOS: Automation tab
→ **+** → **Time of Day** → 23:00 → **Daily** → **Run Immediately** → **New Blank Automation**).
Tap the **›** next to 23:00 and make sure it repeats **Daily** and runs **without asking**:
choose **Run Immediately**, or turn off **Confirm/Ask Before Running**.

**2. Find Calendar Events.** Search bar at the bottom → type `find calendar` → tap it. Then:
- Make the filter read **Start Date is in the next 1 days**. Tap the condition to pick
  **is in the next**, the number to type **1**, and the unit to pick **days**.
- **Add Filter** → tap the new filter's first word → **Is All Day** → set it so all-day events
  are **left out** (switch off, or "Is Not All Day").
- **Sort by** → **Start Date**, **Order** → **Oldest First**, **Limit** on → **1**.
  (No Sort/Limit? Tap the action's **›** to show more options.)

**3. Get Contents of URL.** Search `get contents` → tap it. Tap the URL and type
`http://sunrise.local/plan?first=` (no spaces). With the cursor straight after the `=`, tap
**Calendar Events** in the bar above the keyboard to insert it. Then tap that new **Calendar
Events** bubble:
- choose **Start Date**;
- **Date Format** → **Custom**, Format String `HHmm` (capital HH, small mm);
  **Time Format** → **None** if it's shown.

**4. Show Notification.** Search `notification` → tap **Show Notification** → tap its text →
choose **Contents of URL**.

**5. Test it now.** Tap **▶** (bottom right) and allow everything it asks:
- **Calendar:** **Allow Full Access**;
- **Local Network:** **Allow**;
- **sunrise.local:** **Always Allow**.

You should get a notification like *"Mon 16 Nov: wake-up 08:30 (usual weekday time)"*.
To check it reads events: add a 07:30 event tomorrow and tap ▶ again. On a weekday it should say
*wake-up 06:30*. Then delete the event and tap ▶ once more.

Known limit: an event starting tonight between 23:00 and midnight hides tomorrow morning's
first event (rare).

### If something's wrong

| What you see | Fix |
|---|---|
| "Could not connect to the server" | Same Wi-Fi? Settings → Privacy & Security → Local Network → **Shortcuts** on. Or use `http://192.168.0.118/plan?first=` (that IP can change) |
| "Bad value: …" | The bubble in step 3 must be **Start Date** with Custom format `HHmm`; the URL must have no spaces |
| Notification shows the wrong event | Step 2: all-day events left out, sorted **Oldest First**, limit **1** |
| It never runs at night | Tap **›** next to 23:00: Daily, and running without asking |
| Wrong wake-up time | Change the rules on http://sunrise.local, not in the Shortcut |

## Putting the Pico back on its own power

The printer must run from the 5V supply. On USB from a laptop it prints slowly and faintly,
because USB can't supply enough current. **Never connect USB and the 5V supply at the same time.**

1. Unplug the USB cable from the Pico.
2. Plug in the 5V supply. The Pico boots by itself.
3. Wait about 15 seconds, then open http://sunrise.local on your phone. It should load.
4. Press the Pico's **BOOTSEL** button once. It reprints the latest brief (about 40 seconds).
   The print should be dark and steady. If it's still faint on the 5V supply, check the supply is
   rated 2A or more, then raise `HEAT_TIME` in `main.py`.

## Tests

Logic tests run on a Mac (not the Pico), against answers worked out by hand:

```
python3 pico/test_sunrise.py
```
