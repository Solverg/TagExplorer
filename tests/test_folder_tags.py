from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog

from core.file_ops import BatchRenameError, FileIdentity, RenameOperation, TargetExistsError, execute_rename_plan, safe_rename
from core.filter_engine import filter_files
from core.scanner import FileScanner
from core.tags import build_tagged_filename
from ui.file_list import FileListWidget
from ui.file_tile import FileTileWidget
from ui.main_window import MainWindow, ScanWorker


class FolderTagTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_scans_only_folder_names_in_both_scan_modes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            tagged = root / 'Project.v1 [folder]'
            nested = tagged / 'Nested [child]'
            nested.mkdir(parents=True)
            (root / 'Root [filetag].txt').write_text('x')
            (tagged / 'Inner [innerfile].txt').write_text('x')
            scanner = FileScanner()
            direct = sum(scanner.iter_scan_folders(root), [])
            deep = sum(scanner.iter_scan_folders(root, recursive=True), [])
            self.assertEqual([(r['filename'], r['tags']) for r in direct], [(tagged.name, ['folder'])])
            self.assertEqual({t for r in deep for t in r['tags']}, {'folder', 'child'})
            self.assertEqual(len(filter_files(deep, ['filetag'], 'OR')[0]), 0)
            self.assertEqual(len(filter_files(deep, ['child'], 'AND')[0]), 1)
            worker = ScanWorker(1, root, recursive_scan=True, folder_mode=True)
            result = []
            worker.finished_scan.connect(lambda _id, _path, records: result.extend(records))
            with patch('ui.main_window.FileCache', side_effect=AssertionError('folder scan must not touch file cache')):
                worker.run()
            self.assertEqual({r['filename'] for r in result}, {tagged.name, nested.name})

    def test_directory_names_and_rename_preserve_contents_and_reject_conflicts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'project.v1'
            folder.mkdir()
            (folder / 'data.txt').write_text('content')
            target = root / build_tagged_filename(folder, ['work'], False, is_directory=True)
            self.assertEqual(target.name, 'project.v1 [work]')
            record = next(iter(sum(FileScanner().iter_scan_folders(root), [])))
            safe_rename(RenameOperation(folder, target, FileIdentity.from_record(record), True))
            self.assertEqual((target / 'data.txt').read_text(), 'content')
            collision = root / 'existing'
            collision.mkdir()
            with self.assertRaises(TargetExistsError):
                safe_rename(RenameOperation(target, collision, None, True))
            self.assertTrue((target / 'data.txt').exists())

    def test_nested_batch_is_rejected_before_any_change(self):
        with TemporaryDirectory() as tmp:
            parent = Path(tmp) / 'parent'
            child = parent / 'child'
            child.mkdir(parents=True)
            operations = [RenameOperation(parent, parent.with_name('renamed'), None, True),
                          RenameOperation(child, child.with_name('renamed-child'), None, True)]
            with self.assertRaises(BatchRenameError):
                execute_rename_plan(operations)
            self.assertTrue(child.is_dir())

    def test_both_views_apply_tags_and_open_folder(self):
        for widget_class, dialog_path in ((FileListWidget, 'ui.file_list.TagEditDialog'),
                                          (FileTileWidget, 'ui.file_tile.TagEditDialog')):
            with self.subTest(widget=widget_class.__name__), TemporaryDirectory() as tmp, patch('ui.file_tile.FileCache'):
                folder = Path(tmp) / 'Name.ext'
                folder.mkdir()
                (folder / 'file [ignored].txt').write_text('x')
                record = sum(FileScanner().iter_scan_folders(Path(tmp)), [])[0]
                widget = widget_class()
                widget.set_folder_mode(True)
                if widget_class is FileListWidget:
                    widget.set_files([], [record])
                    widget.tree.expand(widget.model.index(1, 0))
                    index = widget.model.index(0, 0, widget.model.index(1, 0))
                    widget.tree.setCurrentIndex(index)
                    open_folder = lambda: widget._open_file(index)
                else:
                    widget.set_files_batched([], [record])
                    self.app.processEvents()
                    index = widget._sec_untagged.model.index(0, 0)
                    widget._sec_untagged.list_view.setCurrentIndex(index)
                    open_folder = lambda: widget._open_file_by_path(str(folder))
                opened = []
                widget.folder_opened.connect(opened.append)
                open_folder()
                self.assertEqual(opened, [str(folder)])
                with patch(dialog_path) as dialog:
                    dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
                    dialog.return_value.get_actions.return_value = ({'work'}, set())
                    widget._edit_tags()
                self.assertTrue((Path(tmp) / 'Name.ext [work]' / 'file [ignored].txt').exists())
                if widget_class is FileTileWidget:
                    widget.shutdown()
                widget.close()

    def test_switch_clears_prior_results_and_ignores_file_type_filters(self):
        with TemporaryDirectory() as tmp, patch('ui.main_window.FileCache') as cache, \
             patch('ui.file_tile.FileCache'), patch.object(MainWindow, 'refresh_folder'), \
             patch.object(MainWindow, '_show_cache_warnings'):
            cache.return_value.security_warnings = []
            window = MainWindow()
            window.all_records = [{'path': str(Path(tmp) / 'a.txt'), 'filename': 'a.txt', 'tags': ['filetag']}]
            window.tag_panel.set_tags(['filetag'])
            window.tag_panel._on_tag_clicked('filetag')
            window.folder_mode_switch.setChecked(True)
            self.assertEqual(window.all_records, [])
            self.assertEqual(window.tag_panel.selected_tags(), [])
            self.assertTrue(window.file_list.folder_mode)
            self.assertTrue(window.file_tile_widget.folder_mode)
            window.all_records = [{'path': str(Path(tmp) / 'folder'), 'filename': 'folder', 'tags': [], 'file_type': 'folder'}]
            with patch.object(window.type_filter, 'selected_types', return_value=['image']):
                window._apply_filter()
            self.assertEqual(len(window._filtered_untagged), 1)
            window.folder_mode_switch.setChecked(False)
            self.assertFalse(window.file_list.folder_mode)
            window.close()

    def test_switch_during_scan_discards_old_results(self):
        with patch('ui.main_window.FileCache') as cache, patch('ui.file_tile.FileCache'), \
             patch.object(MainWindow, 'refresh_folder'), patch.object(MainWindow, '_show_cache_warnings'):
            cache.return_value.security_warnings = []
            window = MainWindow()
            window._scan_generation = 4
            window.folder_mode = True
            window._on_scan_complete(3, window.current_folder.as_posix(), [
                {'path': 'stale', 'filename': 'file [old].txt', 'tags': ['old']}
            ])
            self.assertEqual(window.all_records, [])
            self.assertEqual(window.tag_panel.selected_tags(), [])
            window.close()


if __name__ == '__main__':
    unittest.main()
