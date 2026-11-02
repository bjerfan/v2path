import json
import unittest
from unittest.mock import patch

import update_check


class UpdateCheckTests(unittest.TestCase):
    def test_newer_and_same_release(self):
        with patch.object(update_check.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = json.dumps({"tag_name": "v3.2.8", "html_url": "https://github.com/bjerfan/v2path/releases/tag/v3.2.8"})
            self.assertEqual(update_check.check("3.2.7"), (True, "https://github.com/bjerfan/v2path/releases/tag/v3.2.8"))
            self.assertEqual(update_check.check("3.2.8")[0], False)

    def test_reject_untrusted_link(self):
        with patch.object(update_check.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = json.dumps({"tag_name": "v3.2.8", "html_url": "https://evil.example/v3.2.8"})
            with self.assertRaisesRegex(RuntimeError, "unexpected release link"):
                update_check.check("3.2.7")
