import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ConfigExampleTests(unittest.TestCase):
    def test_example_is_valid_toml_and_contains_all_sections(self):
        with (ROOT / "tests/fixtures/config.r0.example.toml").open("rb") as file:
            config = tomllib.load(file)
        from maibot_autonomous_planning_plugin.config_models import MaiVitaConfig
        model = MaiVitaConfig.model_validate(config)
        config = {"plugin": config["plugin"], "schedule": model.schedule.model_dump(),
                  "inject": model.inject.model_dump(), "autonomous_planning": model.autonomous_planning.model_dump()}
        self.assertEqual(
            set(config),
            {"plugin", "autonomous_planning", "schedule", "inject"},
        )
        self.assertEqual(config["plugin"]["config_version"], "1")
        self.assertEqual(config["schedule"]["sleep_start_time"], "01:00")
        self.assertEqual(config["schedule"]["sleep_end_time"], "08:00")
        self.assertEqual(config["schedule"]["generation_timeout"], 180.0)
        self.assertEqual(config["schedule"]["generation_max_retries"], 1)
        self.assertEqual(config["schedule"]["generation_retry_backoff"], 30.0)
        self.assertEqual(
            config["schedule"]["model"],
            {"mode": "task", "task": "replyer", "model": ""},
        )
        self.assertNotIn("llm_task_name", config["schedule"])
        self.assertNotIn("day_start_time", config["schedule"])
        self.assertNotIn("sync_sleep_frequency", config["schedule"])
        self.assertNotIn("sleep_talk_adjust", config["schedule"])
        self.assertNotIn("enable_frequency_modulation", config["schedule"])
        self.assertNotIn("inject_mode", config["inject"])
        self.assertEqual(config["schedule"]["frequency"]["sleep_factor"], 0.0)
        self.assertEqual(
            config["schedule"]["frequency"]["activity_factors"]["entertainment"],
            1.5,
        )


if __name__ == "__main__":
    unittest.main()
