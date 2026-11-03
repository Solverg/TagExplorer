from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from core import file_ops
from core.file_ops import (BatchRenameError, FileIdentity, FileOperationError,
                           FilenameValidationError, RenameOperation)
from core.scanner import FileScanner
from ui.file_list import FileListWidget
from ui.file_tile import FileTileWidget
from ui.main_window import ScanWorker


class DataIntegrityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_rollback_refuses_replacement_and_reports_both_paths(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = root / 'first', root / 'second'
            first.mkdir(); second.mkdir()
            (first / 'original').write_text('safe')
            moved = root / 'first [tag]'
            ops = [RenameOperation(first, moved, None, True), RenameOperation(second, root / 'second [tag]', None, True)]
            actual = file_ops.safe_rename
            def sync_and_fail(op):
                if op.source == second:
                    moved.rename(root / 'moved-by-sync')
                    moved.mkdir()
                    (moved / 'replacement').write_text('other')
                    raise FileOperationError('simulated failure')
                return actual(op)
            with patch('core.file_ops.safe_rename', side_effect=sync_and_fail):
                with self.assertRaises(BatchRenameError) as caught:
                    file_ops.execute_rename_plan(ops)
            self.assertTrue((moved / 'replacement').exists())
            self.assertTrue((root / 'moved-by-sync' / 'original').exists())
            self.assertFalse(first.exists())
            self.assertIn(str(moved), caught.exception.rollback_errors[0])
            self.assertIn(str(first), caught.exception.rollback_errors[0])

    def test_postcheck_failure_rolls_back_or_reports_location(self):
        for always_fail in (False, True):
            with self.subTest(always_fail=always_fail), TemporaryDirectory() as tmp:
                root = Path(tmp)
                source, target = root / 'folder', root / 'folder [tag]'
                source.mkdir()
                (source / 'data').write_text('safe')
                actual = file_ops.ensure_current_directory
                failed = False
                def check(path, expected=None):
                    nonlocal failed
                    if path == target and (always_fail or not failed):
                        failed = True
                        raise FileOperationError('simulated verification failure')
                    return actual(path, expected)
                with patch('core.file_ops.ensure_current_directory', side_effect=check):
                    with self.assertRaises(BatchRenameError) as caught:
                        file_ops.execute_rename_plan([RenameOperation(source, target, None, True)])
                if always_fail:
                    self.assertTrue((target / 'data').exists())
                    self.assertIn(str(target), caught.exception.rollback_errors[0])
                else:
                    self.assertTrue((source / 'data').exists())
                    self.assertIn('restored', str(caught.exception))
                    self.assertEqual(caught.exception.rollback_errors, [])

    def test_validation_error_during_batch_rolls_back_previous(self):
        if os.name != 'nt':
            self.skipTest('Windows case-only rename')
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = root / 'first', root / 'second'
            first.mkdir(); second.mkdir()
            ops = [RenameOperation(first, root / 'first [tag]', None, True),
                   RenameOperation(second, root / 'SECOND', None, True)]
            with patch('core.file_ops._temporary_sibling_path', side_effect=FilenameValidationError('long path')):
                with self.assertRaises(BatchRenameError):
                    file_ops.execute_rename_plan(ops)
            self.assertTrue(first.is_dir())
            self.assertTrue(second.is_dir())
            self.assertFalse((root / 'first [tag]').exists())

    def test_case_only_double_failure_reports_temporary_path(self):
        if os.name != 'nt':
            self.skipTest('Windows case-only rename')
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'folder'
            source.mkdir()
            (source / 'data').write_text('safe')
            real_rename = os.rename
            calls = 0
            def fail_later(a, b):
                nonlocal calls
                calls += 1
                if calls >= 2:
                    raise PermissionError('simulated failure')
                return real_rename(a, b)
            with patch('core.file_ops.os.rename', side_effect=fail_later):
                with self.assertRaises(FileOperationError) as caught:
                    file_ops.safe_rename(RenameOperation(source, root / 'FOLDER', None, True))
            stranded = list(root.glob('.texplorer-rename-*.tmp'))
            self.assertEqual(len(stranded), 1)
            self.assertTrue((stranded[0] / 'data').exists())
            self.assertIn(str(stranded[0]), str(caught.exception))

    def test_rescan_during_tag_dialog_does_not_rename_replacement(self):
        for cls, module in ((FileListWidget, 'ui.file_list'), (FileTileWidget, 'ui.file_tile')):
            with self.subTest(view=cls.__name__), TemporaryDirectory() as tmp, patch('ui.file_tile.FileCache'):
                root = Path(tmp)
                source = root / 'folder [old]'
                source.mkdir()
                (source / 'original').write_text('safe')
                record = sum(FileScanner().iter_scan_folders(root), [])[0]
                widget = cls()
                widget.set_folder_mode(True)
                if cls is FileListWidget:
                    widget.set_files([record], [])
                    widget.tree.expand(widget.model.index(0, 0))
                    widget.tree.setCurrentIndex(widget.model.index(0, 0, widget.model.index(0, 0)))
                else:
                    widget.set_files_batched([record], [])
                    self.app.processEvents()
                    widget._sec_tagged.list_view.setCurrentIndex(widget._sec_tagged.model.index(0, 0))
                def changed_during_dialog():
                    source.rename(root / 'moved')
                    source.mkdir()
                    (source / 'replacement').write_text('other')
                    if cls is FileListWidget:
                        widget.set_files([], [])
                    else:
                        widget.set_files_batched([], [])
                    return QDialog.DialogCode.Accepted
                with patch(module + '.TagEditDialog') as dialog, patch(module + '.QMessageBox.warning') as warning:
                    dialog.return_value.exec.side_effect = changed_during_dialog
                    dialog.return_value.get_actions.return_value = ({'new'}, set())
                    widget._edit_tags()
                    warning.assert_called_once()
                self.assertTrue((source / 'replacement').exists())
                self.assertTrue((root / 'moved' / 'original').exists())
                self.assertFalse((root / 'folder [new][old]').exists())
                widget.close()
                self.app.processEvents()
                widget.deleteLater()
                self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.app.processEvents()

    def test_delete_confirmation_keeps_original_identity(self):
        for cls, module in ((FileListWidget, 'ui.file_list'), (FileTileWidget, 'ui.file_tile')):
            with self.subTest(view=cls.__name__), TemporaryDirectory() as tmp, patch('ui.file_tile.FileCache'):
                root = Path(tmp)
                source = root / 'file.txt'
                source.write_text('original')
                record = FileScanner().scan_directory(root)[0]
                widget = cls()
                if cls is FileListWidget:
                    widget.set_files([], [record])
                    widget.tree.expand(widget.model.index(1, 0))
                    widget.tree.setCurrentIndex(widget.model.index(0, 0, widget.model.index(1, 0)))
                else:
                    widget.set_files_batched([], [record])
                    self.app.processEvents()
                    widget._sec_untagged.list_view.setCurrentIndex(widget._sec_untagged.model.index(0, 0))
                def replaced(*args, **kwargs):
                    source.rename(root / 'moved.txt')
                    source.write_text('replacement with different content')
                    if cls is FileListWidget:
                        widget.set_files([], [])
                    else:
                        widget.set_files_batched([], [])
                    return QMessageBox.StandardButton.Yes
                # Do not delete even inside the disposable tree; exercise the identity check only.
                def check_only(path, expected):
                    file_ops.ensure_current_regular_file(path, expected)
                    raise AssertionError('replacement passed identity check')
                with patch(module + '.QMessageBox.question', side_effect=replaced), \
                     patch(module + '.QMessageBox.warning') as warning, \
                     patch(module + '.safe_delete', side_effect=check_only):
                    widget._delete_files()
                    warning.assert_called_once()
                self.assertEqual(source.read_text(), 'replacement with different content')
                self.assertEqual((root / 'moved.txt').read_text(), 'original')
                widget.close()
                self.app.processEvents()
                widget.deleteLater()
                self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.app.processEvents()

    def test_rename_dialog_keeps_original_identity(self):
        for cls, module in ((FileListWidget, 'ui.file_list'), (FileTileWidget, 'ui.file_tile')):
            with self.subTest(view=cls.__name__), TemporaryDirectory() as tmp, patch('ui.file_tile.FileCache'):
                root = Path(tmp)
                source = root / 'folder'
                source.mkdir()
                (source / 'original').write_text('safe')
                record = sum(FileScanner().iter_scan_folders(root), [])[0]
                widget = cls()
                widget.set_folder_mode(True)
                if cls is FileListWidget:
                    widget.set_files([], [record])
                    widget.tree.expand(widget.model.index(1, 0))
                    widget.tree.setCurrentIndex(widget.model.index(0, 0, widget.model.index(1, 0)))
                else:
                    widget.set_files_batched([], [record])
                    self.app.processEvents()
                    widget._sec_untagged.list_view.setCurrentIndex(widget._sec_untagged.model.index(0, 0))
                def replaced(*args, **kwargs):
                    source.rename(root / 'moved')
                    source.mkdir()
                    (source / 'replacement').write_text('other')
                    if cls is FileListWidget:
                        widget.set_files([], [])
                    else:
                        widget.set_files_batched([], [])
                    return 'new name', True
                with patch(module + '.QInputDialog.getText', side_effect=replaced), \
                     patch(module + '.QMessageBox.warning') as warning:
                    widget._rename_files()
                    warning.assert_called_once()
                self.assertTrue((source / 'replacement').exists())
                self.assertTrue((root / 'moved' / 'original').exists())
                self.assertFalse((root / 'new name').exists())
                widget.close()
                self.app.processEvents()
                widget.deleteLater()
                self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.app.processEvents()

    def test_collision_confirmation_keeps_original_identity(self):
        for cls, module in ((FileListWidget, 'ui.file_list'), (FileTileWidget, 'ui.file_tile')):
            with self.subTest(view=cls.__name__), TemporaryDirectory() as tmp, patch('ui.file_tile.FileCache'):
                root = Path(tmp)
                source, collision = root / 'folder', root / 'taken'
                source.mkdir(); collision.mkdir()
                (source / 'original').write_text('safe')
                record = next(r for r in sum(FileScanner().iter_scan_folders(root), []) if r['filename'] == 'folder')
                widget = cls()
                widget.set_folder_mode(True)
                if cls is FileListWidget:
                    widget.set_files([], [record])
                    widget.tree.expand(widget.model.index(1, 0))
                    widget.tree.setCurrentIndex(widget.model.index(0, 0, widget.model.index(1, 0)))
                else:
                    widget.set_files_batched([], [record])
                    self.app.processEvents()
                    widget._sec_untagged.list_view.setCurrentIndex(widget._sec_untagged.model.index(0, 0))
                def replaced(*args, **kwargs):
                    source.rename(root / 'moved')
                    source.mkdir()
                    (source / 'replacement').write_text('other')
                    return QMessageBox.StandardButton.Yes
                with patch(module + '.QInputDialog.getText', return_value=('taken', True)), \
                     patch(module + '.QMessageBox.question', side_effect=replaced), \
                     patch(module + '.QMessageBox.warning') as warning:
                    widget._rename_files()
                    warning.assert_called_once()
                self.assertTrue((source / 'replacement').exists())
                self.assertTrue((root / 'moved' / 'original').exists())
                self.assertFalse((root / 'taken (2)').exists())
                widget.close()
                self.app.processEvents()
                widget.deleteLater()
                self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.app.processEvents()

    def test_inaccessible_folder_produces_visible_warning_signal(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'real').mkdir()
            worker = ScanWorker(7, root, folder_mode=True)
            results, warnings = [], []
            worker.finished_scan.connect(lambda *args: results.append(args))
            worker.folder_scan_warnings.connect(lambda *args: warnings.append(args))
            with patch('core.scanner.os.scandir', side_effect=PermissionError('denied')):
                worker.run()
            self.assertEqual(results[0][2], [])
            self.assertEqual(len(warnings), 1)
            self.assertEqual(warnings[0][0], 7)
            self.assertIn('denied', warnings[0][2][0])
            self.assertTrue((root / 'real').is_dir())

    def test_recursive_scan_reports_inaccessible_subfolder(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            blocked = root / 'blocked [tag]'
            (blocked / 'child').mkdir(parents=True)
            actual_scandir = os.scandir
            def deny_subfolder(path):
                if Path(path) == blocked:
                    raise PermissionError('simulated access denied')
                return actual_scandir(path)
            worker = ScanWorker(9, root, recursive_scan=True, folder_mode=True)
            results, warnings = [], []
            worker.finished_scan.connect(lambda *args: results.append(args))
            worker.folder_scan_warnings.connect(lambda *args: warnings.append(args))
            with patch('core.scanner.os.scandir', side_effect=deny_subfolder):
                worker.run()
            self.assertEqual([r['filename'] for r in results[0][2]], ['blocked [tag]'])
            self.assertIn(str(blocked), warnings[0][2][0])
            self.assertTrue((blocked / 'child').is_dir())


if __name__ == '__main__':
    unittest.main()
