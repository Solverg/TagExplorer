from pathlib import Path
import os
import subprocess
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from core.file_ops import (BatchRenameError, FileIdentity, FileOperationError,
                           RenameOperation, TargetExistsError, execute_rename_plan,
                           safe_rename)
from core.filter_engine import filter_files
from core.scanner import FileScanner
from ui.file_list import FileListWidget
from ui.file_tile import FileTileWidget
from ui.main_window import MainWindow


class FolderEdgeCasesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_recursive_filters_use_only_own_tags_and_show_duplicate_paths(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('first [a]/same [b]', 'second [a]/same [b]', 'untagged'):
                (root / name).mkdir(parents=True)
            (root / 'untagged' / 'file [a][b].txt').write_text('data')
            records = sum(FileScanner().iter_scan_folders(root, recursive=True), [])
            tagged, _ = filter_files(records, ['a'], 'OR')
            self.assertEqual({r['filename'] for r in tagged}, {'first [a]', 'second [a]'})
            tagged, _ = filter_files(records, ['a', 'b'], 'AND')
            self.assertEqual(tagged, [])
            tagged, untagged = filter_files(records, [], 'OR', excluded_tags=['a'])
            self.assertEqual(len(tagged), 2)
            self.assertEqual([r['filename'] for r in untagged], ['untagged'])
            view = FileListWidget()
            view.set_folder_mode(True)
            view.set_files(tagged, untagged)
            path_tips = {view.model.index(i, 0, view.model.index(0, 0)).data(Qt.ItemDataRole.ToolTipRole)
                         for i in range(2)}
            self.assertEqual(len(path_tips), 2)
            view.close()
            with patch('ui.file_tile.FileCache'):
                tiles = FileTileWidget()
                tiles.set_folder_mode(True)
                tiles.set_files_batched(tagged, untagged)
                self.app.processEvents()
                tips = {tiles._sec_tagged.model.index(i, 0).data(Qt.ItemDataRole.ToolTipRole) for i in range(2)}
                self.assertEqual(tips, path_tips)
                tiles.shutdown()
                tiles.close()

    def test_directory_replaced_after_scan_cannot_be_renamed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'original'
            folder.mkdir()
            record = sum(FileScanner().iter_scan_folders(root), [])[0]
            folder.rename(root / 'moved')
            folder.write_text('replacement file')
            with self.assertRaises(FileOperationError):
                safe_rename(RenameOperation(folder, root / 'new', FileIdentity.from_record(record), True))
            self.assertTrue(folder.is_file())
            self.assertTrue((root / 'moved').is_dir())

    def test_rename_rejects_file_collision_and_handles_case_change(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'project'
            folder.mkdir()
            (folder / 'contents').write_text('safe')
            (root / 'collision').write_text('other')
            with self.assertRaises(TargetExistsError):
                safe_rename(RenameOperation(folder, root / 'collision', None, True))
            changed = root / 'PROJECT'
            safe_rename(RenameOperation(folder, changed, None, True))
            self.assertEqual((changed / 'contents').read_text(), 'safe')
            self.assertEqual((root / 'collision').read_text(), 'other')

    def test_real_directory_link_is_not_scanned(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / 'root'
            root.mkdir()
            target = base / 'target'
            (target / 'child [outside]').mkdir(parents=True)
            link = root / 'link [ignore]'
            if os.name == 'nt':
                result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                                        capture_output=True, check=False)
                if result.returncode:
                    self.skipTest('Could not create a temporary directory junction')
            else:
                os.symlink(target, link, target_is_directory=True)
            self.assertEqual(list(FileScanner().iter_scan_folders(root, recursive=True)), [])

    def test_second_preflight_failure_is_reported_as_batch_error(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'folder'
            folder.mkdir()
            identity = FileIdentity.from_stat(folder.lstat())
            (folder / 'new file').write_text('changed after scan')
            os.utime(folder, ns=(identity.modified_time_ns + 5_000_000_000,
                                 identity.modified_time_ns + 5_000_000_000))
            op = RenameOperation(folder, root / 'renamed', identity, True)
            with self.assertRaises(BatchRenameError) as caught:
                execute_rename_plan([op])
            self.assertIs(caught.exception.failed_operation, op)
            self.assertTrue(folder.exists())

    def test_failed_second_rename_rolls_back_first(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = root / 'one', root / 'two'
            first.mkdir()
            second.mkdir()
            (first / 'data').write_text('safe')
            ops = [RenameOperation(first, root / 'one [tag]', None, True),
                   RenameOperation(second, root / 'two [tag]', None, True)]
            def fail_second(op):
                if op.source == second:
                    raise FileOperationError('simulated failure')
                return safe_rename(op)
            with patch('core.file_ops.safe_rename', side_effect=fail_second):
                with self.assertRaises(BatchRenameError) as caught:
                    execute_rename_plan(ops)
            self.assertEqual(caught.exception.rollback_errors, [])
            self.assertEqual((first / 'data').read_text(), 'safe')
            self.assertTrue(second.is_dir())
            self.assertFalse((root / 'one [tag]').exists())

    def test_cancel_does_not_emit_partial_final_batch(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(5):
                (root / str(i)).mkdir()
            calls = 0
            def cancelled():
                nonlocal calls
                calls += 1
                return calls > 3
            self.assertEqual(list(FileScanner().iter_scan_folders(root, should_cancel=cancelled)), [])

    def test_navigation_clears_old_results_before_worker_completes(self):
        with TemporaryDirectory() as tmp, patch('ui.main_window.FileCache') as cache, \
             patch('ui.file_tile.FileCache'), patch.object(MainWindow, 'refresh_folder'), \
             patch.object(MainWindow, '_show_cache_warnings'):
            cache.return_value.security_warnings = []
            window = MainWindow()
            window.folder_mode_switch.setChecked(True)
            old = {'filename': 'Old [tag]', 'path': str(Path(tmp) / 'Old [tag]'), 'tags': ['tag']}
            window.all_records = [old]
            window.tag_panel.set_tags(['tag'])
            window._apply_filter()
            window._set_current_folder(Path(tmp), update_navigation_root=True)
            self.assertEqual(window.all_records, [])
            self.assertEqual(window.file_list.model.tagged_records, [])
            self.assertEqual(window.tag_panel.selected_tags(), [])
            window.close()

    def test_finished_worker_is_not_replaced_before_its_queued_signal(self):
        real_refresh = MainWindow.refresh_folder
        with patch('ui.main_window.FileCache') as cache, patch('ui.file_tile.FileCache'), \
             patch.object(MainWindow, 'refresh_folder'), patch.object(MainWindow, '_show_cache_warnings'):
            cache.return_value.security_warnings = []
            window = MainWindow()
            worker = Mock()
            worker.isRunning.return_value = False
            window.scan_worker = worker
            with patch.object(window, '_start_scan') as start:
                real_refresh(window, False)
                real_refresh(window, True)
                start.assert_not_called()
                self.assertEqual(window._pending_scan[0], window._scan_generation)
                self.assertTrue(window._pending_scan[2])
                window._on_scan_worker_finished(window._scan_generation - 1, worker)
                start.assert_called_once()
                self.assertEqual(start.call_args.args[0], window._scan_generation)
            window.close()

    def test_real_workers_handle_rapid_switches_and_navigation(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / 'root'
            folder = root / 'parent [folder]'
            (folder / 'child [nested]').mkdir(parents=True)
            (folder / 'inside [filetag].txt').write_text('data')
            (root / 'direct [filetag].txt').write_text('data')
            with patch('core.cache._get_db_path', return_value=base / 'cache.db'), \
                 patch('core.cache._restrict_path_permissions'), \
                 patch.object(MainWindow, '_show_cache_warnings'), \
                 patch.object(MainWindow, 'refresh_folder'):
                window = MainWindow()
            # Patches used by worker threads remain active through shutdown.
            try:
                with patch('core.cache._get_db_path', return_value=base / 'cache.db'), \
                     patch('core.cache._restrict_path_permissions'), \
                     patch('core.cache.FileCache.migrate_legacy_scan_caches', return_value=[]):
                    window.current_folder = root
                    window.folder_mode_switch.setChecked(True)
                    window.recursive_scan_switch.setChecked(True)
                    window.folder_mode_switch.setChecked(False)
                    window.folder_mode_switch.setChecked(True)
                    deadline = time.monotonic() + 10
                    while (window.scan_worker is not None or window._filter_timer.isActive()) and time.monotonic() < deadline:
                        QTest.qWait(10)
                    self.assertIsNone(window.scan_worker, 'scan did not finish')
                    self.assertEqual({r['filename'] for r in window.all_records}, {'parent [folder]', 'child [nested]'})
                    self.assertEqual(set(window.tag_panel._tag_buttons), {'folder', 'nested'})
                    window._set_current_folder(folder, update_navigation_root=True)
                    self.assertEqual(window.all_records, [])
                    deadline = time.monotonic() + 10
                    while (window.scan_worker is not None or window._filter_timer.isActive()) and time.monotonic() < deadline:
                        QTest.qWait(10)
                    self.assertIsNone(window.scan_worker, 'navigation scan did not finish')
                    self.assertEqual({r['filename'] for r in window.all_records}, {'child [nested]'})
                    self.assertEqual(set(window.tag_panel._tag_buttons), {'nested'})
                    window.close()
            finally:
                if not window._closing:
                    window.close()
