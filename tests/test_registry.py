from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from loracast.ingest.registry import (
    default_registry_text,
    load_registry,
    user_registry_path,
)

SECOND_REGISTRY = """
[[sources]]
slug = "other-show"
name = "Other Show"
adapter = "rss_html"
feed_url = "https://example.org/feed.xml"
"""


class RegistryTests(unittest.TestCase):
    def test_loads_packaged_default_registry(self) -> None:
        # Empty data root, so nothing shadows the packaged default.
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"LORACAST_DATA": tmp}):
                config = load_registry()
        slugs = [source["slug"] for source in config["sources"]]
        self.assertEqual(slugs, ["planet-money", "the-indicator"])
        for source in config["sources"]:
            self.assertEqual(source["adapter"], "rss_html")
            self.assertTrue(source["feed_url"].startswith("https://"))

    def test_default_registry_text_is_readable_toml(self) -> None:
        self.assertIn("[[sources]]", default_registry_text())

    def test_explicit_path_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.toml"
            path.write_text(SECOND_REGISTRY)
            config = load_registry(path)
            self.assertEqual(
                [source["slug"] for source in config["sources"]], ["other-show"]
            )

    def test_missing_explicit_path_raises_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.toml"
            with self.assertRaises(FileNotFoundError) as ctx:
                load_registry(missing)
            message = str(ctx.exception)
            self.assertIn(str(missing), message)
            self.assertIn("init-registry", message)

    def test_user_registry_overrides_packaged_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"LORACAST_DATA": tmp}):
                self.assertEqual(user_registry_path(), Path(tmp) / "registry.toml")
                self.assertEqual(
                    [s["slug"] for s in load_registry()["sources"]],
                    ["planet-money", "the-indicator"],
                )
                (Path(tmp) / "registry.toml").write_text(SECOND_REGISTRY)
                self.assertEqual(
                    [s["slug"] for s in load_registry()["sources"]], ["other-show"]
                )

    def test_rejects_source_missing_required_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.toml"
            path.write_text('[[sources]]\nslug = "x"\nname = "X"\n')
            with self.assertRaises(ValueError):
                load_registry(path)


if __name__ == "__main__":
    unittest.main()
