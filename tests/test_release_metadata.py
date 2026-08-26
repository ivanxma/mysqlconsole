import json
import re
import tomllib
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


class ReleaseMetadataTests(unittest.TestCase):
    def test_release_versions_match(self):
        app_version = json.loads((ROOT_DIR / "appver.json").read_text(encoding="utf-8"))["version"]
        pyproject_version = tomllib.loads((ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        readme = (ROOT_DIR / "README.md").read_text(encoding="utf-8")
        readme_match = re.search(r"Current version: `([^`]+)`", readme)

        self.assertIsNotNone(readme_match, "README must declare the current release version.")
        self.assertEqual(app_version, pyproject_version)
        self.assertEqual(app_version, readme_match.group(1))


if __name__ == "__main__":
    unittest.main()
