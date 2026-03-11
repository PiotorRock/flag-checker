import json
import os
import re
import sys
from datetime import datetime, timezone
from html import unescape

import requests
from bs4 import BeautifulSoup

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

MIN_REASONABLE_TOTAL = 100


def fetch(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=60)
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
    value = re.sub(r"\s+", " ", value).strip(" \t\r\n-–—;,.")
    return value.strip()


def normalize_key(value: str) -> str:
    value = clean_name(value).lower().replace("ё", "е")
    value = re.sub(r"[\"'«»“”„]", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def add_entity(bucket: dict, name: str, category: str, aliases=None):
    name = clean_name(name)
    if not name:
        return
    if aliases is None:
        aliases = []

    all_aliases = []
    for a in aliases:
        a = clean_name(a)
        if a and a != name:
            all_aliases.append(a)

    key = (category, normalize_key(name))
    if key not in bucket:
        bucket[key] = {
            "name": name,
            "category": category,
            "aliases": [],
        }

    existing = {normalize_key(x) for x in bucket[key]["aliases"]}
    for a in all_aliases:
        nk = normalize_key(a)
        if nk and nk not in existing and nk != normalize_key(name):
            bucket[key]["aliases"].append(a)
            existing.add(nk)


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
    """
    Универсальный парсер строк вида:
    1158. Иванов Иван Иванович
    1. АБАДИЕВ МАГОМЕД..., 09.11.1982 г.
    """
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
    """
    Для страниц Минюста, где список обычно идет нумерованными строками.
    """
    items = []
    for item in parse_numbered_lines(text):
        # Отсекаем служебные строки
        if len(item) < 3:
            continue
        if item.lower().startswith("реестр "):
            continue
        items.append(item)
    return items


def parse_fedsfm_list(text: str):
    """
    Для Росфинмониторинга:
    1. ФИО..., 01.01.1980 г.
    Берем все до даты/г.р./запятой, плюс алиасы из скобок.
    """
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

        # Обрезаем типичные хвосты с датой рождения и географией
        body = re.split(r",\s*\d{2}\.\d{2}\.\d{4}", body)[0]
        body = re.split(r",\s*\d{4}\s*г", body)[0]
        body = re.split(r"\b\d{2}\.\d{2}\.\d{4}\b", body)[0]
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


def build_entities():
    bucket = {}

    # 1) Иноагенты
    html = fetch(URLS["foreign_agents"])
    text = soup_text(html)
    for raw in parse_minjust_simple_list(text):
        base, extra_aliases = split_aliases_from_parentheses(raw)
        aliases = make_aliases(base) + extra_aliases
        add_entity(bucket, base, "foreign_agents", aliases)

    # 2) Нежелательные организации
    html = fetch(URLS["undesirable_orgs"])
    text = soup_text(html)
    for raw in parse_minjust_simple_list(text):
        base, extra_aliases = split_aliases_from_parentheses(raw)
        aliases = make_aliases(base) + extra_aliases
        add_entity(bucket, base, "undesirable_orgs", aliases)

    # 3) Запрещенные / ликвидированные организации
    html = fetch(URLS["banned_orgs"])
    text = soup_text(html)
    for raw in parse_minjust_simple_list(text):
        base, extra_aliases = split_aliases_from_parentheses(raw)
        aliases = make_aliases(base) + extra_aliases
        add_entity(bucket, base, "banned_orgs", aliases)

    # 4) Террористы / экстремисты
    html = fetch(URLS["terrorists_extremists"])
    text = soup_text(html)
    for raw in parse_fedsfm_list(text):
        base, extra_aliases = split_aliases_from_parentheses(raw)
        aliases = make_aliases(base) + extra_aliases
        add_entity(bucket, base, "terrorists_extremists", aliases)

    entities = sorted(
        bucket.values(),
        key=lambda x: (x["category"], normalize_key(x["name"]))
    )
    return entities


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


def main():
    previous = load_previous()

    try:
        entities = build_entities()
        counts = counts_from_entities(entities)

        # Защита от "успешного" пустого парсинга
        if counts["total_entities"] < MIN_REASONABLE_TOTAL:
            raise RuntimeError(
                f"Собрано слишком мало записей: {counts['total_entities']}. "
                "Похоже, верстка источников изменилась."
            )

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sources": URLS,
            "counts": counts,
            "entities": entities,
        }

        save_payload(payload)
        print(json.dumps(counts, ensure_ascii=False))
        return 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)

        # Не затираем рабочий файл пустотой
        if previous:
            print("Keeping previous registries.json", file=sys.stderr)
            return 1

        # Если файла раньше не было — пишем минимальный fallback
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
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
