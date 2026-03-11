from __future__ import annotations

import json
import re
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT_FILE = DATA_DIR / "registries.json"

URLS = {
    "foreign_agents": "https://minjust.gov.ru/ru/pages/reestr-inostryannykh-agentov/",
    "undesirable_orgs": "https://minjust.gov.ru/ru/pages/perechen-inostrannyh-i-mezhdunarodnyh-organizacij-deyatelnost-kotoryh-priznana-nezhelatelnoj-na-territorii-rossijskoj-federacii/",
    "banned_orgs": "https://minjust.gov.ru/ru/documents/7822/",
    "terrorists_extremists": "https://www.fedsfm.ru/documents/terrorists-catalog-portal-act",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; flag-checker-bot/1.0; +https://github.com/)"
}


def fetch_text(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=90)
    response.raise_for_status()
    response.encoding = response.encoding or "utf-8"
    soup = BeautifulSoup(response.text, "lxml")
    text = soup.get_text("\n")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def cleanup_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t\r\n-–;,")


def unique_keep_order(items: list[str]) -> list[str]:
    seen = OrderedDict()
    for item in items:
        clean = cleanup_spaces(item)
        if not clean:
            continue
        seen.setdefault(clean, None)
    return list(seen.keys())


def extract_numbered_items(text: str) -> list[str]:
    items = []
    for line in text.splitlines():
        line = cleanup_spaces(line)
        m = re.match(r"^\d+\.\s+(.+)$", line)
        if m:
            items.append(m.group(1))
    return items


def simplify_name(value: str, category: str) -> str:
    value = cleanup_spaces(value)

    if category == "terrorists_extremists" and "," in value:
        value = value.split(",", 1)[0].strip()

    if category == "banned_orgs":
        value = re.sub(r"\s*\(.*$", "", value).strip()

    value = re.sub(r"^[•·]+\s*", "", value)
    value = value.strip(' "\'')
    return cleanup_spaces(value)


def build_variants(name: str) -> list[str]:
    variants = {name}
    plain = name.replace("«", "").replace("»", "").replace('"', "").strip()
    variants.add(plain)
    variants.add(plain.replace("ё", "е").replace("Ё", "Е"))
    variants.add(re.sub(r"\s*\(.*?\)\s*", " ", plain).strip())
    return unique_keep_order([v for v in variants if len(v) >= 3])


def scrape_category(category: str, url: str) -> list[dict]:
    text = fetch_text(url)
    raw_items = extract_numbered_items(text)
    entities = []

    for raw in raw_items:
        name = simplify_name(raw, category)
        if len(name) < 3:
            continue
        entities.append(
            {
                "name": name,
                "category": category,
                "variants": build_variants(name),
            }
        )

    seen = OrderedDict()
    for entity in entities:
        key = (entity["category"], entity["name"])
        if key not in seen:
            seen[key] = entity
    return list(seen.values())


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    all_entities: list[dict] = []
    counts = {}

    for category, url in URLS.items():
        try:
            items = scrape_category(category, url)
            counts[category] = len(items)
            all_entities.extend(items)
            print(f"{category}: {len(items)}")
        except Exception as exc:
            counts[category] = 0
            print(f"ERROR {category}: {exc}")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": URLS,
        "counts": {
            **counts,
            "total_entities": len(all_entities),
        },
        "entities": all_entities,
    }

    OUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written: {OUT_FILE}")


if __name__ == "__main__":
    main()
