"""Read the structured data published in Formula 1's Next.js pages."""

import json
import re
import unicodedata


def normalized_name(value):
    return " ".join(unicodedata.normalize("NFKD", value).encode(
        "ascii", "ignore"
    ).decode().casefold().split())


def flight_records(html: str) -> dict:
    chunks = re.findall(
        r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)', html
    )
    records = {}
    for line in "".join(json.loads(chunk) for chunk in chunks).splitlines():
        key, _, value = line.partition(":")
        try:
            records[key] = json.loads(value)
        except (ValueError, TypeError):
            continue
    return records


def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


def resolve(value, records, seen=frozenset()):
    if isinstance(value, str) and value.startswith("$"):
        key = value[1:]
        if key in records and key not in seen:
            return resolve(records[key], records, seen | {key})
    if isinstance(value, list):
        return [resolve(child, records, seen) for child in value]
    if isinstance(value, dict):
        return {
            key: resolve(child, records, seen) for key, child in value.items()
        }
    return value
