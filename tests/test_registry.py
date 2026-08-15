from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from loracast.ingest.registry import load_registry

REPO_REGISTRY = Path(__file__).resolve().parent.parent / "configs" / "registry.toml"


class RegistryTests(unittest.TestCase):
    def test_loads_shipped_registry(self) -> None:
        config = load_registry(REPO_REGISTRY)
        slugs = [source["slug"] for source in config["sources"]]
        self.assertEqual(slugs, ["planet-money", "the-indicator"])
        for source in config["sources"]:
            self.assertEqual(source["adapter"], "rss_html")
            self.assertTrue(source["feed_url"].startswith("https://"))

    def test_rejects_source_missing_required_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.toml"
            path.write_text('[[sources]]\nslug = "x"\nname = "X"\n')
            with self.assertRaises(ValueError):
                load_registry(path)


if __name__ == "__main__":
    unittest.main()
