"""Version ceilings never authorize untested Host/SDK combinations."""
import os
import unittest
from unittest.mock import patch
import r0_support
from maibot_autonomous_planning_plugin.maivita import runtime_versions as versions


CASES = [
    ('1.3.12','2.15.0',True), ('1.3.15','2.16.0',True),
    ('1.3.12','2.16.0',False), ('1.3.15','2.15.0',False),
    ('1.3.13','2.15.0',False), ('1.3.14','2.16.0',False),
    ('1.3.16','2.16.0',False), ('1.3.15','2.17.0',False),
    ('1.3.15','2.16.0rc1',False), (None,'2.16.0',False),
]


class RuntimeVersionTests(unittest.TestCase):
    def test_explicit_pairs_and_actual_descriptor(self):
        for host, sdk, allowed in CASES:
            with self.subTest(host=host,sdk=sdk), patch.dict(os.environ), patch.object(versions.importlib.metadata,'version',return_value=sdk):
                if host is None:
                    os.environ.pop('MAIBOT_HOST_VERSION',None)
                else:
                    os.environ['MAIBOT_HOST_VERSION']=host
                self.assertIs(versions.runtime_supported(),allowed)
                self.assertEqual(versions.runtime_descriptor(),{'host':host,'sdk':sdk})
