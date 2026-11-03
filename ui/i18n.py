"""Application language selection and Russian UI strings."""
from __future__ import annotations

import re
from string import Formatter
from typing import Literal

from PyQt6.QtCore import QLibraryInfo, QSettings, QTranslator
from PyQt6.QtWidgets import QApplication, QLabel, QAbstractButton, QLineEdit, QWidget
from PyQt6.QtGui import QAction

Language = Literal['en', 'ru']
LANGUAGE_KEY = 'appearance/language'
_language: Language = 'en'
_qt_translator: QTranslator | None = None

RU = {
    'File': 'Файл', 'View': 'Вид', 'Options': 'Настройки', 'Help': 'Справка',
    'Language': 'Язык', 'English': 'English', 'Russian': 'Русский',
    'Open Folder': 'Открыть папку', 'Refresh': 'Обновить', 'List': 'Список', 'Tiles': 'Плитка',
    'Theme': 'Тема', 'Dark': 'Тёмная', 'Light': 'Светлая',
    'Tags at the beginning': 'Теги в начале имени', 'Check for Updates': 'Проверить обновления', 'About': 'О программе',
    'Scan Folder Recursively': 'Сканировать вложенные папки', 'Rescan Current Folder': 'Обновить текущую папку',
    'Rescan Recursive Folder': 'Обновить с вложенными папками', 'Recursive mode': 'Включая вложенные папки',
    'Scan selected folder and all subfolders': 'Сканировать выбранную папку и все вложенные папки',
    'Toggle recursive folder scanning': 'Включить или выключить поиск во вложенных папках',
    'File tags': 'Теги файлов', 'Folder tags': 'Теги папок',
    'Switch between file tags and folder tags': 'Переключить режим тегов файлов и папок',
    'Tag filter mode:': 'Режим фильтра:', 'Clear all': 'Сбросить', 'Include:': 'Включить:', 'Exclude:': 'Исключить:',
    'AND': 'И', 'OR': 'ИЛИ',
    'Toggle filter mode: AND / OR': 'Переключить режим фильтра: И / ИЛИ',
    'Click to include tag · click 🚫 to exclude from results': 'Нажмите, чтобы включить тег; нажмите 🚫, чтобы исключить',
    'Remove this filter': 'Убрать этот фильтр', '▶ File types': '▶ Типы файлов', '▼ File types': '▼ Типы файлов',
    'Images': 'Изображения', 'Audio': 'Аудио', 'Video': 'Видео', 'Documents': 'Документы', 'Other': 'Другое',
    'Name': 'Имя', 'Tagged files': 'Файлы с тегами', 'Untagged files': 'Файлы без тегов',
    'Tagged folders': 'Папки с тегами', 'Untagged folders': 'Папки без тегов', 'No files': 'Нет файлов', 'No folders': 'Нет папок',
    'Quick access': 'Быстрый доступ', 'This PC': 'Этот компьютер',
    'Desktop': 'Рабочий стол', 'Downloads': 'Загрузки', 'Pictures': 'Изображения',
    'Music': 'Музыка', 'Movies': 'Видео',
    'Edit tags': 'Изменить теги', 'Rename': 'Переименовать', 'Delete': 'Удалить', 'Open location': 'Открыть расположение',
    'Open file': 'Открыть файл', 'Open folder': 'Открыть папку',
    'Available tags (Click to add/remove):': 'Доступные теги (нажмите, чтобы добавить или убрать):',
    'Enter new tag': 'Введите новый тег', 'Add': 'Добавить', 'No tags selected': 'Теги не выбраны',
    'Will be added to all selected files': 'Будет добавлен ко всем выбранным элементам',
    'Present in some files. Click to apply to all.': 'Есть у части элементов. Нажмите, чтобы применить ко всем.',
    'Click to add to all selected files': 'Нажмите, чтобы добавить ко всем выбранным элементам',
    'Invalid tag': 'Недопустимый тег', 'Please select one or more items to edit tags.': 'Выберите один или несколько элементов для изменения тегов.',
    'The mode changed. Refresh and try again.': 'Режим изменился. Обновите список и повторите попытку.',
    'Batch edit cancelled': 'Пакетное изменение отменено', 'Batch edit failed': 'Не удалось изменить теги',
    'Please select a single item to rename.': 'Выберите один элемент для переименования.', 'New name:': 'Новое имя:',
    'Tag conflict': 'Конфликт имён', 'Name already exists': 'Имя уже существует',
    'Select a file to preview': 'Выберите файл для просмотра', 'Select a single file to preview': 'Выберите один элемент для просмотра',
    'Loading preview...': 'Загрузка предпросмотра...', 'No preview available': 'Предпросмотр недоступен', 'Unknown': 'Неизвестно',
    'TagExplorer Image Viewer': 'Просмотр изображений TagExplorer', 'No images to display': 'Нет изображений для просмотра',
    'Update Available': 'Доступно обновление', 'Later': 'Позже', 'Update': 'Обновить', 'Close': 'Закрыть',
    'Downloading update...': 'Загрузка обновления...', 'Closing TagExplorer and starting the installer...': 'Закрытие TagExplorer и запуск установщика...',
    'Scan error': 'Ошибка сканирования', 'Scan failed': 'Сканирование не выполнено',
    'Cache security warning': 'Предупреждение о безопасности кэша', 'Cache migration warning': 'Предупреждение о переносе кэша',
    'Incomplete folder scan': 'Сканирование папок не завершено', 'Filter error': 'Ошибка фильтра',
    'Run full recursive rescan for the current folder': 'Повторно сканировать текущую папку и все вложенные папки',
    'Rescan direct items in the current folder': 'Повторно сканировать содержимое текущей папки',
    'Tile view enabled': 'Включён вид плиткой', 'List view enabled': 'Включён вид списком',
    'About TagExplorer': 'О программе TagExplorer', 'Update check already in progress': 'Проверка обновлений уже выполняется',
    'Checking for updates...': 'Проверка обновлений...', 'No Updates Available': 'Обновлений нет',
    'TagExplorer is up to date': 'Установлена последняя версия TagExplorer', 'Update Check Failed': 'Ошибка проверки обновлений',
    'Update check failed': 'Не удалось проверить обновления', 'Waiting for scan to stop before closing': 'Ожидание завершения сканирования перед закрытием',
    'Explorer did not respond in time.': 'Проводник не ответил вовремя.',
    'Folder': 'Папка', 'File item': 'Файл',
    'Selected folders: {count}': 'Выбрано папок: {count}', 'Selected files: {count}': 'Выбрано файлов: {count}',
    'Folder: {name}': 'Папка: {name}', 'File: {name}': 'Файл: {name}',
    '{tag} (mixed)': '{tag} (частично)',
    'Scan failed: {error}': 'Ошибка сканирования: {error}',
    'Waiting for previous scan to stop: {folder}': 'Ожидание завершения предыдущего сканирования: {folder}',
    'Scanning: {folder}': 'Сканирование: {folder}',
    'Could not scan folder:\n{message}': 'Не удалось сканировать папку:\n{message}',
    'Could not apply filters:\n{error}': 'Не удалось применить фильтры:\n{error}',
    'Displayed {displayed} of {total} folders in {folder}': 'Показано папок: {displayed} из {total} в {folder}',
    'Displayed {displayed} of {total} files in {folder}': 'Показано файлов: {displayed} из {total} в {folder}',
    'Scan mode: recursive': 'Сканирование: включая вложенные папки', 'Scan mode: standard': 'Сканирование: текущая папка',
    'Dark theme enabled': 'Включена тёмная тема', 'Light theme enabled': 'Включена светлая тема',
    'Update available: {version}': 'Доступно обновление: {version}',
    'TagExplorer is up to date.{suffix}': 'Установлена последняя версия TagExplorer.{suffix}',
    ' Latest release: {version}.': ' Последний выпуск: {version}.',
    'Some folders could not be scanned. Results may be incomplete.\n\n': 'Не удалось прочитать некоторые папки. Результаты могут быть неполными.\n\n',
    '\n... and {count} more issue(s).': '\n... и ещё ошибок: {count}.',
    '\n... and {count} more file(s).': '\n... и ещё элементов: {count}.',
    'Delete selected file(s)?\n\n{names}{more}': 'Удалить выбранные файлы?\n\n{names}{more}',
    'Applying tags creates a conflict: \'{target}\' already exists.\n\nSave as \'{suggested}\'?': 'Невозможно применить теги: «{target}» уже существует.\n\nСохранить как «{suggested}»?',
    'An item named \'{target}\' already exists.\n\nWould you like to rename it to \'{suggested}\' instead?': 'Элемент «{target}» уже существует.\n\nПереименовать его в «{suggested}»?',
    'Unable to open file:\n{error}': 'Не удалось открыть файл:\n{error}',
    'Unable to open file location:\n{error}': 'Не удалось открыть расположение:\n{error}',
    '{details}\n\nContinue opening?\n\n{path}': '{details}\n\nПродолжить открытие?\n\n{path}',
    'Viewer - {name} ({index} of {total})': 'Просмотр — {name} ({index} из {total})',
    '<b>TagExplorer {version} is available.</b><br><br>Install the update now?': '<b>Доступен TagExplorer {version}.</b><br><br>Установить обновление сейчас?',
    'Update failed: {error}': 'Ошибка обновления: {error}',
    'Update downloaded to:<br>{path}<br><br>Development mode does not replace the running app.': 'Обновление загружено в:<br>{path}<br><br>В режиме разработки работающее приложение не заменяется.',
    'TagExplorer {version}\n\nA desktop explorer that filters files and folders by name tags.': 'TagExplorer {version}\n\nПроводник для фильтрации файлов и папок по тегам в именах.',
    'Name: {name}\nPath: {path}\nSize: {size}\nModified: {modified}{duration}': 'Имя: {name}\nПуть: {path}\nРазмер: {size}\nИзменён: {modified}{duration}',
    '\nDuration: {seconds} s': '\nДлительность: {seconds} с', '{size} bytes': '{size} байт',
    'Rollback warnings:': 'Предупреждения при восстановлении:',
    'SQLCipher is unavailable; TagExplorer cache will be stored unencrypted.': 'SQLCipher недоступен; кэш TagExplorer будет храниться без шифрования.',
    'This file is a shortcut or URL and may open a different local, network, or web target.': 'Этот файл является ярлыком или ссылкой и может открыть другой локальный, сетевой или интернет-ресурс.',
    'This file type can run programs or scripts.': 'Этот тип файлов может запускать программы или сценарии.',
    'This file is on a network path and opening it may contact that host.': 'Файл находится в сети; при его открытии может произойти подключение к удалённому компьютеру.',
    'This location is on a network share and may contact that host.': 'Расположение находится в сети; может произойти подключение к удалённому компьютеру.',
    'Tag cannot be empty.': 'Тег не может быть пустым.',
    'Tags cannot include square brackets.': 'Тег не может содержать квадратные скобки.',
    'Filename cannot be empty.': 'Имя не может быть пустым.',
    "Filename cannot be '.' or '..'.": 'Имя не может состоять из одной или двух точек.',
    'Filename cannot contain control characters.': 'Имя не может содержать управляющие символы.',
    'Filename cannot end with a space or dot.': 'Имя не может оканчиваться пробелом или точкой.',
    'Filename cannot contain: {characters}': 'Имя не может содержать: {characters}',
    "'{name}' is a reserved Windows name.": '«{name}» — зарезервированное имя Windows.',
    'Refusing to operate on a symbolic link.': 'Операция с символической ссылкой запрещена.',
    'Refusing to operate on a Windows reparse point.': 'Операция с точкой повторного анализа Windows запрещена.',
    'Selected path is no longer a regular file.': 'Выбранный путь больше не указывает на обычный файл.',
    'Selected path is no longer a safe folder.': 'Выбранный путь больше не указывает на безопасную папку.',
    'File changed since it was scanned. Refresh and try again.': 'Файл изменился после сканирования. Обновите список и повторите попытку.',
    'Folder changed since it was scanned. Refresh and try again.': 'Папка изменилась после сканирования. Обновите список и повторите попытку.',
    'Cannot edit a folder and its descendant together. Edit them separately.': 'Нельзя одновременно изменить папку и вложенную в неё папку. Измените их отдельно.',
    'Completed changes were restored to their original names.': 'Исходные имена изменённых элементов восстановлены.',
    'File is no longer available: {error}': 'Файл больше недоступен: {error}',
    'Folder is no longer available: {error}': 'Папка больше недоступна: {error}',
    'Could not rename file: {error}': 'Не удалось переименовать элемент: {error}',
    'Could not delete file: {error}': 'Не удалось удалить файл: {error}',
    "Rename failed: {error}. Could not restore the original name: {restore_error}. The item may remain at '{path}'; check that location before retrying.": "Ошибка переименования: {error}. Не удалось восстановить исходное имя: {restore_error}. Элемент может находиться по пути «{path}»; проверьте его перед повторной попыткой.",
    "Could not check target '{name}': {error}": 'Не удалось проверить имя «{name}»: {error}',
    'Could not allocate a temporary rename path.': 'Не удалось подобрать временное имя для переименования.',
    'Path is too long for Windows shell operations ({count} characters).': 'Путь слишком длинный для операций Windows (символов: {count}).',
    'Could not migrate legacy cache {path}: {error}': 'Не удалось перенести старый кэш {path}: {error}',
    "A file named '{name}' already exists.": 'Элемент «{name}» уже существует.',
    "Cannot scan '{path}': folder is unavailable or is a link.": 'Не удалось сканировать «{path}»: папка недоступна или является ссылкой.',
    "Cannot scan '{path}': {error}": 'Не удалось сканировать «{path}»: {error}',
    "Cannot inspect '{path}': {error}": 'Не удалось проверить «{path}»: {error}',
    "Could not restore '{target}' to '{source}': {error}": 'Не удалось восстановить имя «{source}» для «{target}»: {error}',
    "Rename completed from '{source}' to '{target}', but the result could not be verified: {error}": 'Элемент переименован из «{source}» в «{target}», но проверить результат не удалось: {error}',
    'Selection is no longer available: {path}. Refresh and try again.': 'Выбранный элемент больше недоступен: {path}. Обновите список и повторите попытку.',
    'Selection cannot be verified: {path}. Refresh and try again.': 'Не удалось проверить выбранный элемент: {path}. Обновите список и повторите попытку.',
    'Rename cancelled because the target already exists.': 'Переименование отменено: элемент с таким именем уже существует.',
}


def normalize_language(value: object) -> Language:
    return 'ru' if str(value).lower() == 'ru' else 'en'


def load_language(settings: QSettings | None = None) -> Language:
    settings = settings if settings is not None else QSettings()
    return normalize_language(settings.value(LANGUAGE_KEY, 'en'))


def set_language(value: str, *, persist: bool = False, settings: QSettings | None = None) -> None:
    global _language, _qt_translator
    _language = normalize_language(value)
    app = QApplication.instance()
    if isinstance(app, QApplication):
        if _qt_translator is not None:
            app.removeTranslator(_qt_translator)
            _qt_translator = None
        if _language == 'ru':
            translator = QTranslator(app)
            directory = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
            if translator.load('qtbase_ru', directory):
                app.installTranslator(translator)
                _qt_translator = translator
    if persist:
        target = settings if settings is not None else QSettings()
        target.setValue(LANGUAGE_KEY, _language)


def language() -> Language:
    return _language


def tr(source: str, **values: object) -> str:
    text = RU.get(source, source) if _language == 'ru' else source
    return text.format(**values) if values else text


def translate_detail(message: object) -> str:
    """Translate known diagnostics while preserving paths and OS error details."""
    text = str(message)
    if _language != 'ru':
        return text
    if text in RU:
        return RU[text]
    for source, translated in RU.items():
        fields = list(Formatter().parse(source))
        names = [field for _, field, _, _ in fields if field]
        if not names or len(set(names)) != len(names):
            continue
        pattern = ''.join(re.escape(literal) + (f'(?P<{field}>.*?)' if field else '')
                          for literal, field, _, _ in fields)
        match = re.fullmatch(pattern, text, flags=re.DOTALL)
        if match:
            values = match.groupdict()
            if 'error' in values:
                values['error'] = translate_detail(values['error'])
            return translated.format(**values)
    if '\n' in text:
        return '\n'.join(translate_detail(line) for line in text.split('\n'))
    return text


def bind_static_text(root: QWidget) -> None:
    """Bind only initial static widgets; never translate user names or tags."""
    for widget in [root, *root.findChildren(QWidget)]:
        if isinstance(widget, (QLabel, QAbstractButton)):
            source = widget.text()
            if source in RU:
                widget.setProperty('_language_text', source)
        tooltip = widget.toolTip()
        if tooltip in RU:
            widget.setProperty('_language_tooltip', tooltip)
    for action in root.findChildren(QAction):
        if action.text() in RU:
            action.setProperty('_language_text', action.text())
    retranslate_static_text(root)


def retranslate_static_text(root: QWidget) -> None:
    for widget in [root, *root.findChildren(QWidget)]:
        source = widget.property('_language_text')
        if source and isinstance(widget, (QLabel, QAbstractButton)):
            widget.setText(tr(source))
        tooltip = widget.property('_language_tooltip')
        if tooltip:
            widget.setToolTip(tr(tooltip))
    for action in root.findChildren(QAction):
        source = action.property('_language_text')
        if source:
            action.setText(tr(source))
