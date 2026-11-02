"""Öğle ve akşam menülerini tek akşam yemeği menüsü kataloğunda toplar.

Tarif çıkarımından bağımsızdır; recipes.json ve recipes_index.json dosyalarına dokunmaz.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path

import pymupdf

from recipe_agent.menu_catalog import BASE_DIR, linked_recipe_names
from source_names import build_source_names

DEFAULT_PDF_DIR = Path(str(BASE_DIR / "data" / "pdfs"))
DINNER = re.compile(r"^ak[şs]am(?:\s+yemeği)?\s*(?:\(|:|$)", re.I)
LUNCH = re.compile(r"^(?:öğle(?:\s+yemeği)?|1\s*[.)]\s*ana\s*öğün)\s*(?:\(|:|$)", re.I)
OPTION = re.compile(r"^\d+\s*[.)]\s*seçenek\b", re.I)
OTHER_MEAL = re.compile(
    r"^(?:(?:\d+\s*[.)]\s*)?ara\s*öğün|gece|kahvaltı|öğle(?:\s+yemeği)?)\s*(?:\(|:|$)", re.I)
RECIPE_HEADING = re.compile(r"\([^)]*\d+\s*(?:pors\w*|adet)[^)]*\)\s*:?$", re.I)
PRIVATE_OR_RECIPE = re.compile(
    r"^(?:sn\s*[.]|sayın\b|ad\s*soyad\b|tarih\b|kilo\b|boy\b|bel\b|kalça\b|"
    r"vücut\b|ölçüm\b|malzemeler\b|yapılışı\b|hazırlanışı\b|tarifler\b)", re.I)
BULLET = re.compile(r"^[•●▪◦·]\s*")


def parse_pages(pages: list[str], meal: str = "dinner") -> tuple[list[dict], list[str]]:
    """Sayfa geçişlerini korur; belirsiz sınırda durur, kişisel tabloyu yutmaz."""
    menus, warnings = [], []
    target_heading = {"dinner": DINNER, "lunch": LUNCH}[meal]
    active = False
    current = None
    shared_notes = []
    shared_pages = set()
    dinner_page = None
    last_kind = None

    def new_menu(option: str, page: int) -> dict:
        return {"option": option, "items": [], "notes": list(shared_notes),
                "pages": set(shared_pages) | {dinner_page, page}}

    def finish():
        nonlocal current
        if current:
            current["items"] = [item.strip() for item in current["items"] if item.strip()]
        if current and current["items"]:
            current["pages"] = sorted(current["pages"])
            menus.append(current)
        elif current:
            warnings.append("empty_option")
        current = None

    all_lines = [(n, line.strip()) for n, page in enumerate(pages, 1)
                 for line in page.splitlines() if line.strip()]
    for line_index, (page_number, raw) in enumerate(all_lines):
        line = raw.strip()
        if not line:
            continue
        if target_heading.match(line):
            finish()
            active, dinner_page = True, page_number
            shared_notes, shared_pages, last_kind = [], set(), None
            continue
        if not active:
            continue
        if OPTION.match(line):
            finish()
            current = new_menu(line, page_number)
            last_kind = None
            continue
        if OTHER_MEAL.match(line) or DINNER.match(line) or PRIVATE_OR_RECIPE.match(line):
            finish()
            active = False
            continue
        # Bağımsız tarif başlıkları ve isim/ölçüm tabloları menü değildir.
        is_bullet = bool(BULLET.match(line))
        is_note = line.startswith(("*", "!"))
        continuation_prefix = line.startswith(("-", "+")) or line[0].isdigit()
        # İsim/soyisim satırlarından hemen sonraki tarih/ölçüm tablosunu da ayır.
        upcoming = [s for _, s in all_lines[line_index + 1:line_index + 3]]
        table_follows = any(re.match(r"^\d{1,2}[./]\d{1,2}[./]\d{2,4}\b", s)
                            or re.match(r"^\d+(?:[.,]\d+)?\s*kg\b", s, re.I)
                            for s in upcoming)
        if not is_bullet and not is_note and (
            (RECIPE_HEADING.search(line) and not continuation_prefix)
            or (line.isupper() and any(c.isalpha() for c in line))
            or re.match(r"^\d{1,2}[./]\d{1,2}[./]\d{2,4}\b", line)
            or (table_follows and re.fullmatch(r"[^\W\d_]+(?:\s+[^\W\d_]+)?", line))
        ):
            finish()
            active = False
            continue
        if is_note and current is None:
            shared_notes.append(line)
            shared_pages.add(page_number)
            last_kind = "shared"
            continue
        if is_bullet:
            if current is None:
                current = new_menu("Akşam menüsü", page_number)
            current["items"].append(BULLET.sub("", line))
            current["pages"].add(page_number)
            last_kind = "items"
        elif is_note and current is not None:
            current["notes"].append(line)
            current["pages"].add(page_number)
            last_kind = "notes"
        elif last_kind == "shared":
            shared_notes[-1] += " " + line
            shared_pages.add(page_number)
        elif current and last_kind:
            # Satır kayması: miktar, VEYA ve parantezleri olduğu gibi sakla.
            current[last_kind][-1] += " " + line
            current["pages"].add(page_number)
        else:
            finish()
            active = False
            warnings.append(f"unrecognized_boundary_page_{page_number}")
    finish()
    if dinner_page is None:
        warnings.append(f"no_{meal}_heading")
    return menus, warnings


def extract_catalog(pdf_dir: Path, recipes: list[dict]) -> tuple[dict, dict]:
    files = sorted(p for p in pdf_dir.rglob("*") if p.suffix.lower() == ".pdf")
    if not files:
        raise ValueError("PDF bulunamadı.")
    unique, reports, source_files = {}, [], {}
    aliases = build_source_names(files, pdf_dir / ".source_registry.local.json")
    for pdf in files:
        # Tarif çıkarıcısıyla aynı kaynak adını kullan; kişisel dosya adını gösterme.
        source_id = aliases[pdf]
        source_files.setdefault(source_id, str(pdf.resolve()))
        with pymupdf.open(pdf) as doc:
            pages = [page.get_text("text") for page in doc]
        extracted, warnings = parse_pages(pages)
        for menu in extracted:
            menu["meal"] = "dinner"
        lunches, lunch_warnings = parse_pages(pages, meal="lunch")
        for menu in lunches:
            menu["meal"] = "lunch"
        # Öğle başlığı olmayan listeler olağandır; diğer çıkarım uyarılarını koru.
        warnings.extend(w for w in lunch_warnings if w != "no_lunch_heading")
        extracted.extend(lunches)
        if not any(page.strip() for page in pages):
            warnings.append("no_readable_text")
        for menu in extracted:
            key = json.dumps({"items": menu["items"], "notes": menu["notes"]}, ensure_ascii=False, sort_keys=True)
            menu_id = "aksam_" + hashlib.sha256(key.encode()).hexdigest()[:16]
            if menu_id not in unique:
                unique[menu_id] = {
                    "id": menu_id, "meal": "dinner", "title": "Akşam yemeği menüsü", "items": menu["items"],
                    "notes": menu["notes"],
                    "linked_recipes": linked_recipe_names(menu["items"], recipes),
                    "sources": [],
                }
            source = {"source_pdf": source_id, "pages": menu["pages"], "option": menu["option"],
                      "original_meal": menu["meal"]}
            if source not in unique[menu_id]["sources"]:
                unique[menu_id]["sources"].append(source)
        reports.append({"source_pdf": source_id, "menu_count": len(extracted), "warnings": warnings})
    return ({"schema_version": 1, "menus": list(unique.values())},
            {"pdf_count": len(files), "unique_menu_count": len(unique), "documents": reports,
             "local_source_files": source_files})


def atomic_json(path: Path, data: dict | list):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def save_catalog(catalog: dict, report: dict, output: Path):
    # Gerçek yollar yalnızca özel yerel eşlemede saklanır.
    source_files = report["local_source_files"]
    public_report = {k: v for k, v in report.items() if k != "local_source_files"}
    atomic_json(output, catalog)
    atomic_json(output.with_name(output.stem + "_report.json"), public_report)
    source_path = output.with_name(output.stem + "_sources.local.json")
    atomic_json(source_path, source_files)
    source_path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_dir", nargs="?", type=Path, default=DEFAULT_PDF_DIR)
    parser.add_argument("--output", type=Path, default=BASE_DIR / "dinner_menus.json")
    args = parser.parse_args()
    if not args.pdf_dir.is_dir():
        parser.error("PDF klasörü bulunamadı.")
    recipes = json.loads((BASE_DIR / "recipes.json").read_text(encoding="utf-8"))
    # Tüm okuma başarılı olmadan mevcut katalog değiştirilmez.
    catalog, report = extract_catalog(args.pdf_dir, recipes)
    save_catalog(catalog, report, args.output)
    print(f"{report['pdf_count']} PDF: {len(catalog['menus'])} tekil akşam yemeği menüsü kaydedildi (öğle + akşam).")
    print(f"Uyarılı belge: {sum(bool(d['warnings']) for d in report['documents'])}")


if __name__ == "__main__":
    main()
