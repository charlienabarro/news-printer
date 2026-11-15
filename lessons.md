# Lessons

- **Printer on laptop USB prints slowly and faintly.** The printer shares the Pico's 5V rail, so
  on USB it gets ~0.5A instead of the 1.5–2A it needs. Test prints only on the 5V supply.
- **The Pico prints on any boot after 10:00 if nothing was printed that day**, and triggers a
  GitHub build first. Restarting it for testing counts. Write today's date to `last_printed.txt`
  first if a print isn't wanted.
- **The Pico W's first Wi-Fi join after a hard reset can hang in "joining"**; calling
  `wlan.connect()` again works within seconds. `main()` now retries Wi-Fi as well as NTP.
- **mDNS gives `sunrise.local` IPv6 addresses too**, and Macs/iPhones try IPv6 first. A server
  bound to `0.0.0.0` resets those connections. Bind an `AF_INET6` socket to `"::"`; on
  MicroPython 1.29 it accepts IPv4 and IPv6.
- **`network.WLAN.PM_NONE` is `0x10` on MicroPython 1.29**, not the old raw value `0xa11140`.
  Use the named constant.
- **macOS refuses UDP to 255.255.255.255** ("No route to host"); use the subnet broadcast
  (192.168.0.255) or the bulb's IP when testing from the Mac. The Pico sends to both.
- **The WiZ bulb reports back exactly the dimming/temp it was given**, so comparing `getPilot`
  with the last value sent reliably detects a manual change.
- **mpremote and another serial reader can't share the port**: the reader eats the raw-REPL
  replies and mpremote hangs. After mpremote, the board sits in raw REPL; send Ctrl-B first.
- **Never copy `config.py` to the Pico from this repo**: the repo copy is a template; the Pico's
  copy holds the Wi-Fi password and GitHub token.
