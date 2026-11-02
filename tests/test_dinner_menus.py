import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pymupdf

from extract_dinner_menus import extract_catalog, parse_pages
from recipe_agent import menu_catalog


class DinnerMenusTest(unittest.TestCase):
    def test_first_main_meal_heading_is_included_in_lunch_extraction(self):
        menus, warnings = parse_pages(["1.Ana Öğün (12.00-13.00):\n2.seçenek:\n"
                                       "• Tatlı Tuzlu Ekmek: 2 ekmek + avokado + hellim\n"
                                       "• Bol sebze\nAkşam (20.00):\n• Çorba"], meal="lunch")
        self.assertEqual(warnings, [])
        self.assertEqual(len(menus), 1)
        self.assertEqual(len(menus[0]["items"]), 2)
        self.assertNotIn("Çorba", str(menus))

    def test_page_continuation_options_and_privacy_boundary(self):
        pages = [
            "Sn. Test Kişisi\nKahvaltı (09.00):\n• Yumurta\n"
            "Akşam (18.00-19.00):\n*Öğle ve akşam yer değiştirebilir.\n"
            "1.seçenek (2 GÜN):\n• 1 porsiyon patlıcanlı tirit\n• 4 yemek kaşığı yoğurt VEYA",
            "1 büyük su bardağı ayran\n• Bol salata\n2.seçenek:\n"
            "• Ton Balıklı Salata: 120 g ton balığı +\nMor lahana + Limon\n"
            "Test\nKişisi\n22.03.2023 29.03.2023\n50.7 kg\nBel\n70 cm",
        ]
        menus, warnings = parse_pages(pages)
        self.assertEqual(warnings, [])
        self.assertEqual(len(menus), 2)
        self.assertEqual(menus[0]["pages"], [1, 2])
        self.assertEqual(menus[0]["items"][1], "4 yemek kaşığı yoğurt VEYA 1 büyük su bardağı ayran")
        self.assertEqual(menus[1]["notes"], ["*Öğle ve akşam yer değiştirebilir."])
        self.assertEqual(menus[1]["items"], ["Ton Balıklı Salata: 120 g ton balığı + Mor lahana + Limon"])
        self.assertNotIn("Kişisi", json.dumps(menus, ensure_ascii=False))

    def test_recipe_and_later_meal_are_not_dinner_items(self):
        for boundary in ["Pırasalı Tava Keki (2 porsiyon)", "TAVADA PİZZA", "Gece (21.00):"]:
            with self.subTest(boundary=boundary):
                menus, _ = parse_pages(["Akşam:\n1.seçenek:\n• 1 kase çorba\n" + boundary + "\n• 3 yumurta"])
                self.assertEqual(menus[0]["items"], ["1 kase çorba"])

    def test_inline_ingredients_are_not_recipe_boundaries(self):
        menus, _ = parse_pages(["Akşam:\n1.seçenek:\n• Sezar Salata:\n"
                                "-1 adet tam tahıllı ekmekten kruton (yoksa 2 adet wasa)\n"
                                "Sos: 1 tatlı kaşığı yağ\n• 1 su bardağı ayran\nGece:"])
        self.assertEqual(len(menus[0]["items"]), 2)
        self.assertIn("Sos: 1 tatlı kaşığı yağ", menus[0]["items"][0])

    def test_pdf_extraction_deduplicates_and_searches_only_explicit_food(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, footer in [("private-name.pdf", "GECE"), ("private-other.pdf", "Gece:")]:
                doc = pymupdf.open()
                page = doc.new_page()
                page.insert_text((50, 50), "Aksam:\n• 1 porsiyon tirit\n• Bol salata\n" + footer)
                doc.save(root / name)
                doc.close()
            recipes = [{"name": "tirit"}, {"name": "pırasa böreği"}]
            catalog, report = extract_catalog(root, recipes)
            self.assertEqual(report["pdf_count"], 2)
            self.assertEqual(len(catalog["menus"]), 1)
            menu = catalog["menus"][0]
            self.assertEqual(menu["linked_recipes"], ["tirit"])
            self.assertEqual(len(menu["sources"]), 2)
            self.assertNotIn("private", json.dumps(catalog))
            path = root / "menus.json"
            path.write_text(json.dumps(catalog))
            with patch.object(menu_catalog, "MENUS_PATH", path):
                result = json.loads(menu_catalog.find_dinner_menus("TİRİT"))
                self.assertEqual(result["total_matches"], 1)
                self.assertEqual(json.loads(menu_catalog.find_dinner_menus("pırasa böreği"))["status"], "no_match")
                self.assertEqual(json.loads(menu_catalog.find_dinner_menus("tirit", [menu["id"]]))["remaining_count"], 0)
                self.assertEqual(json.loads(menu_catalog.get_dinner_menu(menu["id"]))["items"], menu["items"])

    def test_missing_catalog_is_distinct_from_no_match(self):
        with patch.object(menu_catalog, "MENUS_PATH", Path("/nonexistent/dinner_menus.json")):
            self.assertEqual(json.loads(menu_catalog.find_dinner_menus())["status"], "not_extracted")

    def test_lunch_and_dinner_share_catalog_and_keep_original_source_meal(self):
        text = ("Öğle (12.00-13.00):\n4.seçenek (1-2 GÜN):\n"
                "• 1 porsiyon pırasa böreği(tarifi aşağıda)\n"
                "• 1 kase salata(yağsız)\n• 1 büyük su bardağı ayran\n"
                "Akşam (18.00):\n1.seçenek:\n"
                "• 1 porsiyon pırasa böreği(tarifi aşağıda)\n"
                "• 1 kase salata(yağsız)\n• 1 büyük su bardağı ayran\nGece:")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "source.pdf").write_bytes(b"fixture")
            page = MagicMock()
            page.get_text.return_value = text
            doc = MagicMock()
            doc.__enter__.return_value = [page]
            with patch("extract_dinner_menus.pymupdf.open", return_value=doc):
                catalog, _ = extract_catalog(root, [{"name": "PIRASA BÖREĞİ"}])
            self.assertEqual(len(catalog["menus"]), 1)
            menu = catalog["menus"][0]
            self.assertEqual(menu["meal"], "dinner")
            self.assertEqual(menu["title"], "Akşam yemeği menüsü")
            self.assertEqual({s["original_meal"] for s in menu["sources"]}, {"lunch", "dinner"})
            path = root / "menus.json"
            path.write_text(json.dumps(catalog))
            with patch.object(menu_catalog, "MENUS_PATH", path):
                result = json.loads(menu_catalog.find_dinner_menus("PIRASA BÖREĞİ"))
                self.assertEqual(result["total_matches"], 1)
                self.assertEqual(result["menus"][0]["items"], menu["items"])


if __name__ == "__main__":
    unittest.main()
