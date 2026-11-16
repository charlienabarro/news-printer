"""Builds the morning brief and writes it to brief.txt.

Runs on GitHub Actions. Pulls RSS headlines, asks Gemini for a short summary,
then wraps it to 32 characters so the Pico can print it line by line.

To stop the same stories coming back day after day, the stories printed over the
last few days are kept in history.json and shown to Gemini, which only brings a
story back when there is something new, and then gives just the new part.
Feed items older than MAX_AGE_HOURS and shopping-deal filler are dropped first.

Output format (the Pico relies on this):
    @DATE 2026-11-04        first line, used to check the brief is fresh
    # SECTION               section header
    * Headline              bold headline (already wrapped)
    plain text              body (already wrapped)

`python make_brief.py --dry-run` prints the headlines and the prompt without
calling Gemini or writing any files.
"""

import calendar
import json
import re
import sys
import textwrap
import time
import unicodedata
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

FEEDS = {
    "UK": [
        "https://www.theguardian.com/uk-news/rss",
        "https://feeds.skynews.com/feeds/rss/uk.xml",
    ],
    "World": [
        "https://www.theguardian.com/world/rss",
        "https://feeds.skynews.com/feeds/rss/world.xml",
    ],
    "Tech": [
        "https://hnrss.org/frontpage",
        "https://www.theverge.com/rss/index.xml",
    ],
    "Business": [
        "https://www.theguardian.com/uk/business/rss",
        "https://feeds.skynews.com/feeds/rss/business.xml",
    ],
}
PER_FEED = 8
MAX_AGE_HOURS = 36    # feeds keep old stories listed for days; ignore anything older than this
HISTORY_FILE = "history.json"
HISTORY_DAYS = 5      # how many days of printed stories to remember
WIDTH = 32            # characters per line on 58mm paper at normal size
MODEL = "gemini-3.5-flash-lite"

# Shopping filler, not news: price cuts, deals and buying guides. Kept narrow on purpose,
# so real news such as "a new deal with the EU" or "the discount chain Poundland" survives;
# Gemini is also told to skip this kind of story.
BORING = re.compile(
    r"all-time low|lowest price|\bprice drop|\bheavily discounted|\bdiscounts? on\b"
    r"|[$£€]\s?\d[\d,.]*\s+off\b|\b\d+\s?(%|percent|per cent) off\b|\bon sale (now|for)\b"
    r"|\b(best|top|early|today's|weekend|holiday)\b.{0,25}\bdeals?\b|\bdeals? on\b"
    r"|black friday|cyber monday|prime day|\bcoupon|promo code|gift guide|buying guide"
    r"|\bbest\b.{0,30}\bto buy\b",
    re.IGNORECASE,
)


def clean(text):
    """Strip HTML and squash whitespace."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def to_ascii(text):
    """The printer's default code page is basically ASCII, so convert anything fancy."""
    swaps = {
        "‘": "'", "’": "'", "“": '"', "”": '"',
        "–": "-", "—": "-", "…": "...",
        "£": "GBP ", "€": "EUR ", " ": " ",
    }
    for k, v in swaps.items():
        text = text.replace(k, v)
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", "ignore").decode()


def is_boring(title, summary=""):
    return bool(BORING.search(title + " " + summary))


def is_recent(entry, now):
    """True if the feed item is at most MAX_AGE_HOURS old (or has no date to check)."""
    stamp = entry.get("published_parsed") or entry.get("updated_parsed")
    if not stamp:
        return True
    return now - calendar.timegm(stamp) <= MAX_AGE_HOURS * 3600


def fetch_headlines(now):
    import feedparser

    lines, old, boring = [], 0, 0
    for section, urls in FEEDS.items():
        for url in urls:
            try:
                feed = feedparser.parse(url)
            except Exception as e:
                print(f"Skipping {url}: {e}")
                continue
            for entry in feed.entries[:PER_FEED]:
                title = clean(entry.get("title", ""))
                summary = clean(entry.get("summary", ""))[:300]
                if not is_recent(entry, now):
                    old += 1
                elif is_boring(title, summary):
                    boring += 1
                    print(f"Dropped as filler: {title}")
                else:
                    lines.append(f"[{section}] {title} - {summary}")
    print(f"Kept {len(lines)} headlines; dropped {old} older than {MAX_AGE_HOURS}h and {boring} filler")
    return lines


# ---------- memory of what was printed ----------

def parse_brief(text):
    """Read a brief.txt back into (date, stories), each story {section, headline, text}."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith("@DATE"):
        return None, []
    day = lines[0][6:].strip()
    stories, section, head, body = [], "", [], []
    for line in lines[1:] + ["# END"]:
        line = line.strip()
        if line.startswith("# ") or (line.startswith("* ") and body):
            if head:  # a new section or a new headline ends the story so far
                stories.append({"section": section, "headline": " ".join(head),
                                "text": re.sub(r"- (?=[a-z])", "-", " ".join(body))})
            head, body = [], []
        if line.startswith("# "):
            name = line[2:].strip()
            section = "UK" if name == "UK" else name.title()
        elif line.startswith("* "):
            head.append(line[2:].strip())
        elif line and head:
            body.append(line)
    return day, stories


def remember(history, last_brief, today):
    """Add the last printed brief to the history, then keep only the last HISTORY_DAYS days.

    A brief dated today is left out (re-running the build mustn't hide today's own
    stories), and a day already in the history keeps the version recorded first."""
    day, stories = parse_brief(last_brief)
    if day and day < today and stories and day not in history:
        history[day] = stories
    oldest = (date.fromisoformat(today) - timedelta(days=HISTORY_DAYS)).isoformat()
    return {d: history[d] for d in sorted(history) if oldest <= d <= today}


def format_history(history):
    lines = []
    for day in sorted(history, reverse=True):
        when = date.fromisoformat(day).strftime("%a %d %b")
        for s in history[day]:
            lines.append(f"- {when}, {s['section']}: {s['headline']}. {s['text'][:220]}")
    return "\n".join(lines)


def load_history():
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


# ---------- Gemini ----------

def build_prompt(headlines, history):
    return f"""Here are this morning's headlines from several sources.

Write a morning news brief in UK English. Rules:
- Sections in this order: UK, World, Tech, Business.
- Up to 3 stories per section: the most important or genuinely interesting ones.
  Merge duplicates across sources.
- Never include shopping deals, discounts, price cuts, product reviews, buying
  guides, how-to articles, sponsored content or celebrity gossip. If a section has
  fewer than 3 stories worth reading, include fewer; leave out a section that has none.
- The "Already printed" list below is what the reader saw in the last few days.
  Do not repeat those stories. Only bring one back if today's headlines report a
  clear new development (for example a verdict, a resignation, a vote or new
  figures). Then the headline must state the new development and the text must
  give only what is new, without retelling the background.
- Format EXACTLY like this, nothing else:

# UK
* Short headline, max 8 words
One or two plain sentences on what happened and why it matters.

* Next headline
...

- Be specific. Always name the people (with their role), companies, countries,
  cities and organisations involved, and include key numbers (amounts, percentages,
  dates). Never write vague phrases like "a major company", "ministers" or
  "a European country" when the source names them.
- Only use facts that appear in the headlines below. Do not add details from memory.
- Plain text only. No markdown apart from the '# ' and '* ' markers. No emoji.
- Whole brief under 400 words.

Already printed:
{format_history(history) or "(nothing yet)"}

Headlines:
{chr(10).join(headlines)}"""


def summarise(prompt):
    from google import genai

    client = genai.Client()  # reads GEMINI_API_KEY from the environment
    response = client.models.generate_content(model=MODEL, contents=prompt)
    return response.text


def format_for_printer(brief, today):
    out = [f"@DATE {today}"]
    for raw in to_ascii(brief).splitlines():
        line = raw.strip()
        if not line:
            if out[-1] != "":
                out.append("")
        elif line.startswith("# "):
            out.append("# " + line[2:].upper())
        elif line.startswith("* "):
            wrapped = textwrap.wrap(line[2:], WIDTH)
            out.extend("* " + w for w in wrapped)
        else:
            out.extend(textwrap.wrap(line, WIDTH))
    return "\n".join(out).strip() + "\n"


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    today = datetime.now(ZoneInfo("Europe/London")).strftime("%Y-%m-%d")
    try:
        with open("brief.txt") as f:
            last_brief = f.read()
    except FileNotFoundError:
        last_brief = ""
    history = remember(load_history(), last_brief, today)
    prompt = build_prompt(fetch_headlines(time.time()), history)
    if dry_run:
        print(prompt)
        sys.exit()
    text = format_for_printer(summarise(prompt), today)
    with open("brief.txt", "w") as f:
        f.write(text)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=1)
    print(text)
