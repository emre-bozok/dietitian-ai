import unittest

from inline_recipes import extract_inline_recipes, merge_inline_recipes


class InlineRecipesTest(unittest.TestCase):
    def test_first_main_meal_recipe_without_invented_steps_or_side_dishes(self):
        pages = ["1.Ana Öğün (12.00-13.00):\n2.seçenek (1-2 GÜN):\n"
                 "• Tatlı Tuzlu Ekmek:\n2 ince dilim ekmek + ½ adet avokado + 2 ince dilim hellim +",
                 "1 dolu çay kaşığı bal + Tuz, pul biber\n• Bol sebze/yeşillik\n• 1 tatlı kaşığı tahin pekmez"]
        rows = extract_inline_recipes(pages, "Diyet_Listesi_31.pdf")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ingredients"], ["2 ince dilim ekmek", "½ adet avokado", "2 ince dilim hellim",
                                                "1 dolu çay kaşığı bal", "Tuz, pul biber"])
        self.assertEqual(rows[0]["instructions"], "")
        self.assertEqual(rows[0]["metadata"]["source_pages"], [1, 2])
        self.assertNotIn("tahin", str(rows))

    def test_private_table_boundary_is_excluded(self):
        pages = ["Kahvaltı:\n• Tatlı Tuzlu Ekmek: 1 ekmek + ½ avokado + hellim\n"
                 "Test\nKişisi\n22.03.2023\n50.7 kg"]
        rows = extract_inline_recipes(pages, "Diyet_Listesi_1.pdf")
        self.assertEqual(rows[0]["ingredients"][-1], "hellim")
        self.assertNotIn("Kişisi", str(rows))

    def test_merge_is_idempotent_but_keeps_other_source_version(self):
        old = extract_inline_recipes(["Kahvaltı:\n• Tatlı Tuzlu Ekmek: 1 ekmek + ½ avokado + hellim"], "old.pdf")
        new = extract_inline_recipes(["Kahvaltı:\n• Tatlı Tuzlu Ekmek: 2 ekmek + ½ avokado + hellim"], "new.pdf")
        combined = merge_inline_recipes(old, old + new)
        self.assertEqual(len(combined), 2)

    def test_cooking_text_is_left_to_model_extractor(self):
        self.assertEqual(extract_inline_recipes(["Kahvaltı:\n• Omlet: 1 yumurta + peynir. Pişirin."], "source.pdf"), [])


if __name__ == "__main__":
    unittest.main()
