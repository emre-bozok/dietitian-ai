import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from recipe_agent import agent


def response(*vectors):
    return SimpleNamespace(embeddings=[SimpleNamespace(values=v) for v in vectors])


class RecipeSearchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.recipes_path = root / "recipes.json"
        self.index_path = root / "recipes_index.json"
        self.recipes = [
            {"name": "PIRASA BÖREĞİ", "category": ["sebze"], "ingredients": ["pırasa"],
             "instructions": "Pişir.", "metadata": {"source_pdf": "Diyet_Listesi_1.pdf"}},
            {"name": "Tavuk", "category": ["tavuk"], "ingredients": ["tavuk"],
             "instructions": "Pişir.", "metadata": {"source_pdf": "Diyet_Listesi_2.pdf"}},
        ]
        self.recipes_path.write_text(json.dumps(self.recipes))
        for key, value in [("RECIPES_PATH", self.recipes_path), ("INDEX_PATH", self.index_path),
                           ("_SEMANTIC_RETRY_AFTER", 0.0)]:
            patcher = patch.object(agent, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = MagicMock()
        patcher = patch.object(agent.genai, "Client", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_incomplete_legacy_index_is_rebuilt_with_separate_content_objects(self):
        self.index_path.write_text(json.dumps([self.recipes[0] | {"embedding": [1, 0]}, self.recipes[1]]))
        self.client.models.embed_content.return_value = response([1, 0], [0, 1])
        rows = agent.get_or_create_index()
        self.assertEqual(len(rows), 2)
        contents = self.client.models.embed_content.call_args.kwargs["contents"]
        self.assertEqual(len(contents), 2)
        self.assertTrue(all(isinstance(c, agent.types.Content) and len(c.parts) == 1 for c in contents))
        saved = json.loads(self.index_path.read_text())
        self.assertEqual(saved["model"], agent.EMBEDDING_MODEL)
        self.assertEqual(saved["dimension"], 2)
        self.assertEqual(len(saved["recipes"]), 2)
        agent.get_or_create_index()
        self.assertEqual(self.client.models.embed_content.call_count, 1)

    def test_aggregated_or_invalid_response_cannot_overwrite_old_index(self):
        for output in [response([1, 0]), response([1, 0], [0, 0]),
                       response([1, 0], [1, 0, 0]), response([1, 0], [float("nan"), 1])]:
            with self.subTest(output=output):
                previous = json.dumps(self.recipes)
                self.index_path.write_text(previous)
                self.client.models.embed_content.return_value = output
                with self.assertRaises(ValueError):
                    agent.get_or_create_index()
                self.assertEqual(self.index_path.read_text(), previous)

    def test_model_change_or_new_recipes_invalidates_cache(self):
        self.client.models.embed_content.return_value = response([1, 0], [0, 1])
        agent.get_or_create_index()
        data = json.loads(self.index_path.read_text())
        data["model"] = "other-model"
        self.index_path.write_text(json.dumps(data))
        agent.get_or_create_index()
        self.recipes[0]["ingredients"].append("yumurta")
        self.recipes_path.write_text(json.dumps(self.recipes))
        agent.get_or_create_index()
        self.assertEqual(self.client.models.embed_content.call_count, 3)

    def test_more_than_one_batch_preserves_every_recipe_vector(self):
        rows = [self.recipes[0] | {"name": f"Tarif {i}"} for i in range(108)]
        self.recipes_path.write_text(json.dumps(rows))
        self.client.models.embed_content.side_effect = [response(*([[1, 0]] * 20)) for _ in range(5)] + [
                                                        response(*([[0, 1]] * 8))]
        indexed = agent.get_or_create_index()
        self.assertEqual(len(indexed), 108)
        self.assertTrue(all("embedding" in row for row in indexed))
        self.assertEqual([len(call.kwargs["contents"]) for call in
                          self.client.models.embed_content.call_args_list], [20, 20, 20, 20, 20, 8])

    def test_quota_retry_waits_then_retries_same_batch(self):
        error = RuntimeError("quota")
        error.code = 429
        error.details = {"error": {"details": [{"retryDelay": "2s"}]}}
        self.client.models.embed_content.side_effect = [error, response([1, 0], [0, 1])]
        with patch.object(agent.time, "sleep") as sleep:
            rows = agent.get_or_create_index()
        self.assertEqual(len(rows), 2)
        sleep.assert_called_once_with(3.0)
        self.assertEqual(self.client.models.embed_content.call_count, 2)

    def test_quota_failure_keeps_local_search_and_sources_working(self):
        self.client.models.embed_content.side_effect = RuntimeError("429 quota")
        result = json.loads(agent.search_recipes("pırasa", 1))
        self.assertEqual(result[0]["name"], "PIRASA BÖREĞİ")
        self.assertEqual(result[0]["source_pdfs"], ["Diyet_Listesi_1.pdf"])
        self.assertEqual(result[0]["search_mode"], "keyword")
        agent.search_recipes("tavuk", 1)
        self.assertEqual(self.client.models.embed_content.call_count, 1)

    def test_query_dimension_mismatch_falls_back_without_crashing(self):
        self.client.models.embed_content.side_effect = [response([1, 0], [0, 1]), response([1, 0, 0])]
        result = json.loads(agent.search_recipes("pırasa", 1))
        self.assertEqual(result[0]["name"], "PIRASA BÖREĞİ")
        self.assertEqual(result[0]["search_mode"], "keyword")

    def test_hybrid_search_uses_validated_cache(self):
        self.client.models.embed_content.side_effect = [response([1, 0], [0, 1]), response([1, 0])]
        result = json.loads(agent.search_recipes("pırasa", 1))
        self.assertEqual(result[0]["name"], "PIRASA BÖREĞİ")
        self.assertEqual(result[0]["search_mode"], "hybrid")

    def test_empty_catalog_avoids_api(self):
        self.recipes_path.write_text("[]")
        self.assertEqual(agent.search_recipes("pırasa"), "[]")
        self.client.models.embed_content.assert_not_called()

    def test_both_ingredients_outrank_semantically_similar_partial_match(self):
        bread = self.recipes[0] | {"name": "Tatlı Tuzlu Ekmek", "ingredients": ["avokado", "hellim"]}
        salad = self.recipes[1] | {"name": "Hellim Salatası", "ingredients": ["hellim", "domates"]}
        self.recipes_path.write_text(json.dumps([bread, salad]))
        # Vektör sıralaması yanlış adayı öne koysa da tam malzeme eşleşmesi kazanmalı.
        self.client.models.embed_content.side_effect = [response([0, 1], [1, 0]), response([1, 0])]
        result = json.loads(agent.search_recipes("hellim ve avokado", 1))
        self.assertEqual(result[0]["name"], "Tatlı Tuzlu Ekmek")
        self.assertEqual(result[0]["matched_terms"], ["avokado", "hellim"])
        self.assertEqual(result[0]["search_mode"], "keyword")
        self.client.models.embed_content.assert_not_called()

    def test_same_name_with_different_portions_remains_separate(self):
        old = self.recipes[0] | {"name": "Tatlı Tuzlu Ekmek", "ingredients": ["1 dilim ekmek", "hellim", "avokado"]}
        new = old | {"ingredients": ["2 dilim ekmek", "hellim", "avokado"], "metadata": {"source_pdf": "new.pdf"}}
        self.recipes_path.write_text(json.dumps([old, new]))
        self.assertEqual(len(agent._recipe_catalog()), 2)
        variants = json.loads(agent.get_recipe("Tatlı Tuzlu Ekmek"))
        self.assertEqual(len(variants["variants"]), 2)
        chosen = json.loads(agent.get_recipe("Tatlı Tuzlu Ekmek", "new.pdf"))
        self.assertEqual(chosen["ingredients"][0], "2 dilim ekmek")


if __name__ == "__main__":
    unittest.main()
