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

**What it does:** every night at 23:00 it finds tomorrow's first calendar event (not all-day,
starting after 04:00) and sends its start time to the Pico, e.g. `first=0915`, or `first=none`
if there isn't one. The Pico then picks the wake-up time from your settings.

Checked against Apple's Shortcuts guide for iOS 26 (Nov 2026). Button names can shift slightly
between iOS versions; where they differ, the alternatives are given. Takes about 10 minutes.

**Before you start:** your iPhone must be on the same Wi-Fi as the Pico. Check that
http://sunrise.local opens in Safari.

### Part 1: create the 23:00 trigger

1. Open the **Shortcuts** app and tap the **Automation** tab at the bottom.
2. Tap **+** (top right). If you've never made an automation, tap **New Automation** in the
   middle of the screen instead. If you see **Create Personal Automation**, tap it.
3. Scroll the list of triggers and tap **Time of Day**.
4. Make sure **Time of Day** is selected (not Sunrise/Sunset), then tap the time and set
   **23:00**.
5. Under **Repeat**, choose **Daily**.
6. Make it run without asking:
   - iOS 17–26: tap **Run Immediately**, and turn **Notify When Run** off if it's shown.
   - If you see a **Confirm Before Run** or **Ask Before Running** switch instead, turn it
     **off** (tap **Don't Ask** if asked).
7. Tap **Next**, then **New Blank Automation**.

You're now in the action editor. To add each action below: tap **Add Action** (or the
**Search Actions** bar at the bottom), type the action's name, and tap it. New actions go to the
bottom of the list. If one lands in the wrong place, press and hold it and drag it.

### Part 2: work out "tomorrow 04:00 to midnight"

8. **Adjust Date**
   - Tap the blue word **Add** and choose **Get Start of Day**.
   - Tap the **Date** slot and choose **Current Date**.
   - It should read *Get Start of Day of Current Date* (today, 00:00).
9. **Adjust Date** (a second one)
   - Leave it on **Add**. Tap **0** and type **28**. Tap the unit and choose **hours**.
   - The date slot should already say **Adjusted Date**. If not, tap it and choose
     **Adjusted Date**.
   - It reads *Add 28 hours to Adjusted Date* (tomorrow, 04:00).
10. **Set Variable**
    - Tap **Variable Name** and type `From`.
    - Input should already be **Adjusted Date**.
11. **Adjust Date** (a third one)
    - **Add**, **20**, **hours**.
    - Tap the date slot and choose the variable **From**.
    - It reads *Add 20 hours to From* (tomorrow, midnight).
12. **Set Variable**: name it `To`. Input: **Adjusted Date**.

### Part 3: find tomorrow's first event

13. **Find Calendar Events**
    - Tap the existing filter's condition (it says something like **is in the next**) and
      choose **is between**.
    - Tap the first date, choose **Select Variable**, then tap **From**. Tap the second date and
      choose **To**.
    - Tap **Add Filter**. Tap the new filter's first word and choose **Is All Day**. Set it so
      all-day events are **excluded**: it may read **Is Not All Day**, or show a switch you turn
      off.
    - Tap **Sort by** and choose **Start Date**. Set **Order** to **Oldest First**.
    - Turn **Limit** on and set it to **1** (*Get 1 Calendar Event*).
    - Optional: **Add Filter** → **Calendar** → your own calendar(s), so shared or subscribed
      calendars can't wake you early.

### Part 4: turn the event into a time

14. **If**
    - Shortcuts adds three pieces: **If**, **Otherwise** and **End If**.
    - Tap **Input** (or **Condition**) and choose **Calendar Events**. Set the condition to
      **has any value**.
15. **Get Details of Calendar Events**: drag it **between If and Otherwise**.
    - Tap **Detail** and choose **Start Date**. The input should be **Calendar Events**.
16. **Format Date**: drag it **under step 15**, still above Otherwise.
    - The input should be **Start Date**.
    - Tap the date format (or the **›** arrow / **Show More**) and choose **Custom**.
    - In **Format String**, type `HHmm` (capital HH, lowercase mm). That gives e.g. `0915`.
17. **Text**: drag it **between Otherwise and End If**, then type `none`.

Check the If block now reads, top to bottom:
*If Calendar Events has any value → Get Start Date → Format Date → Otherwise → Text "none"
→ End If*.

### Part 5: send it to the Pico

18. **Get Contents of URL**: it must sit **below End If**.
    - Tap **URL** and type: `http://sunrise.local/plan?first=`
    - Straight after the `=`, insert the variable **If Result**: tap the variables bar above the
      keyboard, or **Select Variable** → **If Result**.
    - It reads `http://sunrise.local/plan?first=[If Result]`, all on one line, with no spaces.
19. **Show Notification**: tap its text and choose the variable **Contents of URL**.
    - Each night you'll get e.g. *"Mon 16 Nov: wake-up 08:00 (60 min before 09:00 event)"*.
20. Tap **Done** (top right). On some versions, tap **Next**, then **Done**.

### Part 6: run it once by hand (important)

An automation that runs at 23:00 can't answer permission questions, so answer them now.

21. In the **Automation** tab, tap the new automation, then the **▶** play button (bottom
    right).
22. Allow every prompt:
    - **Calendar:** choose **Allow Full Access**. "Add events only" isn't enough.
    - **Local Network:** **Allow**.
    - **"Allow Shortcuts to connect to sunrise.local?"** (or similar): **Always Allow**.
23. A notification should appear with tomorrow's wake-up. Open http://sunrise.local: the top card
    should show the same, and no longer say "no calendar info yet".

### If something's wrong

| What you see | Fix |
|---|---|
| "Could not connect to the server" | Phone on the same Wi-Fi? Settings → Privacy & Security → Local Network → **Shortcuts** on. Still failing: in step 18, use `http://192.168.0.118/plan?first=` (that IP can change) |
| The Pico says "Bad value" | Step 16's Format String must be exactly `HHmm`, and step 18's URL must have no spaces |
| Notification shows the wrong event | Check step 13: **is between From and To**, all-day events excluded, sorted **Oldest First**, limit **1** |
| It never runs at night | Automation tab → tap it → **Enable This Automation** on, and Run Immediately chosen (or the confirm switch off) |
| Wrong wake-up time | Change the rules on http://sunrise.local (not in the Shortcut) |

To check the timing logic without waiting for tomorrow, add an event at 07:30 tomorrow and run
the automation again. On a weekday the notification should say *wake-up 06:30*. Delete the event
and run it once more.

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
