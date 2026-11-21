import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

path = Path(__file__).parents[1] / '.github/skills/linux-inventory/scripts/collect.py'
spec = importlib.util.spec_from_file_location('inventory_collector', path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class InventoryTests(unittest.TestCase):
    def test_empty_uname_is_partial(self):
        def command(args):
            return {'command': args, 'status': 'complete',
                    'values': [] if args[0] == 'uname' else ['sample']}
        with patch.object(mod, 'os_release', return_value={'ID': 'ubuntu', 'ID_LIKE': None, 'VERSION_ID': '24.04', 'PRETTY_NAME': 'Ubuntu'}), \
             patch.object(mod, 'read_command', side_effect=command), \
             patch.object(mod, 'environment', return_value={'kind': 'host_or_vm'}):
            data = mod.collect()
        self.assertEqual(data['collection_status'], 'partial')
        self.assertEqual(data['kernel_observation']['status'], 'unassessed')
        self.assertEqual(data['kernel_observation']['error'], 'empty kernel release')
