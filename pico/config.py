# Fill these in, then copy this file and main.py onto the Pico.

WIFI_SSID = "your-wifi-name"
WIFI_PASSWORD = "your-wifi-password"

# Raw URL of brief.txt in your GitHub repo (the repo must be public).
BRIEF_URL = "https://raw.githubusercontent.com/charlienabarro/news-printer/main/brief.txt"

# GitHub token so the Pico can start the brief build itself.
# Fine-grained token, only this repo, permission "Actions: Read and write".
# Keep this file OFF GitHub once filled in.
GITHUB_TOKEN = "github_pat_..."
GITHUB_REPO = "charlienabarro/news-printer"

TRIGGER_HOUR = 7    # ask GitHub to build the brief at this time (London)
TRIGGER_MINUTE = 40
PRINT_HOUR = 8      # local (London) time
PRINT_MINUTE = 0
GIVE_UP_HOUR = 10   # if today's brief still isn't there by now, print whatever exists

BAUD = 9600         # check the printer's self-test page
UART_TX_PIN = 0     # Pico GP0 -> printer RX
UART_RX_PIN = 1     # Pico GP1 <- printer TX (optional)
