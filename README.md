# News printer

Prints a morning news brief on a 58mm thermal printer at 8am every day.

- **GitHub Actions** runs `make_brief.py` at 06:30 UTC: pulls RSS headlines, summarises them with Gemini, commits `brief.txt`.
- **Pico W** downloads `brief.txt` after 8am (London time) and prints it over UART.

## 1. GitHub

1. Create a **public** repo called `news-printer` and push this folder to it.
2. Settings → Secrets and variables → Actions → New repository secret: `GEMINI_API_KEY` (from aistudio.google.com/apikey).
3. Actions tab → "Morning brief" → **Run workflow**. Check `brief.txt` appears in the repo.
4. Open `brief.txt`, click **Raw**, and copy the URL. That's your `BRIEF_URL`.

The repo must be public because the Pico reads `brief.txt` without logging in. Only the brief is public; your API key stays in Secrets.

## 2. Pico

1. Hold BOOTSEL while plugging the Pico into your laptop. It shows up as a drive.
2. Drag the MicroPython `.uf2` for **Pico W** onto it (from micropython.org/download/RPI_PICO_W).
3. Install [Thonny](https://thonny.org), select the MicroPython (Raspberry Pi Pico) interpreter.
4. Fill in `pico/config.py`, then save `config.py` and `main.py` onto the Pico.
5. Unplug from the laptop before connecting the 5V supply.

## 3. Wiring

```
5V supply ──> barrel jack terminal  (+) ──┬──> printer power +  (red)
                                          └──> Pico VSYS (pin 39)
                                     (-) ──┬──> printer power -  (black)
                                          └──> Pico GND (pin 38)

Pico GP0 (pin 1)  ──> printer TTL RX
Pico GND (pin 3)  ──> printer TTL GND
```

Use the printer's **TTL** pins, never RS232. Never power the Pico from USB and the 5V supply at the same time.

## 4. Test

- Hold the printer's feed button while powering on to print a self-test. Set `BAUD` in `config.py` to the baud rate it shows.
- Press the Pico's **BOOTSEL** button to print the latest brief on demand.
- If it's past 8am on first boot, it prints straight away.

## Behaviour

- Checks every 10 minutes from 8am until today's brief is there.
- If today's brief still isn't ready by 10am, it prints the latest one with a note.
- Remembers what it printed, so a power cut won't cause a reprint.
- Handles British Summer Time automatically.

## Changing the news

Edit `FEEDS` in `make_brief.py`. Any RSS feed works.
