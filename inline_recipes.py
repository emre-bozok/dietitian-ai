"""Öğün içindeki isimli, malzemeleri '+' ile verilen kısa tarifleri yerelde çıkarır."""

import re

from recipe_agent.menu_catalog import normalize

MEAL = re.compile(r"^(kahvaltı|öğle|akşam|1\s*[.)]\s*ana\s*öğün)(?:\s+yemeği)?\s*(?:\(|:|$)", re.I)
NAMED = re.compile(r"^[•●▪◦·]\s*([^:=]{2,80}?)\s*[:=]\s*(.*)$")
BOUNDARY = re.compile(r"^(?:[•●▪◦·*!]|\d+\s*[.)]\s*seçenek|gece\s*[:(]|"
                      r"ara\s*öğün|malzemeler|yapılışı|hazırlanışı|sn\s*\.)", re.I)
COOKING = re.compile(r"pişir|doğra|karıştır|kavur|ekley|haşla|harmanla|dilimle|sürün", re.I)


def extract_inline_recipes(pages: list[str], source_pdf: str) -> list[dict]:
    lines = [(n, line.strip()) for n, page in enumerate(pages, 1)
             for line in page.splitlines() if line.strip()]
    recipes = []
    meal = None
    for i, (page, line) in enumerate(lines):
        heading = MEAL.match(line)
        if heading:
            meal = heading.group(1).lower()
            continue
        if (not line.startswith(("•", "●", "▪", "◦", "·", "*", "!"))
                and line.isupper() and any(c.isalpha() for c in line)):
            meal = None
        match = NAMED.match(line) if meal else None
        if not match:
            continue
        name, first = match.groups()
        name = name.strip()
        if any(c.isdigit() for c in name) or normalize(name) in {"sos", "sosu", "sos için"}:
            continue
        body = [first] if first else []
        source_pages = {page}
        for next_index in range(i + 1, len(lines)):
            next_page, continuation = lines[next_index]
            table_follows = any(re.match(r"^\d{1,2}[./]\d{1,2}[./]\d{2,4}", value)
                                for _, value in lines[next_index + 1:next_index + 3])
            if (BOUNDARY.match(continuation) or MEAL.match(continuation)
                    or (continuation.isupper() and any(c.isalpha() for c in continuation))
                    or re.search(r"\(\s*\d+\s*(?:pors|adet)", continuation, re.I)
                    or re.match(r"^\d{1,2}[./]\d{1,2}[./]\d{2,4}", continuation)
                    or (table_follows and re.fullmatch(r"[^\W\d_]+(?:\s+[^\W\d_]+)?", continuation))):
                break
            body.append(continuation)
            source_pages.add(next_page)
        text = " ".join(body).strip()
        # Pişirme adımlı metni mevcut Gemma çıkarıcısına bırak; burada adım uydurma.
        if "+" not in text or not re.search(r"[\d½¼¾]", text) or COOKING.search(text):
            continue
        ingredients = [part.strip() for part in text.split("+") if part.strip()]
        categories = ["kahvaltı"] if meal == "kahvaltı" else []
        if "ekmek" in normalize(name).split():
            categories.append("ekmek")
        if "hellim" in normalize(text).split():
            categories.append("peynir")
        recipes.append({"name": name, "category": categories, "ingredients": ingredients,
                        "instructions": "", "metadata": {"source_pdf": source_pdf,
                        "source_pages": sorted(source_pages), "extraction": "inline_menu",
                        "source_quote": line + "\n" + text}})
    return recipes


def merge_inline_recipes(recipes: list[dict], inline: list[dict]) -> list[dict]:
    seen = {(normalize(r["name"]), r.get("metadata", {}).get("source_pdf")) for r in recipes}
    merged = list(recipes)
    for recipe in inline:
        key = (normalize(recipe["name"]), recipe["metadata"]["source_pdf"])
        if key not in seen:
            merged.append(recipe)
            seen.add(key)
    return merged
