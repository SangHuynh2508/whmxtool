import unittest

import whmx_story as s


class LineExtraTest(unittest.TestCase):
    def test_unlock_kept_when_present(self):
        self.assertEqual(s.line_extra("感应达到投契解锁", {}, "touchLine_4"), {"unlock": "感应达到投契解锁"})

    def test_no_unlock_no_zone(self):
        self.assertEqual(s.line_extra("", {"TimeLine_1_zone": "6,0,18,0"}, "regardsLine"), {})

    def test_time_window(self):
        row = {"TimeLine_1_zone": "6,0,18,0", "TimeLine_2_zone": "18,0,23,59"}
        self.assertEqual(s.line_extra("", row, "timeLine_1"), {"zone": "6,0,18,0"})
        self.assertEqual(s.line_extra("", row, "timeLine_2"), {"zone": "18,0,23,59"})

    def test_missing_skin_row(self):
        self.assertEqual(s.line_extra("", None, "timeLine_1"), {})


if __name__ == "__main__":
    unittest.main()
