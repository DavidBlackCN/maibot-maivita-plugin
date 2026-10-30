import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ConfigExampleTests(unittest.TestCase):
    def test_example_is_valid_toml_and_contains_all_sections(self):
        import r0_support
        from maibot_autonomous_planning_plugin.maivita.host.integration_config import IntegrationConfig
        from maibot_autonomous_planning_plugin.maivita.host.config import DevelopmentConfig
        with (ROOT / 'config.example.toml').open('rb') as file:
            config = tomllib.load(file)
        model = IntegrationConfig.model_validate(config)
        self.assertEqual(set(config), {'plugin', 'instance', 'model', 'routine'})
        self.assertEqual(config['plugin']['config_version'], '4')
        self.assertEqual(model, IntegrationConfig())
        self.assertIsInstance(model.selected_config(), DevelopmentConfig)
        self.assertEqual(model.selected_config().routine, model.routine)


    def test_unknown_fields_still_fail_with_retired_sections(self):
        import r0_support
        from pydantic import ValidationError
        from maibot_autonomous_planning_plugin.maivita.host.integration_config import IntegrationConfig
        for unknown, location in (({'engnie': {}}, ('engnie',)),
                                  ({'model': {'unknown_field': 1}}, ('model', 'unknown_field')),
                                  ({'routine': {'typo': 1}}, ('routine', 'typo'))):
            with self.subTest(location=location):
                with self.assertRaises(ValidationError) as caught:
                    IntegrationConfig.model_validate({'engine': {}, 'legacy': {}, **unknown})
                self.assertIn((location, 'extra_forbidden'),
                              [(e['loc'], e['type']) for e in caught.exception.errors()])





class ModelSettingsTests(unittest.TestCase):
    def test_defaults_and_example_match(self):
        import r0_support
        from maibot_autonomous_planning_plugin.maivita.host.config import ModelSettings
        from maibot_autonomous_planning_plugin.maivita.host.integration_config import IntegrationConfig
        defaults = ModelSettings().model_dump()
        self.assertEqual(defaults, dict(mode='task', task='planner', model='',
            timeout_seconds=90, max_tokens=8192, daily_budget=20,
            generate_on_start=False, daily_planning_enabled=True, daily_planning_time='08:00', life_context=''))
        with (ROOT / 'config.example.toml').open('rb') as stream:
            example = tomllib.load(stream)
        self.assertTrue(set(example['model']) <= set(defaults))
        self.assertEqual(IntegrationConfig.model_validate(example).model.model_dump(), defaults)

    def test_invalid_routes_and_limits(self):
        import r0_support
        from pydantic import ValidationError
        from maibot_autonomous_planning_plugin.maivita.host.config import ModelSettings
        for values in ({'mode':'model'}, {'mode':'model','model':'   '},
                       {'mode':'task','task':''}, {'task':'  '}, {'task':42},
                       {'mode':'unknown'}, {'timeout_seconds':301}, {'timeout_seconds':0},
                       {'max_tokens':16385}, {'max_tokens':127},
                       {'daily_budget':1001}, {'daily_budget':0}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                ModelSettings(**values)
        self.assertEqual(ModelSettings(mode='model', model='custom', timeout_seconds=300,
            max_tokens=16384, daily_budget=200).model, 'custom')
        self.assertEqual(ModelSettings(timeout_seconds=1, max_tokens=128, daily_budget=1).daily_budget, 1)


if __name__ == "__main__":
    unittest.main()
