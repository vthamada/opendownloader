"""Local installer ownership/rollback through real temporary filesystem paths."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lume.deployment import DeploymentError, plan_install, install, uninstall


class IntegrationFixture:
    """Stable integration seam; real Windows registry/links have separate coverage."""
    def __init__(self):
        self.fail = False
        self.applied = []
        self.rolled_back = []
    def plan(self, destination, extension_id, previous):
        return {"destination": str(destination), "extension_id": extension_id}
    def apply(self, plan):
        if self.fail:
            raise OSError("fixture registration failure")
        self.applied.append(plan)
        return {"fixture": True}
    def rollback(self, receipt):
        self.rolled_back.append(receipt)
    def remove(self, owner):
        return []


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        (self.source / "_internal").mkdir(parents=True)
        (self.source / "extension").mkdir()
        for name, body in {"LumeDownloads.exe": b"desktop fixture, not executable",
                           "LumeNativeHost.exe": b"native fixture, not executable",
                           "_internal/runtime.bin": b"runtime", "extension/manifest.json": b"{}"}.items():
            (self.source/name).write_bytes(body)
        self.uninstaller = self.root / "Uninstall-source.exe"
        self.uninstaller.write_bytes(b"uninstaller fixture, not executable")
        self.destination = self.root / "installed"
        self.backend = IntegrationFixture()
    def plan(self):
        return plan_install(self.source, self.destination, self.uninstaller,
                            extension_id="a"*32, backend=self.backend)
    def test_plan_has_no_mutations_and_install_copies_exact_hashes(self):
        plan = self.plan()
        self.assertFalse(self.destination.exists())
        self.assertEqual(plan.to_dict()["destination"], str(self.destination))
        receipt = install(plan).to_dict()
        self.assertEqual(receipt["status"], "installed")
        self.assertEqual((self.destination/"app/LumeDownloads.exe").read_bytes(),
                         (self.source/"LumeDownloads.exe").read_bytes())
        owner = json.loads((self.destination/"installation.json").read_text())
        self.assertEqual(owner["id"], "lume-installation-v1")
        self.assertEqual(owner["files"]["LumeDownloads.exe"]["sha256"],
                         hashlib.sha256((self.source/"LumeDownloads.exe").read_bytes()).hexdigest())
    def test_failed_integration_restores_previous_app_uninstaller_and_owner(self):
        install(self.plan())
        old = {path: (self.destination/path).read_bytes() for path in
               ("app/LumeDownloads.exe", "installation.json", "Uninstall.exe")}
        (self.source/"LumeDownloads.exe").write_bytes(b"new desktop")
        self.backend.fail = True
        with self.assertRaises(DeploymentError):
            install(self.plan())
        for path, body in old.items():
            self.assertEqual((self.destination/path).read_bytes(), body)
    def test_source_change_after_plan_is_rejected_without_destination(self):
        plan = self.plan()
        (self.source/"LumeDownloads.exe").write_bytes(b"changed")
        with self.assertRaises(DeploymentError): install(plan)
        self.assertFalse((self.destination/"app").exists())
        self.assertEqual(self.backend.applied, [])
    def test_foreign_destination_is_preserved(self):
        self.destination.mkdir()
        foreign = self.destination/"foreign.txt"
        foreign.write_bytes(b"preserve")
        with self.assertRaises(DeploymentError): self.plan()
        self.assertEqual(foreign.read_bytes(), b"preserve")
    def test_unknown_root_file_and_queue_are_never_deleted(self):
        install(self.plan())
        unknown = self.destination/"queue.sqlite3"
        unknown.write_bytes(b"caller owned")
        receipt = uninstall(self.destination, backend=self.backend).to_dict()
        self.assertIn(str(unknown), receipt["leftovers"])
        self.assertEqual(unknown.read_bytes(), b"caller owned")
        self.assertFalse((self.destination/"app/LumeDownloads.exe").exists())
    def test_changed_app_file_is_preserved_on_uninstall(self):
        install(self.plan())
        changed = self.destination/"app/LumeDownloads.exe"
        changed.write_bytes(b"user modified")
        self.assertEqual(self.plan().to_dict()["repair_differences"]["app/LumeDownloads.exe"]["state"],"modified")
        result = uninstall(self.destination, backend=self.backend).to_dict()
        self.assertIn(str(changed), result["leftovers"])
        self.assertEqual(changed.read_bytes(), b"user modified")

    def test_repair_replaces_missing_and_modified_files_with_originals_in_backup(self):
        install(self.plan())
        changed=self.destination/"app/_internal/runtime.bin";changed.write_bytes(b"damaged runtime")
        missing=self.destination/"app/LumeDownloads.exe";missing.unlink()
        unknown=self.destination/"app/user-notes.txt";unknown.write_bytes(b"user notes")
        uninstaller=self.destination/"Uninstall.exe";uninstaller.write_bytes(b"damaged uninstall")
        plan=self.plan();differences=plan.to_dict()["repair_differences"]
        self.assertEqual(differences["app/LumeDownloads.exe"]["state"],"missing")
        self.assertEqual(differences["app/user-notes.txt"]["state"],"unknown_preserved_in_backup")
        result=install(plan).to_dict();backup=Path(result["backup_directory"])
        self.assertEqual(changed.read_bytes(),b"runtime")
        self.assertEqual((backup/"app/_internal/runtime.bin").read_bytes(),b"damaged runtime")
        self.assertEqual((backup/"app/user-notes.txt").read_bytes(),b"user notes")
        self.assertEqual((backup/"Uninstall.exe").read_bytes(),b"damaged uninstall")
        self.assertFalse((backup/"app/LumeDownloads.exe").exists())

    def test_failed_repair_restores_damaged_bytes_and_missing_state_exactly(self):
        install(self.plan())
        changed=self.destination/"app/_internal/runtime.bin";changed.write_bytes(b"damaged runtime")
        missing=self.destination/"app/LumeDownloads.exe";missing.unlink()
        self.backend.fail=True
        with self.assertRaises(DeploymentError):install(self.plan())
        self.assertEqual(changed.read_bytes(),b"damaged runtime")
        self.assertFalse(missing.exists())
    def test_payload_source_is_rejected_when_entry_budget_is_exceeded(self):
        with patch("lume.deployment.MAX_ARCHIVE_ENTRIES",2):
            with self.assertRaises(DeploymentError):self.plan()
        self.assertFalse(self.destination.exists())

    def test_destination_changed_during_staging_is_not_overwritten(self):
        import lume.deployment as deployment
        plan=self.plan();original=deployment._copy;inserted=False
        def changed(source,target,facts):
            nonlocal inserted
            original(source,target,facts)
            if not inserted:
                inserted=True;self.destination.mkdir();(self.destination/"foreign.txt").write_bytes(b"new owner")
        with patch.object(deployment,"_copy",side_effect=changed):
            with self.assertRaises(DeploymentError):install(plan)
        self.assertEqual((self.destination/"foreign.txt").read_bytes(),b"new owner")

    def test_failed_integration_rollback_retains_host_and_previous_backup(self):
        import lume.deployment as deployment
        install(self.plan())
        (self.source/"LumeDownloads.exe").write_bytes(b"new desktop retained")
        plan=self.plan();original=deployment._write_json
        def fail_final(path,value):
            if path==self.destination/"installation.json":raise OSError("final receipt failure")
            return original(path,value)
        with patch.object(deployment,"_write_json",side_effect=fail_final), \
             patch.object(self.backend,"rollback",side_effect=OSError("rollback unavailable")):
            with self.assertRaises(DeploymentError) as caught:install(plan)
        self.assertTrue(caught.exception.recovery["retained_host"])
        self.assertEqual((self.destination/"app/LumeDownloads.exe").read_bytes(),b"new desktop retained")
        backup=Path(caught.exception.recovery["backup"])
        self.assertTrue((backup/"app/LumeNativeHost.exe").exists())
        self.assertTrue(Path(caught.exception.recovery["receipt"]).exists())
    def test_symlink_source_and_destination_are_refused(self):
        alias = self.root/"alias"
        try: alias.symlink_to(self.source, target_is_directory=True)
        except OSError as error: self.skipTest(f"Symlink unavailable: {error}")
        with self.assertRaises(DeploymentError):
            plan_install(alias,self.destination,self.uninstaller,extension_id="a"*32,backend=self.backend)
        with self.assertRaises(DeploymentError):
            plan_install(self.source,alias,self.uninstaller,extension_id="a"*32,backend=self.backend)

    def test_failure_after_registration_rolls_back_registration_and_original_app(self):
        import lume.deployment as deployment
        install(self.plan())
        old=(self.destination/"installation.json").read_bytes()
        plan=self.plan()
        original=deployment._write_json
        def fail_final(path,value):
            if path==self.destination/"installation.json":raise OSError("final receipt failure")
            return original(path,value)
        with patch.object(deployment,"_write_json",side_effect=fail_final):
            with self.assertRaises(DeploymentError):install(plan)
        self.assertEqual(len(self.backend.rolled_back),1)
        self.assertEqual((self.destination/"installation.json").read_bytes(),old)


class WindowsDeploymentTests(unittest.TestCase):
    """Actual Windows HKCU private keys, Native Messaging manifest and IShellLink."""
    def setUp(self):
        DeploymentTests.setUp(self)
        if __import__('os').name!="nt":self.skipTest("Windows integration")
        import uuid
        from lume.deployment import WindowsBackend
        from lume.registration import WindowsRegistry
        self.namespace="Software\\LumeRegistrationTests\\"+uuid.uuid4().hex
        self.registry_prefix="Software\\LumeDeploymentTests\\"+uuid.uuid4().hex
        self.backend=WindowsBackend(registry_prefix=self.registry_prefix,programs_directory=self.root/"Programs",
                                    desktop_directory=self.root/"Desktop",registration_backend=WindowsRegistry(namespace=self.namespace))
        self.addCleanup(self.cleanup_private_keys)
    plan = DeploymentTests.plan
    def cleanup_private_keys(self):
        import winreg
        def remove(path):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,path,0,winreg.KEY_READ) as key:
                    children=[winreg.EnumKey(key,index) for index in range(winreg.QueryInfoKey(key)[0])]
                for name in children:remove(path+"\\"+name)
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER,path)
            except FileNotFoundError:pass
        remove(self.namespace);remove(self.registry_prefix)
    def test_windows_links_registry_and_native_host_readback_then_uninstall(self):
        import winreg
        from lume.deployment import _shortcut_target
        plan=self.plan()
        json.dumps(plan.to_dict())
        self.assertFalse((self.root/"Programs").exists())
        install(plan)
        for path in self.backend._links():
            self.assertEqual(_shortcut_target(path),str(self.destination/"app/LumeDownloads.exe"))
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,self.backend.registry_key) as key:
            self.assertEqual(winreg.QueryValueEx(key,"InstallLocation")[0],str(self.destination))
            self.assertEqual(winreg.QueryValueEx(key,"UninstallString")[0],f'"{self.destination / "Uninstall.exe"}"')
        receipt=uninstall(self.destination,backend=self.backend).to_dict()
        self.assertEqual(receipt["status"],"preserved_leftovers",receipt)
        self.assertFalse((self.destination/"app").exists())
        self.assertIsNone(self.backend._registry())
        self.assertTrue(all(not path.exists() for path in self.backend._links()))

    def _install_with_lagging_display_version(self):
        import winreg
        with patch("lume.deployment.VERSION","1.8.0"):
            install(self.plan())
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,self.backend.registry_key,0,winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key,"DisplayVersion",0,winreg.REG_SZ,"1.7.2")
        return self.backend._registry()

    def test_owned_older_display_version_is_planned_read_only_repaired_and_idempotent(self):
        from lume import deployment
        registry=self._install_with_lagging_display_version()
        owner=(self.destination/"installation.json").read_bytes()
        files={path:path.read_bytes() for path in self.destination.rglob("*") if path.is_file()}
        plan=self.plan()
        shell=plan.to_dict()["integration"]["shell"]
        self.assertEqual(shell["registry_before"],registry)
        self.assertEqual(shell["registry_repair"],{"DisplayVersion":{
            "state":"older_display_version","observed":["1.7.2",1],"recorded":["1.8.0",1]}})
        self.assertEqual(self.backend._registry(),registry)
        self.assertEqual({path:path.read_bytes() for path in self.destination.rglob("*") if path.is_file()},files)
        install(plan)
        self.assertEqual(self.backend._registry()["DisplayVersion"][0],deployment.VERSION)
        repaired=json.loads((self.destination/"installation.json").read_text())
        self.assertEqual(repaired["integration"]["shell"]["registry"]["before"],registry)
        self.assertNotEqual((self.destination/"installation.json").read_bytes(),owner)
        again=self.plan()
        self.assertNotIn("registry_repair",again.integration_plan["shell"])
        install(again)
        self.assertNotIn("registry_repair",self.plan().integration_plan["shell"])

    def test_lag_repair_rollback_restores_observed_version_not_recorded_version(self):
        from lume import deployment
        registry=self._install_with_lagging_display_version()
        owner=(self.destination/"installation.json").read_bytes()
        original=deployment._write_json
        def fail_receipt(path,value):
            if path==self.destination/"installation.json":raise OSError("final receipt failure after display repair")
            return original(path,value)
        with patch.object(deployment,"_write_json",side_effect=fail_receipt):
            with self.assertRaises(DeploymentError):install(self.plan())
        self.assertEqual(self.backend._registry(),registry)
        self.assertEqual(self.backend._registry()["DisplayVersion"][0],"1.7.2")
        self.assertEqual((self.destination/"installation.json").read_bytes(),owner)
        self.assertIn("registry_repair",self.plan().integration_plan["shell"])

    def test_lag_repair_refuses_other_registry_drift_including_unknown_values(self):
        import winreg
        registry=self._install_with_lagging_display_version()
        owner=(self.destination/"installation.json").read_bytes()
        for name,value,kind in (("DisplayName","Foreign product",winreg.REG_SZ),
                                ("Publisher","Foreign publisher",winreg.REG_SZ),
                                ("InstallLocation",str(self.root/"other"),winreg.REG_SZ),
                                ("UninstallString","foreign.exe",winreg.REG_SZ),
                                ("DisplayIcon","foreign.exe,0",winreg.REG_SZ),
                                ("NoModify",0,winreg.REG_DWORD),
                                ("UnknownValue","external",winreg.REG_SZ)):
            with self.subTest(name=name):
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,self.backend.registry_key,0,winreg.KEY_SET_VALUE) as key:
                    winreg.SetValueEx(key,name,0,kind,value)
                changed=self.backend._registry()
                with self.assertRaises(DeploymentError):self.plan()
                self.assertEqual(self.backend._registry(),changed)
                self.assertEqual((self.destination/"installation.json").read_bytes(),owner)
                self.backend._replace_registry(changed,registry)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,self.backend.registry_key,0,winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key,"Publisher")
        missing=self.backend._registry()
        with self.assertRaises(DeploymentError):self.plan()
        self.assertEqual(self.backend._registry(),missing)

    def test_lag_repair_refuses_newer_or_malformed_display_versions(self):
        import winreg
        self._install_with_lagging_display_version()
        for value,kind in (("1.8.1",winreg.REG_SZ),("2.0.0",winreg.REG_SZ),
                           ("1.7",winreg.REG_SZ),("01.7.2",winreg.REG_SZ),
                           ("1.7.2-beta",winreg.REG_SZ),(" 1.7.2",winreg.REG_SZ),
                           ("1.7.2\n",winreg.REG_SZ),(172,winreg.REG_DWORD)):
            with self.subTest(value=value,kind=kind):
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,self.backend.registry_key,0,winreg.KEY_SET_VALUE) as key:
                    winreg.SetValueEx(key,"DisplayVersion",0,kind,value)
                changed=self.backend._registry()
                with self.assertRaises(DeploymentError):self.plan()
                self.assertEqual(self.backend._registry(),changed)

    def test_lag_repair_requires_recorded_version_to_match_previous_owner(self):
        self._install_with_lagging_display_version()
        path=self.destination/"installation.json"
        previous=json.loads(path.read_text())
        registry=self.backend._registry()
        for version in ("1.7.9",None,172):
            with self.subTest(version=version):
                previous["version"]=version
                path.write_text(json.dumps(previous))
                with self.assertRaises(DeploymentError):self.plan()
                self.assertEqual(self.backend._registry(),registry)

    def _install_previous_brand(self):
        # Exercise migration through the actual shell/registry owner, with old links.
        from lume import deployment
        old=deployment.WindowsBackend._legacy_links
        with patch.object(deployment.WindowsBackend,"_links",old), \
             patch.object(deployment.WindowsBackend,"_legacy_links",lambda _self: []), \
             patch.object(deployment,"APP_NAME","Lume Downloads"), \
             patch.object(deployment,"VERSION","1.7.1"):
            install(self.plan())
        return {path:path.read_bytes() for path in self.backend._legacy_links()}

    def test_rebrand_migrates_owned_shortcuts_and_preserves_queue_and_bridge_identity(self):
        from lume.registration import HOST_NAME
        self._install_previous_brand()
        queue=self.destination/"queue.sqlite3";queue.write_bytes(b"existing user queue")
        install(self.plan())
        self.assertEqual(self.backend._registry()["DisplayName"][0],"OpenDownloader")
        self.assertTrue(all(path.exists() for path in self.backend._links()))
        self.assertTrue(all(not path.exists() for path in self.backend._legacy_links()))
        self.assertEqual(queue.read_bytes(),b"existing user queue")
        self.assertEqual(HOST_NAME,"app.lume.downloads")
        # Repair is idempotent, then uninstall removes only current owned links.
        install(self.plan())
        uninstall(self.destination,backend=self.backend)
        self.assertTrue(all(not path.exists() for path in self.backend._links()))

    def test_rebrand_failure_restores_old_shortcuts_registry_and_owner(self):
        import winreg
        old_links=self._install_previous_brand()
        owner=(self.destination/"installation.json").read_bytes()
        registry=self.backend._registry()
        original=winreg.SetValueEx;failed=False
        def fail_once(*args):
            nonlocal failed
            if not failed:failed=True;raise OSError("fixture rebrand failure")
            return original(*args)
        with patch.object(winreg,"SetValueEx",side_effect=fail_once):
            with self.assertRaises(DeploymentError):install(self.plan())
        for path,body in old_links.items():self.assertEqual(path.read_bytes(),body)
        self.assertEqual(self.backend._registry(),registry)
        self.assertEqual((self.destination/"installation.json").read_bytes(),owner)
        self.assertTrue(all(not path.exists() for path in self.backend._links()))

    def test_rebrand_preserves_modified_legacy_shortcut(self):
        old_links=self._install_previous_brand()
        changed=next(iter(old_links));changed.write_bytes(b"user changed legacy shortcut")
        with self.assertRaises(DeploymentError):self.plan()
        self.assertEqual(changed.read_bytes(),b"user changed legacy shortcut")
    def test_foreign_shortcut_and_registry_are_never_overwritten(self):
        import winreg
        link=self.backend._links()[0]
        link.parent.mkdir(parents=True)
        link.write_bytes(b"foreign shortcut")
        with self.assertRaises(DeploymentError):self.plan()
        self.assertEqual(link.read_bytes(),b"foreign shortcut")
        link.unlink()
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,self.backend.registry_key) as key:
            winreg.SetValueEx(key,"DisplayName",0,winreg.REG_SZ,"Other product")
        with self.assertRaises(DeploymentError):self.plan()
    def test_changed_shortcut_and_registry_are_preserved_on_uninstall(self):
        import winreg
        install(self.plan())
        link=self.backend._links()[0];link.write_bytes(b"changed shortcut")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,self.backend.registry_key,0,winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key,"DisplayName",0,winreg.REG_SZ,"User changed")
        result=uninstall(self.destination,backend=self.backend).to_dict()
        self.assertEqual(link.read_bytes(),b"changed shortcut")
        self.assertEqual(self.backend._registry()["DisplayName"][0],"User changed")
        self.assertTrue(result["leftovers"])

    def test_real_registry_partial_write_rolls_back_files_links_and_registry(self):
        import winreg
        original=winreg.SetValueEx;calls=0
        def fail_once(*args):
            nonlocal calls
            calls+=1
            if calls==3:raise OSError("fixture third value fails")
            return original(*args)
        plan=self.plan()
        with patch.object(winreg,"SetValueEx",side_effect=fail_once):
            with self.assertRaises(DeploymentError):install(plan)
        self.assertIsNone(self.backend._registry())
        self.assertTrue(all(not path.exists() for path in self.backend._links()))
        self.assertFalse((self.destination/"app").exists())

    def test_real_update_and_registration_rollback_after_final_receipt_failure(self):
        import lume.deployment as deployment
        install(self.plan())
        previous=(self.destination/"installation.json").read_bytes()
        registry=self.backend._registry()
        (self.source/"LumeDownloads.exe").write_bytes(b"updated")
        plan=self.plan();original=deployment._write_json
        def fail_once(path,value):
            if path==self.destination/"installation.json":raise OSError("receipt publication failure")
            return original(path,value)
        with patch.object(deployment,"_write_json",side_effect=fail_once):
            with self.assertRaises(DeploymentError):install(plan)
        self.assertEqual((self.destination/"installation.json").read_bytes(),previous)
        self.assertEqual(self.backend._registry(),registry)
        for target in self.backend.registration_backend.targets():
            self.assertTrue(self.backend.registration_backend.read(target).default_exists)

    def test_successful_update_replaces_native_host_and_preserves_previous_app(self):
        install(self.plan())
        original=(self.destination/"app/LumeNativeHost.exe").read_bytes()
        (self.source/"LumeNativeHost.exe").write_bytes(b"new native host")
        result=install(self.plan()).to_dict()
        self.assertEqual((self.destination/"app/LumeNativeHost.exe").read_bytes(),b"new native host")
        backup=Path(result["backup_directory"])
        self.assertEqual((backup/"app/LumeNativeHost.exe").read_bytes(),original)
        uninstall(self.destination,backend=self.backend)
        self.assertTrue((backup/"app/LumeNativeHost.exe").exists())

    def test_repair_missing_and_corrupted_host_uses_owned_manifest_not_old_validity(self):
        install(self.plan())
        target=self.destination/"app/LumeNativeHost.exe"
        for state in ("modified","missing"):
            if state=="modified":target.write_bytes(b"corrupted native host")
            else:target.unlink()
            plan=self.plan()
            self.assertEqual(plan.to_dict()["repair_differences"]["app/LumeNativeHost.exe"]["state"],state)
            result=install(plan).to_dict()
            self.assertEqual(target.read_bytes(),(self.source/"LumeNativeHost.exe").read_bytes())
            backup=Path(result["backup_directory"])/"app/LumeNativeHost.exe"
            if state=="modified":self.assertEqual(backup.read_bytes(),b"corrupted native host")
            else:self.assertFalse(backup.exists())

    def test_registry_failure_keeps_preexisting_empty_desktop_and_menu_folder(self):
        import winreg
        self.backend.desktop.mkdir(parents=True)
        menu=self.backend.programs/"OpenDownloader";menu.mkdir(parents=True)
        with patch.object(winreg,"SetValueEx",side_effect=OSError("registry unavailable")):
            with self.assertRaises(DeploymentError):install(self.plan())
        self.assertTrue(self.backend.desktop.is_dir())
        self.assertTrue(menu.is_dir())

    def test_unregistration_failure_retains_host_runtime_shortcuts_and_uninstaller(self):
        from lume import registration
        install(self.plan())
        before=self.backend._registry()
        with patch.object(registration,"unregister_owned",side_effect=OSError("fixture registry unavailable")):
            result=uninstall(self.destination,backend=self.backend).to_dict()
        self.assertEqual(result["status"],"recovery_pending")
        self.assertTrue(result["retained_host"])
        self.assertTrue((self.destination/"app/LumeNativeHost.exe").exists())
        self.assertTrue((self.destination/"app/_internal/runtime.bin").exists())
        self.assertTrue((self.destination/"Uninstall.exe").exists())
        self.assertEqual(self.backend._registry(),before)
        self.assertTrue(all(path.exists() for path in self.backend._links()))
        self.assertEqual(uninstall(self.destination,backend=self.backend).to_dict()["status"],"preserved_leftovers")

    def test_owned_portable_registration_migrates_without_deleting_old_files(self):
        from lume.registration import plan_registration,apply_registration
        portable=self.root/"portable";portable.mkdir()
        host=portable/"LumeNativeHost.exe";host.write_bytes(b"portable native fixture")
        old=portable/"BrowserHost"
        apply_registration(plan_registration(host,"a"*32,old,_backend=self.backend.registration_backend))
        install(self.plan())
        for target in self.backend.registration_backend.targets():
            state=self.backend.registration_backend.read(target)
            self.assertEqual(state.value,str(self.destination/"BrowserHost/app.lume.downloads.json"))
        self.assertEqual(host.read_bytes(),b"portable native fixture")
        self.assertTrue((old/"app.lume.downloads.json").exists())

    def test_installation_mutex_rejects_concurrent_writer(self):
        import threading
        from lume.deployment import _deployment_lock
        entered=threading.Event();release=threading.Event()
        def held():
            with _deployment_lock(self.destination):entered.set();release.wait(5)
        thread=threading.Thread(target=held);thread.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaises(DeploymentError):install(self.plan())
        finally:release.set();thread.join(5)

    def test_junction_sources_destinations_and_payload_children_are_refused(self):
        import subprocess
        alias=self.root/"junction"
        made=subprocess.run(["cmd.exe","/d","/c","mklink","/J",str(alias),str(self.source)],
                            capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
        if made.returncode:self.skipTest("Junction unavailable: "+made.stderr.decode(errors='replace'))
        try:
            self.assertTrue(alias.is_junction())
            with self.assertRaises(DeploymentError):
                plan_install(alias,self.destination,self.uninstaller,extension_id="a"*32,backend=self.backend)
            with self.assertRaises(DeploymentError):
                plan_install(self.source,alias,self.uninstaller,extension_id="a"*32,backend=self.backend)
            child=self.source/"linked-runtime"
            made=subprocess.run(["cmd.exe","/d","/c","mklink","/J",str(child),str(self.source/"_internal")],
                                capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(made.returncode,0)
            try:
                with self.assertRaises(DeploymentError):self.plan()
            finally:child.rmdir()
        finally:alias.rmdir()
