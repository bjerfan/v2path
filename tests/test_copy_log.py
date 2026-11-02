import unittest
from unittest.mock import Mock

import secondnet_gui as ui


class CopyLogTests(unittest.TestCase):
    def test_copy_preserves_multiline_log_and_does_not_modify_rows(self):
        owner = Mock()
        owner.log_rows.rows = [{"lineText": "first"}, {"lineText": "error\n  detail"}]
        ui.Controller._copy_log(owner)
        owner.application.clipboard.return_value.setText.assert_called_once_with("first\nerror\n  detail")
        self.assertEqual(len(owner.log_rows.rows), 2)

    def test_empty_log_does_not_replace_clipboard(self):
        owner = Mock()
        owner.log_rows.rows = []
        ui.Controller._copy_log(owner)
        owner.application.clipboard.assert_not_called()
