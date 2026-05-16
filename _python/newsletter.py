import os
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import feedparser
import yaml
from dateutil import parser as dateparser  # kept if you later want date filters
from openai import OpenAI


API_KEY = os.getenv("API_KEY")
API_BASE_URL = os.getenv("API_BASE_URL")
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4.1-mini")  # replace with your Ollama model

SOURCES_YAML = "./_data/sources.yml"
DIGEST_PATH = "newsletter/digest.md"
MAX_CANDIDATES_PER_FEED = 20


@dataclass
class Story:
    title: str
    url: str
    source: str
    category: str


# ------------ Feeds from sources.yml ------------

def load_sources() -> List[dict]:
    if not os.path.exists(SOURCES_YAML):
        print(f"[rss] {SOURCES_YAML} not found", file=sys.stderr)
        return []

    with open(SOURCES_YAML, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or []

    if not isinstance(data, list):
        print(f"[rss] {SOURCES_YAML} must be a list", file=sys.stderr)
        return []

    sources: List[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id", "")).strip()
        url = str(item.get("url", "")).strip()
        if not sid or not url:
            continue
        sources.append({"id": sid, "url": url})

    return sources


def fetch_rss_feeds() -> List[Story]:
    configured = load_sources()
    stories: List[Story] = []

    for src in configured:
        src_id = src["id"]
        feed_url = src["url"]

        try:
            parsed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"[rss] failed {feed_url}: {e}", file=sys.stderr)
            continue

        source_title = src_id or parsed.feed.get("title", feed_url)
        count = 0

        for entry in parsed.entries:
            if count >= MAX_CANDIDATES_PER_FEED:
                break

            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not title or not link:
                continue

            low_title = title.lower()
            if "subscribe" in low_title and "newsletter" in low_title:
                continue

            category = ""
            if "tags" in entry and entry.tags:
                category = entry.tags[0].get("term", "") or ""
            elif "category" in entry:
                category = entry.get("category", "") or ""

            stories.append(
                Story(
                    title=title,
                    url=link,
                    source=source_title,
                    category=category or "?",
                )
            )
            count += 1

    return stories


# ------------ Fallback formatting ------------

def render_fallback(stories: List[Story]) -> str:
    if not stories:
        return "# Weekly Newsletter\n\n_No stories this time._\n"

    today = datetime.now(timezone.utc).date().isoformat()
    lines = [
        f"**Weekly Newsletter — {today}**",
        "",
        "### Picks",
        "",
    ]
    for s in stories:
        lines.append(f"- **[{s.title}]({s.url})**  \n  _{s.source} · {s.category}_")
    return "\n".join(lines) + "\n"


# ------------ LLM helpers (Ollama Cloud / compatible) ------------

def make_client() -> Optional[OpenAI]:
    if not API_KEY:
        return None
    kwargs = {"api_key": API_KEY}
    if API_BASE_URL:
        kwargs["base_url"] = API_BASE_URL
    return OpenAI(**kwargs)


def llm_shortlist(client: OpenAI, stories: List[Story]):
    payload = [
        {
            "title": s.title,
            "url": s.url,
            "category": s.category,
            "source": s.source,
        }
        for s in stories
    ]

    user = "\n".join(
        [
            "Candidate stories for this edition (JSON array). Each url must appear verbatim if selected.",
            textwrap.dedent(
                """
                Return ONLY valid JSON with this shape:
                {
                  "fortnight_brief": "2-4 sentences: what mattered in this period (no links).",
                  "themes": ["short theme 1", "theme 2"],
                  "picks": [
                    {
                      "url": "exact url from input",
                      "editor_note": "one line why readers should care"
                    }
                  ]
                }

                Rules: pick 8–14 items. Maximize diversity across categories; drop near-duplicates;
                prefer substantive reporting or analysis over fluff.
                """
            ).strip(),
            "",
            "Candidates JSON:",
            textwrap.indent(
                textwrap.shorten(str(payload), width=12000, placeholder="..."), "  "
            ),
        ]
    )

    resp = client.chat.completions.create(
        model=MODEL_NAME,
        temperature=0.4,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an editor shortlisting a weekly local news digest. "
                    "Output strict JSON only."
                ),
            },
            {"role": "user", "content": user},
        ],
    )
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        return None

    import json

    try:
        o = json.loads(text)
    except Exception:
        return None

    if not isinstance(o, dict):
        return None

    fortnight_brief = o.get("fortnight_brief") or ""
    themes = o.get("themes") or []
    picks_raw = o.get("picks") or []
    if not isinstance(fortnight_brief, str) or not fortnight_brief:
        return None
    if not isinstance(themes, list):
        themes = []
    themes = [t for t in themes if isinstance(t, str)]

    picks = []
    for p in picks_raw:
        if not isinstance(p, dict):
            continue
        url = p.get("url") or ""
        note = p.get("editor_note") or ""
        if isinstance(url, str) and isinstance(note, str) and url and note:
            picks.append({"url": url, "editor_note": note})

    if not picks:
        return None

    return {
        "fortnight_brief": fortnight_brief,
        "themes": themes,
        "picks": picks,
    }


def llm_write_digest(client: OpenAI, shortlist, stories_by_url):
    enriched = []
    for p in shortlist["picks"]:
        url = p["url"]
        s = stories_by_url.get(url)
        enriched.append(
            {
                "url": url,
                "title": s.title if s else url,
                "category": s.category if s else "?",
                "source": s.source if s else "?",
                "editor_note": p["editor_note"],
            }
        )

    today = datetime.now(timezone.utc).date().isoformat()

    user = "\n".join(
        [
            "Use this shortlisted JSON (with editor notes) to write the final digest body:",
            "",
            textwrap.indent(
                textwrap.shorten(
                    str(
                        {
                            "fortnight_brief": shortlist["fortnight_brief"],
                            "themes": shortlist["themes"],
                            "picks": enriched,
                        }
                    ),
                    width=12000,
                    placeholder="...",
                ),
                "  ",
            ),
            "",
            "Formatting (markdown):",
            f"- Start with one title line: **Weekly Newsletter — {today}**.",
            "- ### From the editors — expand fortnight_brief slightly (no new factual claims).",
            "- ### Themes — bullets from themes; you may merge or rephrase briefly.",
            "- ### Picks — for each pick: **[Title](url)** as a clickable heading (title links to EXACT url),",
            "  then no more than 3 sentences of hook (combine editor_note + your voice). Do NOT repeat the link after the hook.",
            "- Use ONLY urls from picks. No new links, no footnotes, no code blocks.",
            "- Keep total under 3500 characters if possible; tight prose.",
        ]
    )

    resp = client.chat.completions.create(
        model=MODEL_NAME,
        temperature=0.7,
        messages=[
            {
                "role": "system",
                "content": (
                    "You turn a structured shortlist into a readable markdown digest. "
                    "Never invent URLs or facts not implied by the input."
                ),
            },
            {"role": "user", "content": user},
        ],
    )
    body = (resp.choices[0].message.content or "").strip()
    return body or None


# ------------ Orchestration ------------

def curate_digest(stories: List[Story]) -> str:
    if not stories:
        return render_fallback([])

    client = make_client()
    if not client:
        print("[curation] API_KEY missing; using fallback.", file=sys.stderr)
        return render_fallback(stories)

    shortlist = llm_shortlist(client, stories)
    if not shortlist:
        print("[curation] shortlist failed; using fallback.", file=sys.stderr)
        return render_fallback(stories)

    by_url = {s.url: s for s in stories}
    shortlist["picks"] = [p for p in shortlist["picks"] if p["url"] in by_url]
    if not shortlist["picks"]:
        print("[curation] shortlist had no valid urls; fallback.", file=sys.stderr)
        return render_fallback(stories)

    body = llm_write_digest(client, shortlist, by_url)
    if not body:
        print("[curation] writer failed; fallback.", file=sys.stderr)
        return render_fallback(stories)

    return body


def ensure_output_dir():
    os.makedirs(os.path.dirname(DIGEST_PATH), exist_ok=True)


def main():
    stories = fetch_rss_feeds()
    print(f"[main] gathered {len(stories)} RSS stories")

    digest = curate_digest(stories)

    ensure_output_dir()
    with open(DIGEST_PATH, "w", encoding="utf-8") as f:
        f.write(digest)

    print(f"[main] wrote {DIGEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
