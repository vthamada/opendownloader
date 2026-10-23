"""Native registration owner exercised without any active browser registration."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from lume.registration import (HOST_NAME, OWNER, MANIFEST_NAME, OWNER_NAME, RegistrationError,
    RegistryState, RegistryTarget, WindowsRegistry, apply_registration, plan_registration,
    rollback_registration, unregister_owned, discover_owned_manifest_directory)

EXTENSION = 'abcdefghijklmnopabcdefghijklmnop'
SECOND = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'

class FakeRegistry:
    def __init__(self):
        self.states={}
        self.writes=0
        self.fail_at=None
        self.after_write=None
    def targets(self):
        return tuple(RegistryTarget(browser,view,'PrivateTest/'+browser+str(view)) for browser in ('Chrome','Edge') for view in (32,64))
    def read(self,target):return self.states.get(target,RegistryState())
    def write(self,target,value,kind=1):
        self.writes+=1
        old=self.read(target)
        self.states[target]=RegistryState(True,True,value,kind,old.other_values,old.subkeys)
        if self.after_write:self.after_write(target)
        if self.writes==self.fail_at:raise OSError('injected uncertain write failure')
    def delete_default(self,target):
        state=self.read(target)
        self.states[target]=replace(state,default_exists=False,value=None,kind=None)
    def delete_empty_key(self,target):
        state=self.read(target)
        if not state.default_exists and not state.other_values and not state.subkeys:self.states.pop(target,None)

class RegistrationTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]/'.cache'/'registration-tests'
        root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root)
        self.root=Path(self.temp.name)
        self.host=self.root/'app'/'LumeNativeHost.exe'
        self.host.parent.mkdir()
        self.host.write_bytes(b'local fixture executable; never run')
        self.backend=FakeRegistry()
    def tearDown(self):self.temp.cleanup()
    def plan(self, extension=EXTENSION, **values):
        return plan_registration(self.host,extension,_backend=self.backend,**values)
    def test_plan_is_read_only_and_install_is_idempotent_for_all_four_views(self):
        plan=self.plan()
        self.assertTrue(plan.changed)
        self.assertFalse(plan.manifest.parent.exists())
        self.assertEqual(self.backend.writes,0)
        receipt=apply_registration(plan)
        self.assertTrue(receipt.applied)
        self.assertTrue(receipt.backup_directory.is_dir())
        self.assertEqual(len(self.backend.states),4)
        self.assertTrue(all(state.value==str(plan.manifest) for state in self.backend.states.values()))
        proof=json.loads(plan.owner_file.read_text(encoding='utf-8'))
        self.assertEqual(proof['owner'],OWNER)
        self.assertEqual(proof['sha256'],hashlib.sha256(plan.manifest.read_bytes()).hexdigest())
        again=self.plan()
        self.assertFalse(again.changed)
        self.assertEqual(apply_registration(again).status,'unchanged')
        self.assertEqual(self.backend.writes,4)
        self.assertIsNone(again.backup_directory)

    def test_both_store_channels_keep_local_extension_authorized_and_are_idempotent(self):
        edge_id='cccccccccccccccccccccccccccccccc'
        plan=self.plan(additional_extension_ids=(SECOND,edge_id))
        apply_registration(plan)
        manifest=json.loads(plan.manifest.read_text())
        expected={f'chrome-extension://{value}/' for value in (EXTENSION,SECOND,edge_id)}
        self.assertEqual(set(manifest['allowed_origins']),expected)
        again=self.plan(additional_extension_ids=(edge_id,SECOND))
        self.assertFalse(again.changed)
        self.assertEqual(again.extension_id,EXTENSION)
        self.assertEqual(set(again.allowed_origins),expected)

    def test_malformed_additional_store_ids_fail_before_any_mutation(self):
        for value in (None,'a'*32,('A'*32,),('chrome-extension://'+SECOND+'/',),(True,)):
            with self.subTest(value=value),self.assertRaises(RegistrationError):
                self.plan(additional_extension_ids=value)
        self.assertEqual(self.backend.writes,0)
        self.assertFalse((self.host.parent/'BrowserHost').exists())
    def test_legacy_owner_preserves_origins_and_migration_requires_selected_proof(self):
        first=self.plan()
        apply_registration(first)
        old_manifest=json.loads(first.manifest.read_text())
        # Legacy PowerShell formatting/order stays valid by its exact digest.
        text=json.dumps(old_manifest,indent=4).encode()+b'\r\n'
        first.manifest.write_bytes(text)
        first.owner_file.write_text(json.dumps({'owner':OWNER,'manifest':str(first.manifest),'sha256':hashlib.sha256(text).hexdigest()}))
        target=self.root/'new location'/'BrowserHost'
        with self.assertRaises(RegistrationError):self.plan(SECOND,manifest_directory=target)
        migrated=self.plan(SECOND,manifest_directory=target,previous_manifest_directory=first.manifest.parent)
        self.assertEqual(len(migrated.allowed_origins),2)
        apply_registration(migrated)
        self.assertTrue(first.manifest.exists())
        self.assertTrue(all(state.value==str(migrated.manifest) for state in self.backend.states.values()))
    def test_foreign_default_in_any_view_refuses_before_writing(self):
        target=self.backend.targets()[-1]
        self.backend.states[target]=RegistryState(True,True,'C:/foreign.json',1)
        with self.assertRaises(RegistrationError):self.plan()
        self.assertEqual(self.backend.writes,0)
        self.assertFalse((self.host.parent/'BrowserHost').exists())
    def test_incomplete_or_modified_owner_is_preserved(self):
        plan=self.plan()
        plan.manifest.parent.mkdir()
        plan.manifest.write_text('foreign')
        with self.assertRaises(RegistrationError):self.plan()
        self.assertEqual(plan.manifest.read_text(),'foreign')
        self.assertEqual(self.backend.writes,0)
    def test_stale_plan_does_not_overwrite_new_registration(self):
        plan=self.plan()
        target=self.backend.targets()[1]
        foreign=RegistryState(True,True,'C:/new-owner.json',1)
        self.backend.states[target]=foreign
        with self.assertRaises(RegistrationError):apply_registration(plan)
        self.assertEqual(self.backend.read(target),foreign)
        self.assertFalse(plan.manifest.parent.exists())
    def test_uncertain_registry_write_failure_rolls_back_written_files_and_values(self):
        plan=self.plan()
        self.backend.fail_at=2
        with self.assertRaises(RegistrationError) as error:apply_registration(plan)
        self.assertEqual(error.exception.receipt.rollback_pending,())
        self.assertFalse(plan.manifest.exists())
        self.assertFalse(plan.owner_file.exists())
        self.assertTrue(all(not self.backend.read(target).default_exists for target in self.backend.targets()))
        self.assertTrue(plan.backup_directory.exists())
    def test_rollback_never_overwrites_concurrent_foreign_change(self):
        plan=self.plan()
        receipt=apply_registration(plan)
        target=self.backend.targets()[0]
        foreign=RegistryState(True,True,'C:/other-later.json',1)
        self.backend.states[target]=foreign
        rollback=rollback_registration(receipt)
        self.assertEqual(rollback.status,'partial')
        self.assertIn('Chrome:32',rollback.rollback_pending)
        self.assertEqual(self.backend.read(target),foreign)
        self.assertFalse(plan.manifest.exists())
    def test_rollback_preserves_concurrently_modified_manifest(self):
        plan=self.plan()
        receipt=apply_registration(plan)
        plan.manifest.write_bytes(b'concurrent content')
        rollback=rollback_registration(receipt)
        self.assertIn(str(plan.manifest),rollback.rollback_pending)
        self.assertEqual(plan.manifest.read_bytes(),b'concurrent content')
    def test_unregister_removes_only_owned_defaults_and_preserves_other_values(self):
        plan=self.plan()
        apply_registration(plan)
        target=self.backend.targets()[0]
        state=self.backend.read(target)
        self.backend.states[target]=replace(state,other_values=(('user_note','keep',1),))
        foreign_target=self.backend.targets()[1]
        foreign=RegistryState(True,True,'C:/foreign-host.json',1)
        self.backend.states[foreign_target]=foreign
        user=self.host.parent/'BrowserHost'/'user.txt'
        user.write_text('keep')
        receipt=unregister_owned(self.host,plan.manifest.parent,_backend=self.backend)
        self.assertEqual(receipt.status,'removed')
        self.assertFalse(plan.manifest.exists())
        self.assertEqual(user.read_text(),'keep')
        self.assertEqual(self.backend.read(foreign_target),foreign)
        self.assertEqual(self.backend.read(target).other_values,(('user_note','keep',1),))
        self.assertFalse(self.backend.read(target).default_exists)
    def test_wrong_executable_cannot_unregister_and_receipt_is_serializable(self):
        plan=self.plan()
        receipt=apply_registration(plan)
        json.dumps(plan.to_dict())
        encoded=json.dumps(receipt.to_dict())
        self.assertIn('rollback',encoded)
        with self.assertRaises(RegistrationError):
            unregister_owned(self.root/'wrong.exe',plan.manifest.parent,_backend=self.backend)
        self.assertTrue(plan.manifest.exists())
    def test_future_host_is_verified_at_apply_against_staged_source(self):
        future=self.root/'installed'/'app'/'LumeNativeHost.exe'
        plan=plan_registration(future,EXTENSION,self.root/'installed'/'BrowserHost',host_source=self.host,_backend=self.backend)
        self.assertFalse(future.parent.exists())
        with self.assertRaises(RegistrationError):apply_registration(plan)
        future.parent.mkdir(parents=True)
        future.write_bytes(b'changed staged candidate')
        with self.assertRaises(RegistrationError):apply_registration(plan)
        self.assertFalse(plan.manifest.parent.exists())
        future.write_bytes(self.host.read_bytes())
        self.assertTrue(apply_registration(plan).applied)

    def test_discovery_requires_unanimous_owned_manifest_independently_of_old_host(self):
        self.assertIsNone(discover_owned_manifest_directory(_backend=self.backend))
        plan=self.plan()
        apply_registration(plan)
        self.assertEqual(discover_owned_manifest_directory(_backend=self.backend),plan.manifest.parent)
        target=self.backend.targets()[-1]
        self.backend.states[target]=RegistryState(True,True,str(self.root/'foreign.json'),1)
        with self.assertRaises(RegistrationError):discover_owned_manifest_directory(_backend=self.backend)
        self.backend.states[target]=RegistryState(True,True,str(plan.manifest),1)
        self.host.unlink()
        self.assertEqual(discover_owned_manifest_directory(_backend=self.backend),plan.manifest.parent)

    def test_migrates_owned_configuration_when_old_host_is_absent_or_broken(self):
        for old_state in ('missing','corrupt','directory'):
            with self.subTest(old_host=old_state):
                folder=self.root/old_state
                host=folder/'old'/'LumeNativeHost.exe'
                host.parent.mkdir(parents=True)
                host.write_bytes(b'old host; never executed')
                backend=FakeRegistry()
                old_plan=plan_registration(host,EXTENSION,_backend=backend)
                apply_registration(old_plan)
                preserved={path:path.read_bytes() for path in (old_plan.manifest,old_plan.owner_file)}
                host.unlink()
                if old_state=='corrupt':host.write_bytes(b'corrupted old program')
                if old_state=='directory':host.mkdir()
                discovered=discover_owned_manifest_directory(_backend=backend)
                self.assertEqual(discovered,old_plan.manifest.parent)
                new_host=folder/'new'/'app'/'LumeNativeHost.exe'
                new_host.parent.mkdir(parents=True)
                new_host.write_bytes(b'new valid staged host bytes')
                migrated=plan_registration(new_host,SECOND,folder/'new'/'BrowserHost',discovered,_backend=backend)
                apply_registration(migrated)
                self.assertEqual(set(migrated.allowed_origins),{'chrome-extension://'+EXTENSION+'/',
                                                               'chrome-extension://'+SECOND+'/'})
                self.assertTrue(all(backend.read(target).value==str(migrated.manifest) for target in backend.targets()))
                for path,content in preserved.items():self.assertEqual(path.read_bytes(),content)
                if old_state=='missing':self.assertFalse(host.exists())
                if old_state=='corrupt':self.assertEqual(host.read_bytes(),b'corrupted old program')
                if old_state=='directory':self.assertTrue(host.is_dir())

    def test_serialized_receipt_rolls_back_and_rejects_foreign_journal_targets(self):
        plan=self.plan()
        receipt=apply_registration(plan)
        saved=json.loads(json.dumps(receipt.to_dict()))
        altered=json.loads(json.dumps(saved))
        foreign=self.root/'unrelated.txt'
        foreign.write_text('preserve')
        altered['rollback'][0]['target']=str(foreign)
        with self.assertRaises(RegistrationError):rollback_registration(altered,_backend=self.backend)
        self.assertEqual(foreign.read_text(),'preserve')
        result=rollback_registration(saved,_backend=self.backend)
        self.assertEqual(result.rollback_pending,())
        self.assertFalse(plan.manifest.exists())
        self.assertFalse(any(self.backend.read(target).default_exists for target in self.backend.targets()))

    def test_update_rollback_restores_legacy_manifest_exactly(self):
        first=self.plan()
        apply_registration(first)
        before=first.manifest.read_bytes()
        second=self.plan(SECOND)
        receipt=apply_registration(second)
        self.assertNotEqual(second.manifest.read_bytes(),before)
        rollback=rollback_registration(json.loads(json.dumps(receipt.to_dict())),_backend=self.backend)
        self.assertFalse(rollback.rollback_pending)
        self.assertEqual(first.manifest.read_bytes(),before)
        self.assertFalse(self.plan().changed)
        self.assertFalse(rollback_registration(receipt,_backend=self.backend).rollback_pending)

    def test_concurrent_registry_write_during_apply_is_preserved_and_ours_roll_back(self):
        plan=self.plan()
        foreign_target=self.backend.targets()[-1]
        foreign=RegistryState(True,True,'C:/concurrent-other.json',1)
        def concurrent(_target):
            if self.backend.writes==1:self.backend.states[foreign_target]=foreign
        self.backend.after_write=concurrent
        with self.assertRaises(RegistrationError):apply_registration(plan)
        self.assertEqual(self.backend.read(foreign_target),foreign)
        self.assertFalse(plan.manifest.exists())
        self.assertFalse(self.backend.read(self.backend.targets()[0]).default_exists)

    def test_file_write_failure_rolls_back_first_file(self):
        import lume.registration as registration
        plan=self.plan()
        write=registration._atomic_write
        def fail_owner(path,content,**values):
            if path==plan.owner_file:raise OSError('owner write failed')
            return write(path,content,**values)
        with patch('lume.registration._atomic_write',side_effect=fail_owner):
            with self.assertRaises(RegistrationError) as error:apply_registration(plan)
        self.assertFalse(error.exception.receipt.rollback_pending)
        self.assertFalse(plan.manifest.exists())
        self.assertFalse(plan.owner_file.exists())
        self.assertEqual(self.backend.writes,0)

    def test_unregister_uncertain_failure_restores_our_default(self):
        plan=self.plan()
        apply_registration(plan)
        delete=self.backend.delete_default
        def failure(target):
            delete(target)
            raise OSError('uncertain delete')
        with patch.object(self.backend,'delete_default',side_effect=failure):
            with self.assertRaises(RegistrationError):unregister_owned(self.host,plan.manifest.parent,_backend=self.backend)
        self.assertTrue(plan.manifest.exists())
        self.assertTrue(all(self.backend.read(target).value==str(plan.manifest) for target in self.backend.targets()))

@unittest.skipUnless(os.name=='nt','Windows registry integration')
class PrivateWindowsRegistryTests(unittest.TestCase):
    plan = RegistrationTests.plan
    def setUp(self):
        RegistrationTests.setUp(self)
        self.namespace='Software\\LumeRegistrationTests\\'+uuid.uuid4().hex
        self.backend=WindowsRegistry(namespace=self.namespace)
    def tearDown(self):
        import winreg
        def remove(path,view):
            assert path.startswith(self.namespace)
            access=winreg.KEY_WOW64_32KEY if view==32 else winreg.KEY_WOW64_64KEY
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,path,0,winreg.KEY_ALL_ACCESS|access) as key:
                    subkeys=[]
                    for index in range(winreg.QueryInfoKey(key)[0]):subkeys.append(winreg.EnumKey(key,index))
                for child in subkeys:remove(path+'\\'+child,view)
                winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER,path,access,0)
            except FileNotFoundError:pass
        for view in (32,64):remove(self.namespace,view)
        RegistrationTests.tearDown(self)
    # Real keys use the same plan/apply/readback/idempotent flow; fake fault
    # injection tests remain in RegistrationTests above.
    def test_private_hkcu_all_views_and_unregistration(self):
        plan=self.plan()
        receipt=apply_registration(plan)
        self.assertTrue(receipt.applied)
        for target in self.backend.targets():self.assertEqual(self.backend.read(target).value,str(plan.manifest))
        self.assertFalse(self.plan().changed)
        rollback=rollback_registration(json.loads(json.dumps(receipt.to_dict())),_backend=self.backend)
        self.assertFalse(rollback.rollback_pending)
        for target in self.backend.targets():self.assertFalse(self.backend.read(target).default_exists)
        self.assertFalse(plan.manifest.exists())
        apply_registration(self.plan())
        self.assertEqual(unregister_owned(self.host,plan.manifest.parent,_backend=self.backend).status,'removed')
        for target in self.backend.targets():self.assertFalse(self.backend.read(target).default_exists)

if __name__=='__main__':unittest.main()
