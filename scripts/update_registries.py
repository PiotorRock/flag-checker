import json
import os
import re
import sys
from datetime import datetime, timezone
from html import unescape

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

OUTPUT_PATH = os.path.join("data", "registries.json")

URLS = {
    "foreign_agents": "https://minjust.gov.ru/ru/pages/reestr-inostryannykh-agentov/",
    "undesirable_orgs": "https://minjust.gov.ru/ru/pages/perechen-inostrannyh-i-mezhdunarodnyh-organizacij-deyatelnost-kotoryh-priznana-nezhelatelnoj-na-territorii-rossijskoj-federacii/",
    "banned_orgs": "https://minjust.gov.ru/ru/documents/7822/",
    "terrorists_extremists": "https://www.fedsfm.ru/documents/terrorists-catalog-portal-act",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; FlagCheckerBot/1.0; +https://github.com/)"
}


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


SESSION = make_session()


def fetch(url: str) -> str:
    resp = SESSION.get(url, headers=HEADERS, timeout=(20, 120))
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def soup_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text("\n")
    text = unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def clean_name(value: str) -> str:
    value = unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" \t\r\n-–—;,.")
    return value.strip()


def normalize_key(value: str) -> str:
    value = clean_name(value).lower().replace("ё", "е")
    value = re.sub(r"[\"'«»“”„]", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def add_entity(bucket: dict, name: str, category: str, aliases=None) -> None:
    name = clean_name(name)
    if not name:
        return

    aliases = aliases or []

    all_aliases = []
    for a in aliases:
        a = clean_name(a)
        if a and normalize_key(a) != normalize_key(name):
            all_aliases.append(a)

    key = (category, normalize_key(name))

    if key not in bucket:
        bucket[key] = {
            "name": name,
            "category": category,
            "aliases": [],
        }

    existing_aliases = {normalize_key(x) for x in bucket[key]["aliases"]}
    for a in all_aliases:
        nk = normalize_key(a)
        if nk and nk not in existing_aliases and nk != normalize_key(name):
            bucket[key]["aliases"].append(a)
            existing_aliases.add(nk)


def split_aliases_from_parentheses(name: str):
    aliases = []
    for m in re.findall(r"\(([^()]+)\)", name):
        part = clean_name(m)
        if part:
            aliases.append(part)

    base = re.sub(r"\([^()]*\)", "", name)
    base = clean_name(base)
    return base, aliases


def parse_numbered_lines(text: str):
    results = []

    for raw_line in text.splitlines():
        line = clean_name(raw_line)
        if not line:
            continue

        m = re.match(r"^\d+\.\s*(.+?)\s*$", line)
        if not m:
            continue

        value = clean_name(m.group(1))
        if value:
            results.append(value)

    return results


def parse_minjust_simple_list(text: str):
    items = []

    for item in parse_numbered_lines(text):
        lower = item.lower()

        if len(item) < 3:
            continue
        if lower.startswith("реестр "):
            continue
        if lower.startswith("перечень "):
            continue
        if "дата рождения" in lower:
            continue

        items.append(item)

    return items


def parse_fedsfm_list(text: str):
    items = []

    for raw_line in text.splitlines():
        line = clean_name(raw_line)
        if not line:
            continue

        m = re.match(r"^\d+\.\s*(.+?)\s*$", line)
        if not m:
            continue

        body = clean_name(m.group(1))
        if not body:
            continue

        # обрезаем типичные хвосты с датой рождения/служебной инфой
        body = re.split(r",\s*\d{2}\.\d{2}\.\d{4}", body)[0]
        body = re.split(r"\b\d{2}\.\d{2}\.\d{4}\b", body)[0]
        body = re.split(r",\s*\d{4}\s*г", body)[0]
        body = clean_name(body)

        if body and len(body) >= 3:
            items.append(body)

    return items


def make_aliases(name: str):
    aliases = set()

    if not name:
        return []

    aliases.add(name)
    aliases.add(name.lower())
    aliases.add(name.upper())
    aliases.add(name.replace("ё", "е"))
    aliases.add(name.replace("Е", "Ё").replace("е", "ё"))

    base, paren_aliases = split_aliases_from_parentheses(name)
    if base:
        aliases.add(base)
        aliases.add(base.lower())

    for a in paren_aliases:
        aliases.add(a)
        aliases.add(a.lower())

    cleaned = []
    seen = set()

    for item in aliases:
        item = clean_name(item)
        key = normalize_key(item)
        if item and key not in seen:
            cleaned.append(item)
            seen.add(key)

    return cleaned


def counts_from_entities(entities):
    counts = {
        "foreign_agents": 0,
        "undesirable_orgs": 0,
        "banned_orgs": 0,
        "terrorists_extremists": 0,
        "total_entities": len(entities),
    }

    for item in entities:
        cat = item["category"]
        counts[cat] = counts.get(cat, 0) + 1

    return counts


def load_previous():
    if not os.path.exists(OUTPUT_PATH):
        return None

    with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_payload(payload):
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def get_previous_entities(previous, category):
    if not previous:
        return []

    return [
        item
        for item in previous.get("entities", [])
        if item.get("category") == category
    ]


def build_entities(previous=None):
    bucket = {}
    failed_sources = []

    source_configs = [
        ("foreign_agents", URLS["foreign_agents"], parse_minjust_simple_list),
        ("undesirable_orgs", URLS["undesirable_orgs"], parse_minjust_simple_list),
        ("banned_orgs", URLS["banned_orgs"], parse_minjust_simple_list),
        ("terrorists_extremists", URLS["terrorists_extremists"], parse_fedsfm_list),
    ]

    for category, url, parser in source_configs:
        try:
            html = fetch(url)
            text = soup_text(html)

            parsed = 0
            for raw in parser(text):
                base, extra_aliases = split_aliases_from_parentheses(raw)
                aliases = make_aliases(base) + extra_aliases
                add_entity(bucket, base, category, aliases)
                parsed += 1

            print(f"{category}: parsed {parsed}")

            if parsed == 0:
                raise RuntimeError(f"{category}: source returned 0 parsed entries")

        except Exception as exc:
            print(f"WARNING: {category} failed: {exc}", file=sys.stderr)
            failed_sources.append({"category": category, "error": str(exc)})

            previous_items = get_previous_entities(previous, category)
            for item in previous_items:
                add_entity(
                    bucket,
                    item["name"],
                    item["category"],
                    item.get("aliases", []),
                )

            print(
                f"{category}: kept previous entries {len(previous_items)}",
                file=sys.stderr,
            )

    entities = sorted(
        bucket.values(),
        key=lambda x: (x["category"], normalize_key(x["name"])),
    )

    return entities, failed_sources


def main():
    previous = load_previous()

    try:
        entities, failed_sources = build_entities(previous=previous)
        counts = counts_from_entities(entities)

        if counts["total_entities"] == 0:
            raise RuntimeError("После обновления нет ни одной записи вообще.")

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sources": URLS,
            "counts": counts,
            "failed_sources": failed_sources,
            "entities": entities,
        }

        save_payload(payload)

        print(
            json.dumps(
                {
                    "counts": counts,
                    "failed_sources": failed_sources,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)

        if previous:
            print("Keeping previous registries.json", file=sys.stderr)
            save_payload(previous)
            return 0

        fallback = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sources": URLS,
            "counts": {
                "foreign_agents": 2,
                "undesirable_orgs": 1,
                "banned_orgs": 0,
                "terrorists_extremists": 0,
                "total_entities": 3,
            },
            "failed_sources": [{"category": "all", "error": str(exc)}],
            "entities": [
                {
                    "name": "Моргенштерн",
                    "category": "foreign_agents",
                    "aliases": ["morgenstern", "Morgenstern", "Алишер Моргенштерн"],
                },
                {
                    "name": "Алишер Моргенштерн",
                    "category": "foreign_agents",
                    "aliases": [],
                },
                {
                    "name": "FREE RUSSIA FOUNDATION",
                    "category": "undesirable_orgs",
                    "aliases": ["Free Russia Foundation"],
                },
            ],
        }

        save_payload(fallback)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
