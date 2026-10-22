"""Resource and granted-data paths remain correct after package relocation."""
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import patch

from r0_support import ROOT
from maibot_autonomous_planning_plugin.maivita.legacy.plugin import MaiVitaPlugin
from maibot_autonomous_planning_plugin.maivita.legacy.planner.auto_scheduler import ScheduleAutoScheduler
from maibot_autonomous_planning_plugin.maivita.legacy.planner import pinned_model_adapter
from maibot_autonomous_planning_plugin.maivita.shared.utils.schedule_image_generator import ScheduleImageGenerator


class SourceLayoutTests(unittest.TestCase):
    def test_plugin_and_image_resources_still_resolve_to_distribution_root(self):
        self.assertEqual(MaiVitaPlugin()._plugin_root, ROOT)
        self.assertEqual(ScheduleImageGenerator.PLUGIN_ROOT, ROOT)
        self.assertTrue(ScheduleImageGenerator.LOGO_IMAGE_PATH.is_file())

    def test_legacy_model_config_fallback_resolves_same_synthetic_instance(self):
        with tempfile.TemporaryDirectory(prefix='maivita-layout-host-') as temporary:
            instance=Path(temporary).resolve()
            config=instance/'config/model_config.toml'
            config.parent.mkdir(); config.write_text('# synthetic fixture, no credentials\n')
            plugin_root=instance/'plugins/maibot-maivita-plugin'
            source=plugin_root/'maivita/legacy/planner/pinned_model_adapter.py'
            with patch.object(pinned_model_adapter,'__file__',str(source)):
                self.assertEqual(pinned_model_adapter._find_model_config_path(None),config)
                self.assertEqual(pinned_model_adapter._find_model_config_path(plugin_root),config)

    def test_legacy_derived_cache_uses_granted_data_and_refuses_unloaded_plugin(self):
        config=NS(schedule=NS(timezone='Asia/Shanghai'))
        with self.assertRaisesRegex(RuntimeError,'loaded data directory'):
            ScheduleAutoScheduler(NS(config=config,_data_dir=None))
        with tempfile.TemporaryDirectory(prefix='maivita-layout-data-') as temporary:
            data=Path(temporary).resolve()
            scheduler=ScheduleAutoScheduler(NS(config=config,_data_dir=data))
            self.assertEqual(scheduler._inferred_prompt_file,data/'next_day_prompt.json')
            # Construction reads a cache if present, but creates no source DB/data.
            self.assertEqual(list(data.iterdir()),[])
            self.assertFalse((ROOT/'maivita/legacy/data').exists())
