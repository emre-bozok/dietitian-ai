import json
from pathlib import Path
import sys
from typing import List
import ollama
from pydantic import BaseModel, Field
import pymupdf  # Deprecation uyarısını kaldırmak için doğrudan pymupdf
from source_names import build_source_names
from extract_dinner_menus import extract_catalog, save_catalog, atomic_json
from inline_recipes import extract_inline_recipes, merge_inline_recipes

BASE_DIR = Path(__file__).resolve().parent

MODEL_NAME = "gemma4:12b"  # Çok yavaşsa 'gemma4:e4b' ile 4 kat hızlı çalıştırabilirsin
DEFAULT_PDF_DIR = str(BASE_DIR / "data" / "pdfs")


class Recipe(BaseModel):
  name: str = Field(description="Yemeğin adı (Örn: Pırasa Böreği)")
  category: List[str] = Field(
      description=(
          "Yemeğin türü ve ana besin grubu etiketleri (Örn: ['sebze', 'börek'])"
      )
  )
  ingredients: List[str] = Field(description="Malzemeler listesi")
  instructions: str = Field(description="Hazırlanışı")


class RecipeList(BaseModel):
  recipes: List[Recipe]


def extract_from_pdf(pdf_path: Path, clean_name: str) -> List[dict]:
  with pymupdf.open(pdf_path) as doc:
    pages = [page.get_text("text") for page in doc]
    text = "\n".join(pages)

  if not text.strip():
    print("  -> Uyarı: PDF içinden okunabilir metin çıkmadı (Görsel/Scan olabilir).")
    return []

  print(f"  -> Okunan metin uzunluğu: {len(text)} karakter. LLM yanıtı bekleniyor...")

  prompt = f"""Aşağıdaki diyetisyen metninde yer alan yemek tariflerini (malzemeleri ve yapılışı olanları) çıkar. 
Günlük öğün/menü tablosunun tamamını tarif olarak alma. Ancak öğünün içinde
'Tatlı Tuzlu Ekmek: 2 dilim ekmek + avokado + hellim ...' gibi adı ve malzemeleri
belirtilmiş kısa tarifleri de çıkar. Ayrı yapılışı yoksa instructions alanını boş bırak;
hazırlama adımı uydurma. Hiç yemek tarifi yoksa boş liste döndür.
Aramalarda kolay bulunması için 'category' alanına ana malzeme türünü (örn: pırasa varsa 'sebze', tavuk varsa 'tavuk' vb.) ekle.

Metin:
{text}"""

  # Canlı takip için stream=True kullanıyoruz
  stream = ollama.chat(
      model=MODEL_NAME,
      messages=[{"role": "user", "content": prompt}],
      format=RecipeList.model_json_schema(),
      stream=True,
      think=False,  # Gemma 4'ün gereksiz düşünme (reasoning) süresini kapatır
      options={
          "temperature": 0.1,
          "num_ctx": 4096,  # Tek PDF için ideal, RAM swap'ı önler
          "num_predict": 2048,  # Sonsuz döngüye girmesini engeller
      },
      keep_alive="15m",
  )

  content = ""
  print("  -> Canlı Çıktı: ", end="", flush=True)
  for chunk in stream:
    token = chunk["message"]["content"]
    content += token
    print(token, end="", flush=True)  # Ekrana anlık basar
  print()  # Alt satıra geç

  recipes = [recipe.model_dump() for recipe in RecipeList.model_validate_json(content).recipes]

  for recipe in recipes:
    recipe["metadata"] = {"source_pdf": clean_name}

  return merge_inline_recipes(recipes, extract_inline_recipes(pages, clean_name))


def main():
  target_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PDF_DIR
  pdf_dir = Path(target_path).expanduser().resolve()

  if not pdf_dir.exists() or not pdf_dir.is_dir():
    print(f"Hata: '{pdf_dir}' klasörü bulunamadı.")
    return 1

  pdf_files = sorted(
      [p for p in pdf_dir.rglob("*") if p.suffix.lower() == ".pdf"]
  )
  if not pdf_files:
    print(f"'{pdf_dir}' içinde hiç PDF bulunamadı.")
    return 1

  output_path = BASE_DIR / "recipes.json"
  all_recipes = []
  failed = 0

  print(f"Toplam {len(pdf_files)} PDF sırayla işlenecek...\n")

  aliases = build_source_names(pdf_files, pdf_dir / ".source_registry.local.json")
  seen_sources = set()
  for idx, pdf_path in enumerate(pdf_files, 1):
    clean_name = aliases[pdf_path]
    if clean_name in seen_sources:
      continue  # Aynı PDF'nin kopyasını yeniden modelle işleme.
    seen_sources.add(clean_name)
    print(f"[{idx}/{len(pdf_files)}] İşleniyor: {pdf_path.name} (Kayıt adı: {clean_name})...")
    try:
      recipes = extract_from_pdf(pdf_path, clean_name)
      all_recipes.extend(recipes)
      print(f"  -> {len(recipes)} tarif eklendi.\n")

    except Exception as e:
      failed += 1
      print(f"\n  -> HATA ({pdf_path.name}): {e}\n")

  if failed:
    print(f"{failed} PDF işlenemedi. Eksik katalog kaydetmemek için mevcut tarifler ve menüler korundu.")
    return 1

  try:
    catalog, report = extract_catalog(pdf_dir, all_recipes)
  except Exception as e:
    print(f"Menü çıkarımı başarısız; mevcut kayıtlar korundu: {e}")
    return 1

  previous = json.loads(output_path.read_text(encoding="utf-8")) if output_path.exists() else None
  atomic_json(output_path, all_recipes)
  save_catalog(catalog, report, BASE_DIR / "dinner_menus.json")
  # Arama eski tariflerin embedding'lerini kullanmasın; ilk aramada yeniden oluşturulur.
  if previous != all_recipes:
    (BASE_DIR / "recipes_index.json").unlink(missing_ok=True)

  print(
      f"\nTamamlandı! Toplam {len(all_recipes)} tarif '{output_path}' dosyasına"
      " kaydedildi."
  )
  print(f"{len(catalog['menus'])} akşam yemeği menüsü de kaydedildi (öğle + akşam).")
  print(f"Menü çıkarımında uyarılı belge: {sum(bool(d['warnings']) for d in report['documents'])}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
