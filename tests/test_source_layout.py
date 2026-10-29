"""Shared image resources still resolve to the distribution root."""
import unittest
from r0_support import ROOT
from maibot_autonomous_planning_plugin.maivita.shared.utils.schedule_image_generator import ScheduleImageGenerator


class SourceLayoutTests(unittest.TestCase):
    def test_plugin_and_image_resources_still_resolve_to_distribution_root(self):
        self.assertEqual(ScheduleImageGenerator.PLUGIN_ROOT, ROOT)
        self.assertTrue(ScheduleImageGenerator.LOGO_IMAGE_PATH.is_file())
