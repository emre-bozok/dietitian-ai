"""Tarifler ve menüler için ortak, kişisel isim içermeyen kaynak adı."""

import re
import hashlib
import json
from pathlib import Path


def anonymize_filename(filename: str, idx: int) -> str:
    numbers = re.findall(r"\d+", filename)
    if numbers:
        return f"Diyet_Listesi_{'_'.join(numbers)}.pdf"
    return f"Diyet_Listesi_{idx}.pdf"


def build_source_names(files: list[Path], registry_path: Path) -> dict[Path, str]:
    """İçerik değişmedikçe dosya taşınsa/adı değişse de kaynak numarası değişmez."""
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    if registry_path.exists():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    else:
        registry = {}
        # İlk geçişte mevcut tarif/menü kaynaklarını koru.
        old_map = Path(__file__).resolve().parent / "dinner_menus_sources.local.json"
        if old_map.exists():
            wanted = set(hashes.values())
            for alias, filename in json.loads(old_map.read_text(encoding="utf-8")).items():
                path = Path(filename)
                if path.is_file():
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    if digest in wanted:
                        registry.setdefault(digest, alias)
    if len(set(registry.values())) != len(registry):
        raise ValueError("Kaynak numarası kaydında çakışma var; kayıt dosyasını kontrol edin.")
    used = set(registry.values())
    # Mevcut numaralar önce ayrılır; yeni dosyalar eski kaynakları değiştiremez.
    for idx, path in enumerate(files, 1):
        digest = hashes[path]
        if digest in registry:
            continue
        candidate = anonymize_filename(path.name, idx)
        if candidate in used:
            number = 1
            while f"Diyet_Listesi_{number}.pdf" in used:
                number += 1
            candidate = f"Diyet_Listesi_{number}.pdf"
        registry[digest] = candidate
        used.add(candidate)
    temp = registry_path.with_suffix(".tmp")
    temp.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(registry_path)
    return {path: registry[digest] for path, digest in hashes.items()}
