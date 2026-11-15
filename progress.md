# Progress

## Current state (15 Nov 2026)

- `pico/main.py` with the sunrise lamp is on the Pico W and running (MicroPython 1.29.0).
  Boot log shows `WiFi connected`, `Clock synced` and `Web server running`.
- Lamp tested live on the real bulb: 1-minute test sunrise (10%→100%, 2200K→4000K), auto-off,
  hand-change detection, and every web command. Test state was wiped afterwards.
- Printer tested live: trigger (GitHub run at 19:13 UTC, "Brief 2026-11-15"), fetch and print.
  The print was faint because the Pico was on laptop USB, not the 5V supply.
- 35 Mac-side tests pass (`python3 pico/test_sunrise.py`).
- Work is on branch `sunrise-lamp`.

## Next steps (Charlie)

1. Move the Pico back to the 5V supply and press BOOTSEL once (SUNRISE.md, last section).
   Must happen before 07:40 tomorrow or the 08:00 print will be faint again.
2. Build the iPhone Shortcut (SUNRISE.md section B) and run it once by hand.
3. Merge `sunrise-lamp` into `main` once happy.

## Open questions

- Shortcut screen labels on the current iOS haven't been checked against Apple's page
  (support.apple.com was unreachable from here).
- `sunrise.local` worked from the Mac (IPv6 and IPv4). Not yet tried from the iPhone.

## Decisions

| Decision | Why | Alternatives | Who |
|---|---|---|---|
| Wake-up rules live on the Pico; the Shortcut only sends tomorrow's first event (`/plan?first=HHmm`) | ~8 actions in the Shortcut instead of ~20; rules tested on the Mac; times changeable from the settings page | All logic in the Shortcut, sending `/wake?in=SECONDS` (kept as a manual command) | Charlie agreed, asked for easy time changes |
| Settings page at http://sunrise.local, saved on the Pico | Change times from the phone without re-uploading code | Edit constants and re-upload | Claude, to meet "easily change the time" |
| Weekends: only wake for events before 11:00 | Otherwise an evening event switches the lamp on at, say, 18:00 | Any event | Charlie |
| Weekdays: wake at the earlier of 08:30 and 1 h before the first event | Same as "event before 09:30 → 1 h before" with defaults, and still sensible if the settings change | Fixed 09:30 cut-off | Claude |
| Ignore events before 04:00 | A 00:30 event would otherwise switch the lamp on at 23:30 | No guard | Claude |
| Missed wake-ups (Pico off at the time) are dropped, not replayed | Avoids a sunrise at 14:00 after a power cut | Replay late | Claude |
| Touching the lamp stops that morning's sunrise; auto-off only if the lamp is still at 100% | Don't fight the user | Ignore manual changes | Claude |
| Lamp code wrapped in try/except in the main loop | A lamp bug must never stop the printer | None | Claude |
| Status log on the web page, saved on the Pico | The printer hadn't worked for 3 days with no way to see why | Serial log only (lost unless a laptop is attached) | Charlie asked |
| Auto-restart after a crash, with doubling waits | A crash used to stop everything, page included; waits stop a crash loop from flooding GitHub | Stay stopped | Claude |
| Test sunrise restores the lamp afterwards | Turning the lamp off after a test surprised Charlie | Turn off | Claude, after Charlie's question |

## Log (newest first)

### 15 Nov 2026 (evening)
- Added a status log shown on http://sunrise.local: a Printer card (build, download, print,
  last problem) and the last 25 events, saved in `status.json` on the Pico. Errors from Wi-Fi,
  the clock, the GitHub trigger (with GitHub's reason), the download, the lamp and crashes are
  all recorded. `/status` includes the printer line too.
- Crash recovery: `run()` wraps `main()`, saves the traceback, then restarts the Pico after
  30 s, doubling per crash in a row (max 30 min); the count resets after an hour of running.
- Test sunrise now puts the lamp back as it was (scene, white or colour) instead of turning it
  off. Charlie's lamp went off after a test at 19:39, which prompted this.
- 35 tests pass. Uploaded and checked live: boot log, page card, test-and-restore on the bulb.

### 15 Nov 2026
- Brought the local repo up to date with GitHub (it was 17 commits behind), branched `sunrise-lamp`.
- Rewrote the lamp section of `pico/main.py`: wake-up rules on the Pico, settings page,
  one-off/cancel, time-anchored ramp (resumes correctly after a reboot), 3 retries per bulb
  command plus re-discovery, auto-off, hand-change detection, Wi-Fi keep-alive every minute,
  Wi-Fi power saving off, IPv6 + IPv4 web server.
- Fixed a printer start-up bug found while testing: if the first Wi-Fi join failed, `main()`
  retried NTP forever and never reconnected.
- Added `pico/test_sunrise.py` (26 tests), `SUNRISE.md` (settings, Shortcut steps, power steps).
- Uploaded to the Pico (only `main.py`; `config.py` untouched). Backup of the old `main.py` is
  identical to `origin/main` at commit 0803359.
