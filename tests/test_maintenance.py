"""CLI composition tests. Registry/filesystem transactions have real tests separately."""
import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from lume.maintenance import main


class MaintenanceCompositionTests(unittest.TestCase):
    def test_browser_preview_never_applies_and_apply_uses_the_same_plan(self):
        plan = SimpleNamespace(to_dict=lambda: {'changed': True})
        receipt = SimpleNamespace(to_dict=lambda: {'status': 'applied'})
        args = ['--browser-setup', '--host-executable', 'chosen.exe']
        with patch('lume.registration.plan_registration', return_value=plan) as make, \
             patch('lume.registration.apply_registration', return_value=receipt) as apply, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(args), 0)
            apply.assert_not_called()
            self.assertTrue(json.loads(output.getvalue())['changed'])
            self.assertEqual(make.call_args.args[0], Path('chosen.exe'))
            self.assertEqual(main(args + ['--apply']), 0)
            apply.assert_called_once_with(plan)

    def test_report_is_complete_json_and_stdout_is_bounded_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            report=Path(folder)/'result.json'
            receipt=SimpleNamespace(to_dict=lambda:{'status':'installed','directory':'C:/ação', 'files':['x']*2000})
            with patch('lume.deployment.plan_install', return_value='plan'), \
                 patch('lume.deployment.install', return_value=receipt), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                code=main(['--install-local','--source-directory','source','--destination','chosen',
                           '--uninstaller','Uninstall.exe','--apply','--report',str(report)])
            self.assertEqual(code,0)
            self.assertEqual(len(json.loads(report.read_text(encoding='utf-8'))['files']),2000)
            self.assertNotIn('files',json.loads(output.getvalue()))
            self.assertLess(len(output.getvalue()),1024)

    def test_failed_plan_is_reported_without_mutation(self):
        with patch('lume.deployment.plan_install', side_effect=ValueError('Destino ocupado')), \
             patch('lume.deployment.install') as apply,contextlib.redirect_stdout(io.StringIO()) as output:
            code=main(['--install-local','--source-directory','source','--destination','chosen','--uninstaller','u.exe','--apply'])
            self.assertEqual(code,1)
            self.assertFalse(json.loads(output.getvalue())['ok'])
            apply.assert_not_called()

    def test_uninstall_requires_explicit_apply(self):
        with patch('lume.deployment.uninstall') as remove,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--uninstall-local','--install-directory','chosen']),1)
            remove.assert_not_called()

    def test_pending_uninstall_is_failure_and_preserves_recovery_receipt(self):
        facts = {'status': 'recovery_pending', 'retained_host': True, 'leftovers': ['host.exe']}
        with patch('lume.deployment.uninstall', return_value=SimpleNamespace(to_dict=lambda: facts)), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--uninstall-local', '--install-directory', 'chosen', '--apply']), 1)
            value = json.loads(output.getvalue())
            self.assertFalse(value['ok'])
            self.assertEqual(value['leftovers'], ['host.exe'])
            self.assertTrue(value['retained_host'])

    def test_install_failure_retains_structured_recovery(self):
        from lume.deployment import DeploymentError
        error = DeploymentError('Rollback pendente', recovery={'retained_host': True, 'receipt': 'recovery.json'})
        with patch('lume.deployment.plan_install', side_effect=error), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--install-local', '--source-directory', 's', '--destination', 'd', '--uninstaller', 'u']), 1)
            self.assertEqual(json.loads(output.getvalue())['recovery'], error.recovery)

