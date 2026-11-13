import asyncio
import importlib
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import config
from services import db_service
from services import lotus_service
from services import pc_monitor_service
from services.ad_service import ActiveDirectoryService, _extract_cn
from services.print_service import generate_equipment_act
from services.zoom_service import ZoomService


class FakeStat:
    def __init__(self, size: int, mtime: float = 1700000000.0):
        self.st_size = size
        self.st_mtime = mtime


class FakeEntry:
    def __init__(self, name: str, is_dir: bool = False, is_file: bool = False, size: int = 4096):
        self.name = name
        self._is_dir = is_dir
        self._is_file = is_file
        self._size = size

    def is_dir(self):
        return self._is_dir

    def is_file(self):
        return self._is_file

    def stat(self):
        return FakeStat(self._size)


class FakeSMBFile:
    def __init__(self, vfs: dict, dirs: set, path: str, mode: str):
        self.vfs = vfs
        self.dirs = dirs
        self.path = path
        self.mode = mode
        self.buffer = bytearray()

    def __enter__(self):
        if "r" in self.mode:
            if self.path not in self.vfs:
                raise FileNotFoundError(self.path)
            return self
        return self

    def read(self):
        return self.vfs[self.path]

    def write(self, data: bytes):
        self.buffer.extend(data)

    def __exit__(self, exc_type, exc, tb):
        if "w" in self.mode and exc_type is None:
            self.vfs[self.path] = bytes(self.buffer)


class DBAndLotusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_file = os.path.join(self.tmp.name, "test_kolins.db")
        self.orig_db_path = db_service.DB_PATH
        db_service.DB_PATH = self.db_file
        db_service.init_db(self.db_file)

    def tearDown(self):
        db_service.DB_PATH = self.orig_db_path
        self.tmp.cleanup()

    def test_db_wal_settings_sessions_and_cert_ou(self):
        with db_service._get_conn(self.db_file) as conn:
            mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

        self.assertEqual(db_service.get_setting("lockout_alerts_enabled", "0"), "0")
        db_service.set_setting("lockout_alerts_enabled", "1")
        self.assertEqual(db_service.get_setting("lockout_alerts_enabled", "0"), "1")

        db_service.save_user_pc_session("Ivanov", "n10453-1", "N10453-1.kdb.kz")
        self.assertEqual(db_service.count_user_pc_sessions(), 1)
        by_user = db_service.get_user_pc_session("IVANOV")
        self.assertEqual(by_user["pc_name"], "N10453-1")
        by_pc = db_service.get_pc_user_session("N10453-1")
        self.assertEqual(by_pc["username"], "ivanov")

        db_service.save_original_ou("petrov", "OU=Finance,DC=kdb,DC=kz")
        cert_list = db_service.list_users_in_cert_ou()
        self.assertEqual(len(cert_list), 1)
        self.assertEqual(cert_list[0]["original_ou"], "OU=Finance,DC=kdb,DC=kz")
        db_service.remove_original_ou("petrov")
        self.assertEqual(db_service.list_users_in_cert_ou(), [])

    def test_lotus_unc_helpers_and_error_classification(self):
        server, share, parts = lotus_service._parse_unc(r"\\storage\public\id")
        self.assertEqual((server, share, parts), ("storage", "public", ["id"]))
        self.assertEqual(lotus_service._build_unc(server, share, parts), r"\\storage\public\id")

        code, msg = lotus_service._classify_smb_error(Exception("NT_STATUS_ACCESS_DENIED (0xc0000022)"))
        self.assertEqual(code, "access_denied")
        self.assertIn("RemoteAdmins", msg)

        code2, _ = lotus_service._classify_smb_error(Exception("STATUS_SHARING_VIOLATION 0xc0000043"))
        self.assertEqual(code2, "in_use")

        code3, _ = lotus_service._classify_smb_error(TimeoutError("timed out"))
        self.assertEqual(code3, "offline")

    def test_lotus_extract_collision_and_restore_cycle(self):
        pc_dir = r"\\N10453-1.kdb.kz\C$\Users\ivanov\AppData\Local\IBM\Notes\Data"
        storage_root = r"\\storage\public"
        storage_id_dir = r"\\storage\public\id"

        vfs = {
            f"{pc_dir}\\user.id": b"LOTUS-ID-SECRET-BYTES-12345",
            f"{storage_id_dir}\\user.id": b"ANOTHER-USER-ID-DO-NOT-OVERWRITE",
        }
        dirs = {
            r"\\N10453-1.kdb.kz\C$\Users",
            r"\\N10453-1.kdb.kz\C$\Users\Public",
            r"\\N10453-1.kdb.kz\C$\Users\ivanov",
            r"\\N10453-1.kdb.kz\C$\Users\ivanov\AppData",
            r"\\N10453-1.kdb.kz\C$\Users\ivanov\AppData\Local",
            r"\\N10453-1.kdb.kz\C$\Users\ivanov\AppData\Local\IBM",
            r"\\N10453-1.kdb.kz\C$\Users\ivanov\AppData\Local\IBM\Notes",
            pc_dir,
            storage_root,
        }

        fake_smb = MagicMock()
        fake_smb.register_session = MagicMock()
        fake_smb.path.isdir.side_effect = lambda p: p in dirs
        fake_smb.path.isfile.side_effect = lambda p: p in vfs
        fake_smb.stat.side_effect = lambda p: FakeStat(len(vfs[p]))

        def fake_mkdir(p):
            dirs.add(p)

        def fake_scandir(p):
            if p == r"\\N10453-1.kdb.kz\C$\Users":
                return [FakeEntry("Public", is_dir=True), FakeEntry("ivanov", is_dir=True)]
            if p == pc_dir:
                return [
                    FakeEntry("notes.ini", is_file=True, size=120),
                    FakeEntry("user.id", is_file=True, size=len(vfs[f"{pc_dir}\\user.id"])),
                ]
            return []

        fake_smb.mkdir.side_effect = fake_mkdir
        fake_smb.scandir.side_effect = fake_scandir
        fake_smb.open_file.side_effect = lambda p, mode="rb": FakeSMBFile(vfs, dirs, p, mode)
        fake_smb.remove.side_effect = lambda p: vfs.pop(p, None)

        with patch.object(lotus_service, "smbclient", fake_smb):
            # 1. Scan PC
            scan = lotus_service.scan_pc_lotus_ids("N10453-1.kdb.kz")
            self.assertTrue(scan["ok"])
            self.assertEqual(len(scan["files"]), 1)
            self.assertEqual(scan["files"][0]["filename"], "user.id")
            self.assertEqual(scan["files"][0]["win_profile"], "ivanov")

            # 2. Check collision -> should auto-create \\storage\public\id and detect user.id conflict
            col = lotus_service.check_storage_collision("user.id")
            self.assertTrue(col["ok"])
            self.assertTrue(col["exists"])
            self.assertIn(storage_id_dir, dirs)

            # 3. Extract with renamed storage file while memorizing original_filename='user.id'
            safe_storage_name = "N10453-1_ivanov_user.id"
            ext = lotus_service.extract_id_to_storage(
                pc_host="N10453-1.kdb.kz",
                pc_name="N10453-1",
                win_profile="ivanov",
                unc_dir=pc_dir,
                original_filename="user.id",
                storage_filename=safe_storage_name,
                ad_username="ivanov",
                admin_id=1001,
            )
            self.assertTrue(ext["ok"])
            tid = ext["transfer_id"]
            self.assertIn(f"{storage_id_dir}\\{safe_storage_name}", vfs)

            # Simulate password reset updating file inside storage
            vfs[f"{storage_id_dir}\\{safe_storage_name}"] = b"RESET-PASSWORD-LOTUS-ID-BYTES"

            # 4. Restore back -> must write to pc_dir\user.id (original name), NOT safe_storage_name!
            res = lotus_service.restore_id_from_storage(tid, cleanup_storage=False)
            self.assertTrue(res["ok"])
            self.assertEqual(res["dst_path"], f"{pc_dir}\\user.id")
            self.assertEqual(vfs[f"{pc_dir}\\user.id"], b"RESET-PASSWORD-LOTUS-ID-BYTES")
            # Original storage user.id was never overwritten!
            self.assertEqual(vfs[f"{storage_id_dir}\\user.id"], b"ANOTHER-USER-ID-DO-NOT-OVERWRITE")

            # Memorized history remains available after return
            latest = db_service.get_latest_lotus_transfer_by_pc("N10453-1")
            self.assertEqual(latest["original_filename"], "user.id")
            self.assertEqual(latest["status"], "returned")

            # 5. Cleanup storage copy
            del_res = lotus_service.delete_storage_copy(tid)
            self.assertTrue(del_res["ok"])
            self.assertTrue(del_res["deleted"])
            self.assertNotIn(f"{storage_id_dir}\\{safe_storage_name}", vfs)

            # 6. Test STATUS_SHARING_VIOLATION (in_use) handling on restore
            ext2 = lotus_service.extract_id_to_storage(
                pc_host="N10453-1.kdb.kz",
                pc_name="N10453-1",
                win_profile="ivanov",
                unc_dir=pc_dir,
                original_filename="user.id",
                storage_filename="N10453-1_ivanov_user.id",
            )
            self.assertTrue(ext2["ok"])
            orig_open = fake_smb.open_file.side_effect

            def sharing_open(p, mode="rb"):
                if "w" in mode and p == f"{pc_dir}\\user.id":
                    raise RuntimeError("STATUS_SHARING_VIOLATION (0xc0000043)")
                return orig_open(p, mode)

            fake_smb.open_file.side_effect = sharing_open
            in_use_res = lotus_service.restore_id_from_storage(ext2["transfer_id"])
            self.assertFalse(in_use_res["ok"])
            self.assertEqual(in_use_res["error_code"], "in_use")


class CoreServicesAndHandlersTests(unittest.TestCase):
    def test_print_service_xml_escape_and_hw_info(self):
        user_det = {
            "username": "o&connor",
            "display_name": "О'Коннор & Тест <ИТ>",
            "department": "Департамент & СБ",
            "title": "Специалист <1>",
            "created_at": "01.01.2020 10:00",
        }
        pc_det = {
            "name": "N10453-1",
            "dns_name": "N10453-1.kdb.kz",
            "os": "Windows 11 Pro",
            "description": "Ноутбук <Тест & Отладка>",
        }
        hw_info = {
            "manufacturer": "LENOVO",
            "model": "ThinkPad T14 & Gen4",
            "serial_number": "PF3<TEST>1",
        }
        buf = generate_equipment_act(user_det, pc_det, hw_info)
        data = buf.getvalue()
        self.assertTrue(data.startswith(b"%PDF-"))
        self.assertGreater(len(data), 1000)

    def test_zoom_service_no_fake_fallback(self):
        zs = ZoomService()
        zs.account_id = ""
        zs.client_id = ""
        zs.client_secret = ""
        self.assertFalse(zs.is_configured())
        res = asyncio.run(zs.create_meeting("Support"))
        self.assertIsNone(res)

    def test_ad_service_safety_guards(self):
        self.assertEqual(_extract_cn("CN=Domain Admins,CN=Users,DC=kdb,DC=kz"), "Domain Admins")
        ad = ActiveDirectoryService.__new__(ActiveDirectoryService)
        ok, msg = ad.add_user_to_group("ivanov", "CN=Domain Admins,CN=Users,DC=kdb,DC=kz")
        self.assertFalse(ok)
        self.assertIn("запрещено", msg)
        ok2, msg2 = ad.remove_user_from_group("ivanov", "CN=Enterprise Admins,CN=Users,DC=kdb,DC=kz")
        self.assertFalse(ok2)
        self.assertIn("запрещено", msg2)

    def test_pc_monitor_helpers(self):
        self.assertFalse(pc_monitor_service.check_pc_online("—"))
        self.assertFalse(pc_monitor_service.check_pc_online(""))
        parsed = pc_monitor_service._parse_quser_output(
            " USERNAME              SESSIONNAME        ID  STATE   IDLE TIME  LOGON TIME\n"
            ">ivanov                console             1  Active      none   11.11.2026 09:00\n"
        )
        self.assertEqual(parsed, "ivanov")

    def test_ad_monitor_worker_opt_in_and_baseline(self):
        from services import ad_monitor_worker

        tmp = tempfile.TemporaryDirectory()
        orig_db = db_service.DB_PATH
        try:
            db_service.DB_PATH = os.path.join(tmp.name, "worker.db")
            db_service.init_db(db_service.DB_PATH)
            ad_monitor_worker.reset_lockout_baseline()

            locked_sample = [{"username": "jdoe", "display_name": "John Doe", "department": "IT", "bad_pwd_count": 5}]
            fake_bot = MagicMock()

            with patch.object(config, "LOG_CHAT_ID", -100123), \
                 patch.object(ad_monitor_worker.ad_service, "get_locked_users", return_value=locked_sample) as mock_get, \
                 patch.object(ad_monitor_worker, "send_rich_to_chat", new_callable=AsyncMock) as mock_send:

                # Disabled by default -> zero AD queries, zero chat spam
                asyncio.run(ad_monitor_worker.check_locked_accounts(fake_bot))
                mock_get.assert_not_called()
                mock_send.assert_not_called()

                # Enabled -> first run seeds baseline WITHOUT sending chat spam
                db_service.set_setting("lockout_alerts_enabled", "1")
                asyncio.run(ad_monitor_worker.check_locked_accounts(fake_bot))
                mock_get.assert_called_once()
                mock_send.assert_not_called()

                # Second run with NEW locked user -> sends alert ONLY for new user
                locked_sample.append({"username": "newlock", "display_name": "New Lock", "department": "IT", "bad_pwd_count": 6})
                asyncio.run(ad_monitor_worker.check_locked_accounts(fake_bot))
                mock_send.assert_awaited_once()
        finally:
            db_service.DB_PATH = orig_db
            tmp.cleanup()

    def test_routers_import_and_password_generator(self):
        handlers_pkg = importlib.import_module("handlers")
        self.assertEqual(len(handlers_pkg.routers), 5)

        from handlers.user_handlers import _generate_strong_password, render_user_profile, _render_user_search_page
        from handlers.pc_handlers import render_pc_profile, _render_pc_search_page
        from handlers.menu_handlers import render_home, render_stats

        for _ in range(10):
            pw = _generate_strong_password(12)
            self.assertEqual(len(pw), 12)
            self.assertTrue(any(c.isupper() for c in pw))
            self.assertTrue(any(c.islower() for c in pw))
            self.assertTrue(any(c.isdigit() for c in pw))
            self.assertTrue(any(c in "!@#$%&*+-=?" for c in pw))

        # Smoke test rich card renderers with max-length AD account names (callback <= 64 bytes)
        long_user = "a_very_long_sAMAccount"[:20]
        long_pc = "N10453-WIN11-TEST"[:15]
        home_html = render_home("Admin", True, pending_lotus=2)
        self.assertIn("Kolins", home_html)
        self.assertIn("Lotus ID (2)", home_html)

        stats_html = render_stats({"total_users": 100, "active_users": 90, "disabled_users": 10, "locked_users": 1, "total_computers": 50, "stale_computers": 5}, 12)
        self.assertIn("100", stats_html)

        pc_html = render_pc_profile({"name": long_pc, "dns_name": f"{long_pc}.kdb.kz", "os": "Windows 11", "os_version": "23H2", "description": "Test", "last_logon": "13.11.2026", "created": "01.01.2025", "dn": f"CN={long_pc},DC=kdb,DC=kz"}, True, {"username": long_user, "source": "live", "updated_at": None, "error_code": None}, active_lotus_count=1)
        self.assertIn(long_pc, pc_html)
        self.assertIn(f"user:show:{long_user}", pc_html)

        user_html = render_user_profile({"username": long_user, "display_name": "Тестовый Сотрудник", "title": "Спец", "department": "ИТ", "telephone_number": "100", "mobile": "+7700", "mail": "t@kdb.kz", "physical_delivery_office_name": "201", "manager_name": "Рук", "pwd_expiry": "01.01.2027", "created_at": "01.01.2024", "pwd_last_set": "01.10.2026", "last_logon": "13.11.2026", "bad_pwd_count": 0, "logon_workstation": "—", "script_path": "—", "dn": f"CN={long_user},DC=kdb,DC=kz", "groups": ["G1"], "is_disabled": False, "is_locked": False}, cached_pc={"pc_name": long_pc}, cert_pending=True)
        self.assertIn(long_user, user_html)
        self.assertIn(long_pc, user_html)

        # Render middle pages so both ◀️ and ▶️ pagination button_row(*nav) are rendered
        us_html = _render_user_search_page([{"username": f"u{i}_{long_user}"[:20], "display_name": f"User {i}", "is_disabled": False, "is_locked": False} for i in range(25)], page=1)
        self.assertIn("upage:0", us_html)
        self.assertIn("upage:2", us_html)

        ps_html = _render_pc_search_page([{"Name": f"PC{i}_{long_pc}"[:15], "Description": f"Desc {i}"} for i in range(25)], page=1)
        self.assertIn("ppage:0", ps_html)
        self.assertIn("ppage:2", ps_html)

        # Health paginated screens
        from handlers import health_handlers
        fake_cb = MagicMock()
        fake_cb.message = MagicMock()
        fake_cb.answer = AsyncMock()
        locked_many = [{"username": f"lock{i}", "display_name": f"Lock {i}", "department": "IT", "bad_pwd_count": i, "lockout_time": "13.11.2026"} for i in range(20)]
        exp_many = [{"username": f"exp{i}", "display_name": f"Exp {i}", "days_left": i, "expires_at": "20.11.2026"} for i in range(20)]
        stale_many = [{"name": f"OLD{i:02d}", "os": "Win10", "last_logon": "01.01.2025", "days_ago": 300} for i in range(20)]
        with patch.object(health_handlers.ad_service, "get_locked_users", return_value=locked_many), \
             patch.object(health_handlers.ad_service, "get_expiring_passwords", return_value=exp_many), \
             patch.object(health_handlers.ad_service, "get_stale_computers", return_value=stale_many), \
             patch.object(health_handlers, "edit_rich", new_callable=AsyncMock) as mock_edit:
            fake_cb.data = "health:locked:1"
            asyncio.run(health_handlers.show_locked_users(fake_cb))
            fake_cb.data = "health:expiring:1"
            asyncio.run(health_handlers.show_expiring_passwords(fake_cb))
            fake_cb.data = "health:stale:1"
            asyncio.run(health_handlers.show_stale_pcs(fake_cb))
            self.assertEqual(mock_edit.await_count, 3)

    def test_pc_background_worker_circuit_breaker(self):
        from services import pc_background_worker

        pcs = [f"PC{i:02d}.kdb.kz" for i in range(10)]
        with patch.object(pc_background_worker.ad_service, "get_target_computers", return_value=pcs), \
             patch.object(pc_background_worker, "check_pc_online", return_value=True), \
             patch.object(pc_background_worker, "get_pc_logged_user_detailed", return_value={"username": None, "source": "none", "updated_at": None, "error_code": "auth_denied"}) as mock_probe:
            stats = asyncio.run(pc_background_worker.run_pc_index_cycle(max_concurrency=1))
            self.assertTrue(stats["aborted"])
            self.assertLessEqual(mock_probe.call_count, 3)

    def test_edit_rich_ignores_not_modified(self):
        from aiogram.exceptions import TelegramBadRequest
        from services import rich_message_service

        fake_msg = MagicMock()
        fake_msg.edit_text = AsyncMock(
            side_effect=TelegramBadRequest(method=MagicMock(), message="Bad Request: message is not modified")
        )
        res = asyncio.run(rich_message_service.edit_rich(fake_msg, "<b>Same</b>"))
        self.assertIs(res, fake_msg)

        fake_bot = MagicMock()
        fake_bot.edit_message_text = AsyncMock(
            side_effect=TelegramBadRequest(method=MagicMock(), message="Bad Request: message is not modified")
        )
        res2 = asyncio.run(rich_message_service.edit_rich_by_ids(fake_bot, 123, 456, "<b>Same</b>"))
        self.assertIsNone(res2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
