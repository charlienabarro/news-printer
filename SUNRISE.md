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
- **Lamp:** **Test sunrise (1 min)** and **Turn off**.

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

I couldn't open Apple's current automation help page, so the labels on the automation screens
(steps 1–6) are from recent iOS versions and may differ slightly on yours. Pick the closest match.

### A. Create the automation

1. Open **Shortcuts** → **Automation** tab (bottom).
2. Tap **+** (top right). If you have no automations yet, tap **New Automation** instead.
3. Tap **Time of Day**.
4. Set the time to **23:00**. Under Repeat choose **Daily**.
5. Choose **Run Immediately**. Turn **Notify When Run** off (the Shortcut shows its own
   notification).
6. Tap **Next** → **New Blank Automation**.

### B. Add the actions

For each step: tap **Add Action** (or the search bar at the bottom), type the action name, tap it.

7. **Adjust Date**. Tap **Add** and change it to **Get Start of Day**. Tap the **Date** slot →
   **Current Date**. *(Today 00:00.)*
8. **Adjust Date** again. Leave it on **Add**. Set the number to **28** and the unit to
   **hours**. The date slot should already show **Adjusted Date** from step 7; if not, tap it and
   pick it. *(Tomorrow 04:00.)*
9. **Adjust Date** again: **Add 20 hours** to the **Adjusted Date** from step 8.
   *(Tomorrow 24:00.)*
10. **Find Calendar Events**. Then:
    - Tap the filter **Start Date is …** → change the condition to **is between**. Tap the first
      date → **Select Variable** → tap the Adjusted Date from **step 8**. Tap the second date →
      the Adjusted Date from **step 9**.
    - Tap **Add Filter** → tap the new filter's first word → **Is All Day**, and set it so all-day
      events are excluded (**Is Not All Day**, or **is** + switch off, depending on what you see).
    - Tap **Sort by** → **Start Date**. Set **Order** → **Oldest First**.
    - Turn **Limit** on and set **Get 1 Calendar Event**.
    - Optional: **Add Filter** → **Calendar** → pick only your own calendars, so shared or
      subscribed calendars can't wake you.
11. **If**. Tap **Input** → **Calendar Events**. Condition: **has any value**.
12. **Format Date** (drag it between **If** and **Otherwise** if it lands below). Tap its
    input → **Calendar Events** → in the property list choose **Start Date**. Tap **Show More**:
    Date Format → **Custom**, Format String → `HHmm` (capital HH, lowercase mm). Time Format →
    **None** if shown.
13. **Text**, placed between **Otherwise** and **End If**. Type `none`.
14. **Get Contents of URL**, placed after **End If**. Type `http://sunrise.local/plan?first=`
    and, straight after the `=`, insert the variable **If Result** (tap **Select Variable**, then
    the **If** block's output).
15. Optional: **Show Notification**. Body: **Contents of URL**. Each night you'll see e.g.
    *"Mon 16 Nov: wake-up 08:00 (60 min before 09:00 event)"*.
16. Tap **Done**.

### C. Test it now

17. Open the automation and tap the **▶** play button. On the first run, allow **Calendar**
    access and **Local Network** access if asked. Without Local Network access the Shortcut can't
    reach the Pico. To fix it later: Settings → Privacy & Security → Local Network → Shortcuts on.
18. Open http://sunrise.local. The top card should show tomorrow's wake-up with the reason,
    not "no calendar info yet".

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
