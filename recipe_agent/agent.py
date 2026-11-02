import json
import hashlib
import logging
import re
import tempfile
import time
import numpy as np
from pathlib import Path
from rank_bm25 import BM25Okapi
from google import genai
from google.genai import types
from google.adk.agents import Agent
from .menu_catalog import find_dinner_menus, get_dinner_menu

BASE_DIR = Path(__file__).resolve().parent.parent
RECIPES_PATH = BASE_DIR / "recipes.json"
INDEX_PATH = BASE_DIR / "recipes_index.json"

EMBEDDING_MODEL = "gemini-embedding-2"
EMBEDDING_BATCH_SIZE = 20
_SEMANTIC_RETRY_AFTER = 0.0
logger = logging.getLogger(__name__)

def tokenize_tr(text: str) -> list[str]:
    text = text.replace("İ", "i").replace("I", "ı").lower()
    for char in [",", ".", "(", ")", "-", "/", ":"]:
        text = text.replace(char, " ")
    return re.findall(r"[^\W_]+", text, re.UNICODE)

def _search_text(recipe: dict) -> str:
    return (f"Yemek: {recipe.get('name', '')} | Kategori: {', '.join(recipe.get('category', []))}"
            f" | Malzemeler: {', '.join(recipe.get('ingredients', []))}")


def _valid_vector(values, dimension: int | None = None) -> bool:
    try:
        vector = np.asarray(values, dtype=float)
        return (vector.ndim == 1 and vector.size > 0
                and (dimension is None or vector.size == dimension)
                and bool(np.isfinite(vector).all()) and np.linalg.norm(vector) > 0)
    except (TypeError, ValueError):
        return False


def _valid_index(data, fingerprint: str, recipes: list[dict]) -> bool:
    if not isinstance(data, dict):
        return False  # Eski liste biçimi model ve bütünlük bilgisi taşımıyor.
    if (data.get("schema_version") != 2 or data.get("model") != EMBEDDING_MODEL
            or data.get("source_sha256") != fingerprint):
        return False
    rows = data.get("recipes")
    dimension = data.get("dimension")
    if not isinstance(rows, list) or len(rows) != len(recipes):
        return False
    if not isinstance(dimension, int) or dimension <= 0:
        return False
    return all(isinstance(row, dict) and row.get("name") == recipe["name"]
               and row.get("search_text") == _search_text(recipe)
               and _valid_vector(row.get("embedding"), dimension)
               for row, recipe in zip(rows, recipes))


def _embed_batch(client, contents):
    for attempt in range(3):
        try:
            return client.models.embed_content(model=EMBEDDING_MODEL, contents=contents)
        except Exception as exc:
            if getattr(exc, "code", None) != 429 or attempt == 2:
                raise
            delay = 60.0
            details = getattr(exc, "details", {})
            if isinstance(details, dict):
                for detail in details.get("error", details).get("details", []):
                    if isinstance(detail, dict) and "retryDelay" in detail:
                        try:
                            delay = max(1.0, min(60.0, float(detail["retryDelay"].rstrip("s")) + 1))
                        except (TypeError, ValueError):
                            pass
            logger.warning("Embedding dakika kotası: %.0f saniye sonra aynı grup yeniden denenecek.", delay)
            time.sleep(delay)


def get_or_create_index() -> list[dict]:
    recipes = _recipe_catalog()
    if not recipes:
        return []
    fingerprint = hashlib.sha256(RECIPES_PATH.read_bytes()).hexdigest()
    if INDEX_PATH.exists():
        try:
            data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
            if _valid_index(data, fingerprint, recipes):
                return data["recipes"]
        except (ValueError, OSError):
            pass

    client = genai.Client(http_options=types.HttpOptions(timeout=30_000))
    rows = []
    dimension = None
    for start in range(0, len(recipes), EMBEDDING_BATCH_SIZE):
        batch = recipes[start:start + EMBEDDING_BATCH_SIZE]
        # Embedding 2: list[str] tek birleşik vektör üretir. Her tarif ayrı Content olmalı.
        response = _embed_batch(client, [types.Content(parts=[types.Part(text=_search_text(r))]) for r in batch])
        embeddings = response.embeddings or []
        if len(embeddings) != len(batch):
            raise ValueError(f"Embedding sayısı uyuşmuyor: {len(batch)} tarif, {len(embeddings)} vektör.")
        for recipe, embedding in zip(batch, embeddings):
            values = embedding.values
            if not _valid_vector(values, dimension):
                raise ValueError("Geçersiz veya farklı boyutlu embedding döndü.")
            dimension = len(values)
            rows.append({**recipe, "metadata": {"source_pdfs": recipe["source_pdfs"]},
                         "search_text": _search_text(recipe), "embedding": values})

    data = {"schema_version": 2, "model": EMBEDDING_MODEL, "source_sha256": fingerprint,
            "dimension": dimension, "recipes": rows}
    # Hatalı/yarım yanıt mevcut indeksin üzerine yazılamaz.
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=INDEX_PATH.parent,
                                         prefix=INDEX_PATH.name + ".", suffix=".tmp", delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False)
        temp_path.replace(INDEX_PATH)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return rows

def search_recipes(query: str, top_k: int = 5) -> str:
    """Kullanıcının yemek isteğine, malzemeye veya kategoriye göre Hybrid Search yaparak en uygun diyetisyen tariflerini döndürür.

    Args:
        query: Aranacak yemek türü, kategori veya malzeme (örn: 'sebze yemekleri', 'pırasa böreği').
        top_k: Getirilecek maksimum tarif sayısı (varsayılan 5).
    """
    global _SEMANTIC_RETRY_AFTER
    recipes = _recipe_catalog()
    if not recipes or not query.strip() or top_k <= 0:
        return "[]"
    stopwords = {"ve", "ile", "olan", "içeren", "hem", "bir", "tarif", "tarifi", "tarifler",
                 "yemek", "yemeği", "öner", "önerir", "misin", "mısın", "bana", "istiyorum"}
    query_terms = set(tokenize_tr(query)) - stopwords
    complete = [r for r in recipes if query_terms.issubset(set(tokenize_tr(_search_text(r))))]
    prefer_local = len(query_terms) >= 2 and bool(complete)
    if prefer_local:
        # Açık malzeme/ad eşleşmesi varsa API'ye ihtiyaç duyma ve kısmi adaylarla karıştırma.
        recipes = complete
    vec_scores = None
    if not prefer_local and time.monotonic() >= _SEMANTIC_RETRY_AFTER:
        try:
            indexed = get_or_create_index()
            query_client = genai.Client(http_options=types.HttpOptions(timeout=30_000))
            response = query_client.models.embed_content(
                model=EMBEDDING_MODEL, contents=query)
            if not response.embeddings or len(response.embeddings) != 1:
                raise ValueError("Sorgu embedding'i eksik.")
            values = response.embeddings[0].values
            if not _valid_vector(values, len(indexed[0]["embedding"])):
                raise ValueError("Sorgu embedding boyutu indeksle uyuşmuyor.")
            q_vec = np.asarray(values, dtype=float)
            matrix = np.asarray([r["embedding"] for r in indexed], dtype=float)
            vec_scores = matrix @ q_vec / (np.linalg.norm(matrix, axis=1) * np.linalg.norm(q_vec))
            recipes = indexed
        except Exception as exc:
            # Kota/model/bağlantı sorunu tarif aramasını durdurmasın; 60 saniye tekrar yüklenme.
            _SEMANTIC_RETRY_AFTER = time.monotonic() + 60
            logger.warning("Anlamsal arama kullanılamıyor (%s); yerel kelime araması etkin.", type(exc).__name__)

    corpus = [tokenize_tr(_search_text(r)) for r in recipes]
    bm25 = BM25Okapi(corpus)
    bm25_scores = np.array(bm25.get_scores(sorted(query_terms)))
    if bm25_scores.max() > 0:
        bm25_scores = bm25_scores / bm25_scores.max()
    else:
        # Çok küçük kataloglarda BM25 negatif olabilir; eşleşen kelimeleri koru.
        terms = query_terms
        bm25_scores = np.array([len(terms.intersection(tokens)) / max(len(terms), 1) for tokens in corpus])

    hybrid_scores = bm25_scores if vec_scores is None else (0.6 * vec_scores) + (0.4 * bm25_scores)
    coverage = [len(query_terms.intersection(tokens)) for tokens in corpus]
    # İstenen bütün malzemeleri içeren tarif, anlamsal olarak benzer kısmi eşleşmeden önce gelir.
    top_indices = sorted(range(len(recipes)), key=lambda i: (
        bool(query_terms) and coverage[i] == len(query_terms), hybrid_scores[i]), reverse=True)[:top_k]

    results = []
    for idx in top_indices:
        r = recipes[idx]
        results.append({
            "name": r.get("name"),
            "category": r.get("category"),
            "ingredients": r.get("ingredients"),
            "instructions": r.get("instructions"),
            "source_pdfs": r.get("source_pdfs", r.get("metadata", {}).get("source_pdfs", [])),
            "hybrid_score": round(float(hybrid_scores[idx]), 3),
            "search_mode": "keyword" if vec_scores is None else "hybrid",
            "matched_terms": sorted(query_terms.intersection(corpus[idx])),
        })

    return json.dumps(results, ensure_ascii=False, indent=2)

def _recipe_key(value: str) -> str:
    # Türkçe büyük/küçük harfler aynı tarife karşılık gelsin.
    return " ".join(value.replace("İ", "i").replace("I", "ı").lower().split())


def _recipe_catalog() -> list[dict]:
    # Kategori taraması embedding indeksi veya API gerektirmez.
    raw = json.loads(RECIPES_PATH.read_text(encoding="utf-8"))
    unique = {}
    for recipe in raw:
        name_key = _recipe_key(recipe.get("name", ""))
        if not name_key:
            continue
        # Aynı ad altında farklı porsiyon/malzeme sürümlerini tek tarife ezme.
        key = (name_key, tuple(sorted(_recipe_key(x) for x in recipe.get("ingredients", []))),
               _recipe_key(recipe.get("instructions", "")))
        if key not in unique:
            unique[key] = {**recipe, "category": [], "source_pdfs": []}
        entry = unique[key]
        for category in recipe.get("category", []):
            if category not in entry["category"]:
                entry["category"].append(category)
        metadata = recipe.get("metadata", {})
        sources = metadata.get("source_pdfs", []) + ([metadata["source_pdf"]] if metadata.get("source_pdf") else [])
        for source in sources:
            if source not in entry["source_pdfs"]:
                entry["source_pdfs"].append(source)
    return list(unique.values())


def list_recipes(category: str = "", exclude_names: list[str] | None = None) -> str:
    """Genel kategori istekleri için TÜM eşleşen tariflerin kısa listesini döndürür.

    Args:
        category: Etiket; örneğin 'sebze', 'tavuk', 'börek'. Boşsa tüm tarifler.
        exclude_names: Önceki yanıtlarda önerilmiş ve tekrar edilmeyecek tarif adları.
    """
    category_key = _recipe_key(category)
    excluded = {_recipe_key(name) for name in (exclude_names or [])}
    matches = [r for r in _recipe_catalog()
               if not category_key or category_key in {_recipe_key(c) for c in r["category"]}]
    remaining = [r for r in matches if _recipe_key(r["name"]) not in excluded]
    return json.dumps({
        "total_matches": len(matches),
        "remaining_count": len(remaining),
        "recipes": [{"name": r["name"], "category": r["category"]} for r in remaining],
    }, ensure_ascii=False)


def get_recipe(name: str, source_pdf: str = "") -> str:
    """Listeden seçilen tarifin malzemelerini, yapılışını ve kaynaklarını getirir.

    Args:
        name: list_recipes veya search_recipes sonucundaki tam tarif adı.
        source_pdf: Aynı isimli farklı sürümler varsa sonuçtaki kaynak PDF adı.
    """
    matches = [r for r in _recipe_catalog() if _recipe_key(r["name"]) == _recipe_key(name)
               and (not source_pdf or source_pdf in r["source_pdfs"])]
    fields = ("name", "category", "ingredients", "instructions", "source_pdfs")
    if len(matches) == 1:
        return json.dumps({key: matches[0].get(key) for key in fields}, ensure_ascii=False)
    if matches:
        return json.dumps({"name": name, "variants": [{key: r.get(key) for key in fields} for r in matches],
                           "message": "Kaynak listelerde farklı malzeme veya miktarlı sürümler var; kaynakları karıştırma."},
                          ensure_ascii=False)
    return json.dumps({"error": "Bu isimde tarif bulunamadı. list_recipes ile geçerli isimleri kontrol et."}, ensure_ascii=False)


root_agent = Agent(
    name="diyetisyen_tarif_asistani",
    model="gemini-3.5-flash-lite",
    description="Diyetisyen listelerindeki tarifleri ve kayıtlı akşam menülerini bulan asistan.",
    instruction="""Sen kişisel bir diyetisyen tarif asistanısın.
Her yeni tarif önerisi isteğinde, 'başka' gibi takip mesajları dahil, MUTLAKA araç çağır.
'Sebze yemeği öner', 'tavuk yemekleri', 'başka sebze yemeği' gibi genel kategori
isteklerinde önce list_recipes kullan; yalnızca search_recipes top-5 sonucuna dayanma.
list_recipes bütün adayların kısa listesini verir; önerilecek tariflerin detaylarını
get_recipe ile al. Başka istendiğinde önceki önerileri exclude_names ile dışla.
Sebze kategorisi sebzeli börekleri, tava keklerini ve diğer sebze etiketli tarifleri
kapsar. Kullanıcı açıkça dışlamadıkça börekleri eleme. Farklı tarif türlerinden
çeşitli öneriler seç. Kullanıcının malzeme ve yemek türü kısıtlarına uy.
Belirli malzeme veya serbest metin isteğinde search_recipes kullanılabilir.
Arama aracının az sonuç getirmesini veritabanında başka tarif olmadığı şeklinde yorumlama.
Başka tarif yok demeden önce list_recipes ile kalan adayları kontrol et.
Yalnızca araç sonuçlarındaki tarifleri ve yapılış adımlarını kullan; eksik adım uydurma.
instructions boşsa kaynakta ayrı yapılış belirtilmediğini söyle; hazırlama adımı uydurma.
get_recipe variants döndürürse farklı kaynak sürümlerini ve porsiyon farklarını koru.
Birden fazla malzemeyle aramada malzemeleri birlikte içeren tarifleri önce sun.
Sonuçta yalnızca bazı malzemeler varsa bunu açıkça belirt; eksik malzemeyi varmış gibi söyleme.
Önerdiğin tarifin adını, malzemelerini, yapılışını ve source_pdfs kaynaklarını sun.
Kullanıcı akşam menüsü istediğinde MUTLAKA find_dinner_menus çağır.
Seçili bir tarif varsa recipe_name olarak o tarifin tam adını ver.
Başka menü istendiğinde önce gösterilmiş kimlikleri exclude_ids ile dışla.
Aracın limitli sonuçlarını bütün katalog sanma; total_matches ve remaining_count alanlarına bak.
Menü seçimini get_dinner_menu ile getir. Her menü ayrı bir alternatiftir;
farklı menüleri veya seçenekleri birleştirip kaynakta varmış gibi sunma.
Porsiyonları, VEYA alternatiflerini, menü notlarını ve kaynak seçeneğinin gün
bilgisini koru. Kaynak PDF kimliğini ve sayfa numaralarını belirt.
Katalog kullanıcının tercihiyle PDF'lerdeki ÖĞLE ve AKŞAM seçeneklerini birlikte
içerir. İkisini de 'Akşam yemeği menüsü' adıyla sun; öğle kaydı olduğu için eleme.
Bulunan menünün TAMAMINI porsiyonlarıyla göster; yalnızca tarifi tekrar edip
menüyü atlama. original_meal kaynak takibi içindir; kullanıcı sorarsa belirt.
Eşleşme yoksa bu yemek için kayıtlı menü bulunamadığını söyle.
Tarifin bir PDF'de bulunması o PDF'deki her menüyle eşleştiği anlamına gelmez.
Menülerdeki eksik miktarları veya kalorileri tahmin ederek tamamlama.
Menü değiştirme ve kalori eşleştirme henüz desteklenmiyor; yapılmış gibi söyleme.""",
    tools=[search_recipes, list_recipes, get_recipe, find_dinner_menus, get_dinner_menu],
)
