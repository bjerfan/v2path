import queue
import unittest
from unittest.mock import Mock, patch

import secondnet_gui as ui


class LogPerformanceTests(unittest.TestCase):
    def test_log_model_keeps_only_latest_300_lines(self):
        rows = ui.Rows(["lineText"], None, max_rows=ui.MAX_LOG_ROWS)
        rows.append([f"line {i}" for i in range(500)])
        self.assertEqual(rows.rowCount(), 300)
        self.assertEqual(rows.rows[0]["lineText"], "line 200")
        rows.append(["new line"])
        self.assertEqual(rows.rowCount(), 300)
        self.assertEqual(rows.rows[0]["lineText"], "line 201")
        self.assertEqual(rows.rows[-1]["lineText"], "new line")

    def test_event_logs_flush_at_most_four_times_per_second(self):
        owner = Mock()
        owner.events = queue.Queue()
        owner._pending_logs = []
        owner._next_log_flush = 0.0
        with patch.object(ui.time, "monotonic", side_effect=[10.0, 10.1, 10.25]):
            for line in ("first", "second", "third"):
                owner.events.put(("log", line))
                ui.Controller._poll_events(owner)
        self.assertEqual(owner._append_logs.call_count, 2)
        self.assertEqual(owner._append_logs.call_args_list[0].args[0], ["first"])
        self.assertEqual(owner._append_logs.call_args_list[1].args[0], ["second", "third"])

    def test_clear_discards_not_yet_displayed_lines(self):
        owner = Mock()
        owner._pending_logs = ["old line"]
        ui.Controller._clear_log(owner)
        self.assertEqual(owner._pending_logs, [])
        self.assertEqual(owner.log_rows.rows, [])
