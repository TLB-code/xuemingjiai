import json
import tempfile
import unittest
from pathlib import Path

from apply_reader_overrides import apply_account


class ReaderOverridesTests(unittest.TestCase):
    def test_applies_profile_and_generated_data_overrides(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            account = Path(temp_dir) / "accounts" / "example"
            snapshots = account / "wayback_snapshots"
            json_dir = snapshots / "json"
            json_dir.mkdir(parents=True)
            (account / "reader_overrides.json").write_text(
                json.dumps(
                    {
                        "profile": {"name": "Correct Name"},
                        "replace_exact": {"PLACEHOLDER": "Correct Name"},
                    }
                ),
                encoding="utf-8",
            )
            (snapshots / "profile.json").write_text(
                json.dumps({"name": "PLACEHOLDER", "username": "@example"}),
                encoding="utf-8",
            )
            (snapshots / "index.json").write_text(
                json.dumps([{"author_name": "PLACEHOLDER"}]), encoding="utf-8"
            )
            tweet_path = json_dir / "tweet.json"
            tweet_path.write_text(
                json.dumps({"includes": {"users": [{"name": "PLACEHOLDER"}]}}),
                encoding="utf-8",
            )

            changed = apply_account(account)

            self.assertEqual(len(changed), 3)
            self.assertEqual(
                json.loads((snapshots / "profile.json").read_text())["name"],
                "Correct Name",
            )
            self.assertEqual(
                json.loads((snapshots / "index.json").read_text())[0]["author_name"],
                "Correct Name",
            )
            self.assertEqual(
                json.loads(tweet_path.read_text())["includes"]["users"][0]["name"],
                "Correct Name",
            )
            self.assertEqual(apply_account(account, check=True), [])


if __name__ == "__main__":
    unittest.main()
