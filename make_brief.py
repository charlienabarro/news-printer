"""Builds the morning brief and writes it to brief.txt.

Runs on GitHub Actions. Pulls RSS headlines, asks Gemini for a short summary,
then wraps it to 32 characters so the Pico can print it line by line.

Output format (the Pico relies on this):
    @DATE 2026-11-04        first line, used to check the brief is fresh
    # SECTION               section header
    * Headline              bold headline (already wrapped)
    plain text              body (already wrapped)
"""

import re
import textwrap
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

from google import genai
import feedparser

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
WIDTH = 32  # characters per line on 58mm paper at normal size
MODEL = "gemini-3.5-flash-lite"


def clean(text):
    """Strip HTML and squash whitespace."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def to_ascii(text):
    """The printer's default code page is basically ASCII, so convert anything fancy."""
    swaps = {
        "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
        "\u2013": "-", "\u2014": "-", "\u2026": "...",
        "\u00a3": "GBP ", "\u20ac": "EUR ", "\u00a0": " ",
    }
    for k, v in swaps.items():
        text = text.replace(k, v)
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", "ignore").decode()


def fetch_headlines():
    lines = []
    for section, urls in FEEDS.items():
        for url in urls:
            try:
                feed = feedparser.parse(url)
            except Exception as e:
                print(f"Skipping {url}: {e}")
                continue
            for entry in feed.entries[:PER_FEED]:
                summary = clean(entry.get("summary", ""))[:300]
                lines.append(f"[{section}] {clean(entry.title)} - {summary}")
    return lines


def summarise(headlines):
    prompt = f"""Here are this morning's headlines from several sources.

Write a morning news brief in UK English. Rules:
- Sections in this order: UK, World, Tech, Business.
- 3 stories per section, the most important ones. Merge duplicates across sources.
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

Headlines:
{chr(10).join(headlines)}"""

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
    today = datetime.now(ZoneInfo("Europe/London")).strftime("%Y-%m-%d")
    headlines = fetch_headlines()
    print(f"Fetched {len(headlines)} headlines")
    brief = summarise(headlines)
    text = format_for_printer(brief, today)
    with open("brief.txt", "w") as f:
        f.write(text)
    print(text)
