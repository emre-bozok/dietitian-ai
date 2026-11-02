import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import extract_recipes


class ExtractionPipelineTest(unittest.TestCase):
    def test_single_command_saves_both_catalogs_and_invalidates_old_index(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf_dir = root / "pdfs"
            pdf_dir.mkdir()
            (pdf_dir / "source.pdf").write_bytes(b"fixture")
            (root / "recipes_index.json").write_text("old index")
            recipe = {"name": "Tirit", "metadata": {"source_pdf": "Diyet_Listesi_1.pdf"}}
            catalog = {"schema_version": 1, "menus": [{"id": "menu"}]}
            report = {"documents": [], "local_source_files": {"Diyet_Listesi_1.pdf": "private/path"}}
            with patch.object(extract_recipes, "BASE_DIR", root), \
                 patch.object(extract_recipes.sys, "argv", ["extract_recipes.py", str(pdf_dir)]), \
                 patch.object(extract_recipes, "extract_from_pdf", return_value=[recipe]), \
                 patch.object(extract_recipes, "extract_catalog", return_value=(catalog, report)) as menus, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(extract_recipes.main(), 0)
            menus.assert_called_once_with(pdf_dir.resolve(), [recipe])
            self.assertEqual(json.loads((root / "recipes.json").read_text()), [recipe])
            self.assertEqual(json.loads((root / "dinner_menus.json").read_text()), catalog)
            self.assertNotIn("private/path", (root / "dinner_menus_report.json").read_text())
            self.assertFalse((root / "recipes_index.json").exists())

    def test_failed_pdf_preserves_previous_catalogs_and_index(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf_dir = root / "pdfs"
            pdf_dir.mkdir()
            (pdf_dir / "source.pdf").write_bytes(b"fixture")
            previous = {"recipes.json": "[]", "dinner_menus.json": "previous menus",
                        "recipes_index.json": "previous index"}
            for name, content in previous.items():
                (root / name).write_text(content)
            with patch.object(extract_recipes, "BASE_DIR", root), \
                 patch.object(extract_recipes.sys, "argv", ["extract_recipes.py", str(pdf_dir)]), \
                 patch.object(extract_recipes, "extract_from_pdf", side_effect=RuntimeError("Ollama unavailable")), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(extract_recipes.main(), 1)
            for name, content in previous.items():
                self.assertEqual((root / name).read_text(), content)


if __name__ == "__main__":
    unittest.main()
