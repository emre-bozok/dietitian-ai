"""Yerel menü kataloğu; embedding veya internet çağrısı yapmaz."""

import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MENUS_PATH = BASE_DIR / "dinner_menus.json"


def normalize(text: str) -> str:
    text = text.replace("İ", "i").replace("I", "ı").lower()
    return " ".join(re.findall(r"\w+", text))


def linked_recipe_names(items: list[str], recipes: list[dict]) -> list[str]:
    """Yalnızca adı menüde açıkça geçen tarifleri bağlar; benzerlik tahmini yapmaz."""
    text = " " + normalize(" ".join(items)) + " "
    return sorted({r["name"] for r in recipes
                   if normalize(r.get("name", ""))
                   and " " + normalize(r["name"]) + " " in text})


def _catalog() -> list[dict]:
    if not MENUS_PATH.exists():
        return []
    data = json.loads(MENUS_PATH.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Desteklenmeyen akşam menüsü dosya sürümü.")
    return data["menus"]


def find_dinner_menus(recipe_name: str = "", exclude_ids: list[str] | None = None, limit: int = 5) -> str:
    """Öğle ve akşam kayıtlarını birleştiren akşam yemeği menüsü kataloğunda arar.

    Args:
        recipe_name: Seçilen tarifin adı veya 'pırasa böreği' gibi yemek adı. Boşsa tüm menüler.
        exclude_ids: Önceden gösterilmiş menülerin kimlikleri.
        limit: Bir yanıtta getirilecek menü sayısı (1-20).
    """
    if not MENUS_PATH.exists():
        return json.dumps({"status": "not_extracted", "menus": [],
                           "message": "Önce extract_dinner_menus.py çalıştırılmalı."}, ensure_ascii=False)
    query = " " + normalize(recipe_name) + " "
    excluded = set(exclude_ids or [])
    matches = []
    for menu in _catalog():
        # Aynı PDF'de yer almak tek başına yemek-menü bağlantısı sayılmaz.
        text = " " + normalize(" ".join(menu["items"])) + " "
        if recipe_name.strip() and query not in text:
            continue
        matches.append(menu)
    remaining = [m for m in matches if m["id"] not in excluded]
    selected = remaining[:max(1, min(limit, 20))]
    return json.dumps({
        "status": "ok" if matches else "no_match",
        "title": "Akşam yemeği menüsü",
        "total_matches": len(matches), "remaining_count": len(remaining),
        "menus": [{"id": m["id"], "meal": m["meal"], "items": m["items"], "notes": m["notes"],
                   "linked_recipes": m["linked_recipes"], "sources": m["sources"]}
                  for m in selected],
    }, ensure_ascii=False)


def get_dinner_menu(menu_id: str) -> str:
    """Seçilen akşam menüsünü porsiyonları, seçenek notları ve kaynak sayfalarıyla getirir.

    Args:
        menu_id: find_dinner_menus sonucundaki tam menü kimliği.
    """
    for menu in _catalog():
        if menu["id"] == menu_id:
            return json.dumps(menu, ensure_ascii=False)
    return json.dumps({"error": "Menü bulunamadı; find_dinner_menus kullan."}, ensure_ascii=False)
