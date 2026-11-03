from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QEvent, QSettings, Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

from ui.i18n import load_language, set_language, tr, translate_detail
from ui.file_list import TagEditDialog
from ui.main_window import MainWindow


class LanguageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        set_language('en')
        self.app.processEvents()

    def test_settings_roundtrip_and_unknown_fallback(self):
        with TemporaryDirectory() as tmp:
            settings = QSettings(str(Path(tmp) / 'settings.ini'), QSettings.Format.IniFormat)
            self.assertEqual(load_language(settings), 'en')
            set_language('ru', persist=True, settings=settings)
            self.assertEqual(load_language(settings), 'ru')
            self.assertEqual(tr('File tags'), 'Теги файлов')
            settings.setValue('appearance/language', 'unknown')
            self.assertEqual(load_language(settings), 'en')

    def test_runtime_switch_preserves_state_and_user_tags(self):
        with TemporaryDirectory() as tmp:
            settings = QSettings(str(Path(tmp) / 'settings.ini'), QSettings.Format.IniFormat)
            with patch('ui.i18n.QSettings', return_value=settings), \
                 patch('ui.main_window.FileCache') as cache, patch('ui.file_tile.FileCache'), \
                 patch.object(MainWindow, 'refresh_folder') as refresh, \
                 patch.object(MainWindow, '_show_cache_warnings'):
                cache.return_value.security_warnings = []
                window = MainWindow()
                window.current_folder = Path(tmp)
                window.folder_mode_switch.setChecked(True)
                record = {'path': str(Path(tmp) / 'File [Delete]'), 'filename': 'File [Delete]',
                          'tags': ['Delete'], 'file_type': 'folder'}
                window.all_records = [record]
                window.tag_panel.set_tags(['Delete'])
                window.tag_panel._on_tag_clicked('Delete')
                window._apply_filter()
                previous_refreshes = refresh.call_count
                window.russian_language_action.trigger()
                self.assertEqual(window._language_name, 'ru')
                self.assertEqual(load_language(settings), 'ru')
                self.assertEqual(window.menuBar().actions()[0].text(), 'Файл')
                self.assertEqual(window.open_folder_button.text(), 'Открыть папку')
                self.assertEqual(window.file_list.model.data(window.file_list.model.index(0, 0)), 'Папки с тегами')
                self.assertEqual(window.tag_panel.selected_tags(), ['Delete'])
                self.assertEqual(window.tag_panel._tag_buttons['Delete'].text(), 'Delete')
                self.assertEqual(window.all_records, [record])
                self.assertEqual(window.current_folder, Path(tmp))
                self.assertEqual(refresh.call_count, previous_refreshes)
                self.assertEqual(translate_detail('Folder changed since it was scanned. Refresh and try again.'),
                                 'Папка изменилась после сканирования. Обновите список и повторите попытку.')
                recovery = "Rename failed: denied. Could not restore the original name: busy. The item may remain at 'C:\\Тест\\temporary'; check that location before retrying."
                translated_recovery = translate_detail(recovery)
                self.assertIn('Не удалось восстановить исходное имя', translated_recovery)
                self.assertIn('C:\\Тест\\temporary', translated_recovery)
                dialog = TagEditDialog('Папка: File', {}, ['Delete'])
                self.assertEqual(dialog.windowTitle(), 'Изменить теги')
                self.assertIn('Delete', dialog.tag_buttons)
                dialog.close()
                dialog.deleteLater()
                window.english_language_action.trigger()
                self.assertEqual(window.open_folder_button.text(), 'Open Folder')
                self.assertEqual(window.tag_panel.selected_tags(), ['Delete'])
                self.assertEqual(window.file_list.model.data(window.file_list.model.index(0, 0)), 'Tagged folders')
                self.assertEqual(load_language(settings), 'en')
                window.close()
                self.app.processEvents()
                window.deleteLater()
                self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.app.processEvents()

    def test_saved_russian_is_applied_on_startup(self):
        with TemporaryDirectory() as tmp:
            settings = QSettings(str(Path(tmp) / 'settings.ini'), QSettings.Format.IniFormat)
            settings.setValue('appearance/language', 'ru')
            with patch('ui.i18n.QSettings', return_value=settings), \
                 patch('ui.main_window.FileCache') as cache, patch('ui.file_tile.FileCache'), \
                 patch.object(MainWindow, 'refresh_folder'), patch.object(MainWindow, '_show_cache_warnings'):
                cache.return_value.security_warnings = []
                window = MainWindow()
                self.assertEqual(window.menuBar().actions()[0].text(), 'Файл')
                self.assertTrue(window.russian_language_action.isChecked())
                self.assertEqual(window.open_folder_button.text(), 'Открыть папку')
                self.assertEqual(window.file_list.model.data(window.file_list.model.index(0, 0)), 'Файлы с тегами')
                window.close()
                self.app.processEvents()
                window.deleteLater()
                self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
