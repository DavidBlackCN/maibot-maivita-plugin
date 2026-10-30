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

    def test_retired_sections_are_discarded_without_mutating_input(self):
        import copy
        import r0_support
        from maibot_autonomous_planning_plugin.maivita.host.integration_config import IntegrationConfig
        with (ROOT / 'config.example.toml').open('rb') as stream:
            clean = tomllib.load(stream)
        for retired in ({'engine': {'mode': 'life'}}, {'engine': {'mode': 'legacy'}},
                        {'legacy': {}}, tomllib.loads('''
[engine]
mode = "legacy"
[legacy.model]
secret = "do-not-log-this"
[legacy.planner.nested]
enabled = true
''')):
            with self.subTest(sections=list(retired)):
                source = {**clean, **retired}
                original = copy.deepcopy(source)
                model = IntegrationConfig.model_validate(source)
                self.assertEqual(source, original)
                self.assertEqual(model, IntegrationConfig.model_validate(clean))
                self.assertEqual(model.selected_config(), IntegrationConfig.model_validate(clean).selected_config())
                self.assertNotIn('engine', IntegrationConfig.model_fields)
                self.assertNotIn('legacy', IntegrationConfig.model_fields)

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

    def test_startup_warns_once_and_never_logs_values(self):
        import r0_support
        from unittest.mock import patch
        from maibot_autonomous_planning_plugin.maivita.host import integration_config as module
        from maibot_autonomous_planning_plugin.maivita.host.entrypoint import MaiVitaPlugin
        for retired in ({'engine': {'mode': 'secret-mode'}},
                        {'legacy': {'secret': 'do-not-log-this'}},
                        {'engine': {}, 'legacy': {'secret': 'do-not-log-this'}}):
            with self.subTest(sections=list(retired)), patch.object(module, '_retired_warning_logged', False):
                plugin = MaiVitaPlugin()
                with self.assertLogs(module.logger, level='WARNING') as logs:
                    plugin.set_plugin_config({'plugin': {'config_version': '3'}, **retired})
                self.assertEqual(len(logs.records), 1)
                message = logs.records[0].getMessage()
                for name in retired:
                    self.assertIn(f'[{name}]', message)
                self.assertIn('config.example.toml', message)
                self.assertNotIn('secret', message)
                with self.assertNoLogs(module.logger, level='WARNING'):
                    plugin.set_plugin_config({'plugin': {'config_version': '3'}, **retired})
                    module.IntegrationConfig.model_validate(retired)
                self.assertEqual(set(plugin.config.model_dump()), {'plugin', 'instance', 'model', 'routine', 'scheduler', 'availability'})

    def test_clean_config_does_not_warn(self):
        import r0_support
        from unittest.mock import patch
        from maibot_autonomous_planning_plugin.maivita.host import integration_config as module
        with patch.object(module, '_retired_warning_logged', False):
            with self.assertNoLogs(module.logger, level='WARNING'):
                module.IntegrationConfig.model_validate({})
            self.assertFalse(module._retired_warning_logged)

    def test_retired_cli_entrypoints_fail_without_touching_input(self):
        import subprocess
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            work=Path(temporary)
            source=work/'config.toml'; source.write_text('sentinel')
            output=work/'output.toml'
            for name in ('convert_config.py','switch_engine.py'):
                result=subprocess.run([sys.executable,str(ROOT/'tools'/name),str(source),str(output)],
                                      cwd=work,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,2,result.stderr)
                self.assertIn('retired',result.stderr)
                self.assertNotIn('Traceback',result.stderr)
                self.assertEqual(source.read_text(),'sentinel')
                self.assertFalse(output.exists())


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
