import tempfile
import unittest
from pathlib import Path

from source_names import build_source_names


class SourceNamesTest(unittest.TestCase):
    def test_repeated_period_numbers_and_new_files_do_not_change_existing_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "Donem_1" / "Liste_1.pdf"
            second = root / "Donem_2" / "Liste_1.pdf"
            for path, content in [(first, b"first period"), (second, b"second period")]:
                path.parent.mkdir()
                path.write_bytes(content)
            registry = root / ".source_registry.local.json"
            initial = build_source_names([first, second], registry)
            self.assertNotEqual(initial[first], initial[second])
            newer = root / "0_new_1.pdf"
            newer.write_bytes(b"new PDF sorted before older PDFs")
            updated = build_source_names(sorted([newer, first, second]), registry)
            self.assertEqual(initial[first], updated[first])
            self.assertEqual(initial[second], updated[second])
            self.assertEqual(len(set(updated.values())), 3)
            moved = root / "renamed.pdf"
            first.rename(moved)
            after_move = build_source_names([moved, second, newer], registry)
            self.assertEqual(after_move[moved], initial[first])
            copy = root / "copy.pdf"
            copy.write_bytes(second.read_bytes())
            self.assertEqual(build_source_names([copy, second], registry)[copy], initial[second])


if __name__ == "__main__":
    unittest.main()
