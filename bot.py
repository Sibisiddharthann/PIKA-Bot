"""Daily tech news Telegram bot.

Fetches the top stories from Hacker News, optionally writes a one-line summary
of each article with Google Gemini (free tier), and pushes a formatted digest
to a Telegram chat. Designed to be run once per day (e.g. from a GitHub Actions
cron), so it does no polling and exits after sending.

Required environment variables:
    TELEGRAM_BOT_TOKEN   token from @BotFather
    TELEGRAM_CHAT_ID     the chat/channel id to send to

Optional:
    GEMINI_API_KEY       if set, each article is summarized in one line.
                         Get a free key at https://aistudio.google.com
    GEMINI_MODEL         Gemini model id (default gemini-2.0-flash)
    NEWS_STORY_COUNT     how many stories to include (default 8)
"""

import os
import re
import sys
import json
import html
import datetime
import urllib.parse

import requests

HN_TOP = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{}.json"
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
GEMINI_API = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)

STORY_COUNT = int(os.environ.get("NEWS_STORY_COUNT", "8"))
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Browser-ish UA so fewer sites reject the article fetch.
UA = {"User-Agent": "Mozilla/5.0 (compatible; TechNewsBot/1.0)"}


def fetch_top_stories(count):
    """Return the top `count` Hacker News stories as dicts."""
    ids = requests.get(HN_TOP, timeout=20).json()
    stories = []
    for story_id in ids:
        if len(stories) >= count:
            break
        item = requests.get(HN_ITEM.format(story_id), timeout=20).json()
        if not item or item.get("type") != "story" or "title" not in item:
            continue
        stories.append(item)
    return stories


def fetch_article_text(url, max_chars=3500):
    """Best-effort extraction of an article's main text. Returns '' on failure.

    Skips obvious non-HTML (PDFs) and known-unreadable hosts. Pulls visible
    paragraph text only — good enough to summarize, cheap on tokens.
    """
    if not url:
        return ""
    skip_hosts = ("twitter.com", "x.com", "youtube.com", "youtu.be")
    host = urllib.parse.urlparse(url).netloc.lower()
    if url.lower().endswith(".pdf") or any(h in host for h in skip_hosts):
        return ""
    try:
        resp = requests.get(url, headers=UA, timeout=15)
        ctype = resp.headers.get("Content-Type", "")
        if resp.status_code != 200 or "html" not in ctype.lower():
            return ""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        text = " ".join(p for p in paragraphs if len(p) > 40)
        return text[:max_chars]
    except Exception:
        return ""


def summarize_stories(stories):
    """Return {story_id: one-line summary} using Gemini, if a key is set.

    Fetches each article's text and sends them all in ONE Gemini request so we
    stay well under free-tier rate limits. Stories whose text can't be read are
    omitted from the result (the caller falls back to headline-only).
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {}

    articles = []
    for s in stories:
        text = fetch_article_text(s.get("url"))
        if text:
            articles.append({"id": s["id"], "title": s["title"], "text": text})
    if not articles:
        return {}

    prompt = (
        "For each article below, write ONE concise sentence (max 25 words) "
        "explaining what it's about and why it matters. Be specific and factual "
        "— use only the provided text, do not invent details. Return ONLY a "
        "JSON array of objects with keys \"id\" (number) and \"summary\" "
        "(string), nothing else.\n\n"
        + json.dumps(articles, ensure_ascii=False)
    )

    try:
        resp = requests.post(
            GEMINI_API.format(model=GEMINI_MODEL, key=api_key),
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        # Gemini sometimes wraps JSON in ```json ... ``` fences — strip them.
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE)
        parsed = json.loads(raw.strip())
        return {int(item["id"]): item["summary"].strip() for item in parsed}
    except Exception as e:
        print(f"Gemini summarization failed ({e}); sending headlines only.")
        return {}


# Topic sections, checked top to bottom; first match wins. Each entry is
# (emoji, label, keywords-matched-against-title-and-domain).
CATEGORIES = [
    ("🤖", "AI & ML", (
        "ai", "ml", "llm", "gpt", "claude", "openai", "anthropic", "model",
        "neural", "deep learning", "machine learning", "agent", "diffusion",
        "transformer", "chatbot", "inference", "llms", "gemini", "mistral",
    )),
    ("🔐", "Security", (
        "security", "vulnerab", "cve", "exploit", "breach", "hack", "malware",
        "ransomware", "phishing", "encryption", "zero-day", "0-day", "leak",
        "backdoor", "privacy",
    )),
    ("🛠️", "Dev & Code", (
        "rust", "python", "javascript", "typescript", "golang", "compiler",
        "kernel", "linux", "database", "sql", "api", "framework", "open source",
        "git", "github", "code", "programming", "library", "release",
    )),
    ("🔬", "Science", (
        "research", "study", "scientist", "physics", "quantum", "biology",
        "cancer", "brain", "space", "nasa", "climate", "energy", "medic",
        "university", "discover",
    )),
]
OTHER = ("📌", "Also Worth a Look")

# Precompile one regex per category. `\b<kw>` matches the keyword only at a word
# start, so "ai" no longer matches inside "desfontain" and stems like "medic"
# still match "medicine". Hyphens are treated as boundaries on their own.
_CATEGORY_RES = [
    (emoji, label, re.compile(r"(?:\b|(?<=-))(?:" +
        "|".join(re.escape(kw) for kw in keywords) + r")", re.IGNORECASE))
    for emoji, label, keywords in CATEGORIES
]


def domain_of(url):
    """Return a clean source domain like 'economist.com' from a URL."""
    netloc = urllib.parse.urlparse(url).netloc
    return netloc[4:] if netloc.startswith("www.") else netloc


def categorize(story):
    """Pick the best-fitting section label for a story (first match wins)."""
    haystack = story.get("title", "") + " " + (story.get("url") or "")
    for emoji, label, regex in _CATEGORY_RES:
        if regex.search(haystack):
            return emoji, label
    return OTHER


def format_digest(stories, summaries=None):
    """Build a sectioned, HTML-formatted Telegram briefing.

    `summaries` is an optional {story_id: one-line summary} mapping; stories not
    present in it simply render without a summary line.
    """
    summaries = summaries or {}
    today = datetime.date.today().strftime("%A, %B %d")
    lines = [f"<b>📰 Your Tech Briefing</b>", f"<i>{today}</i>"]

    # Bucket stories into sections, preserving HN rank order within each.
    buckets = {}
    order = []
    for s in stories:
        emoji, label = categorize(s)
        key = (emoji, label)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(s)

    for emoji, label in order:
        lines.append(f"\n{emoji} <b>{label}</b>")
        for s in buckets[(emoji, label)]:
            title = html.escape(s["title"])
            hn = f"https://news.ycombinator.com/item?id={s['id']}"
            url = s.get("url") or hn
            src = domain_of(url) if s.get("url") else "news.ycombinator.com"
            score = s.get("score", 0)
            comments = s.get("descendants", 0)
            block = [f'• <a href="{html.escape(url, quote=True)}">{title}</a>']
            summary = summaries.get(s["id"])
            if summary:
                block.append(f"  {html.escape(summary)}")
            block.append(
                f'  <i>{html.escape(src)}</i> · ▲{score} · '
                f'<a href="{html.escape(hn, quote=True)}">💬{comments}</a>'
            )
            lines.append("\n".join(block))
    return "\n".join(lines)


def send_telegram(token, chat_id, text):
    resp = requests.post(
        TELEGRAM_API.format(token=token),
        data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        sys.exit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set.")

    stories = fetch_top_stories(STORY_COUNT)
    if not stories:
        sys.exit("No stories fetched — aborting.")

    summaries = summarize_stories(stories)
    digest = format_digest(stories, summaries)
    send_telegram(token, chat_id, digest)
    print(f"Sent {len(stories)} stories ({len(summaries)} summarized).")


if __name__ == "__main__":
    main()
