import unittest
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("maibot_autonomous_planning_plugin")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("maibot_autonomous_planning_plugin", package)

from maibot_autonomous_planning_plugin.maivita.shared.utils.time_utils import TimeParseError, TimeRange, parse_hhmm


class TimeConstraintTests(unittest.TestCase):
    def test_sleep_windows(self):
        for start, end, duration in [
            ("01:00", "08:00", 420), ("23:00", "07:00", 480),
            ("00:00", "08:00", 480), ("23:30", "24:00", 30),
        ]:
            with self.subTest(start=start, end=end):
                self.assertEqual(TimeRange.from_strings(start, end).duration_minutes, duration)

    def test_invalid_time_never_falls_back_to_midnight(self):
        for value in ["24:30", "25:00", "12:99", "abc", ""]:
            with self.subTest(value=value), self.assertRaises(TimeParseError):
                parse_hhmm(value, allow_24=True)

    def test_cross_midnight_overlap_detects_early_morning(self):
        self.assertTrue(TimeRange.from_strings("23:00", "07:00").overlaps(
            TimeRange.from_strings("01:00", "02:00")
        ))


if __name__ == "__main__":
    unittest.main()
