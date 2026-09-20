"""Диалог настроек плагина Ozon.ru Metadata Source."""

import os

from qt.core import (
    QDialog, QDialogButtonBox, QVBoxLayout, QSpinBox,
    QCheckBox, QFormLayout, QLabel, QLineEdit,
)

from calibre.gui2 import config as calibre_config

CONFIG_KEY = 'ozon_metadata_settings'

DEFAULTS = {
    'delay_seconds': 1,
    'only_books': True,
    'max_results': 5,
    'timeout': 30,
    'proxy': '',
    'venv_path': '~/.local/share/calibre/ozon_venv',
}


def get_settings():
    raw = calibre_config.get(CONFIG_KEY, {})
    if not isinstance(raw, dict):
        raw = {}
    return {k: raw.get(k, v) for k, v in DEFAULTS.items()}


def save_settings(settings):
    calibre_config[CONFIG_KEY] = settings


class ConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Настройки источника Ozon.ru')
        self.setMinimumWidth(450)

        self.settings = get_settings()
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 30)
        self.delay_spin.setSuffix(' сек')
        self.delay_spin.setValue(self.settings['delay_seconds'])
        self.delay_spin.setToolTip('Защита от блокировки Cloudflare')
        form.addRow('Задержка между запросами:', self.delay_spin)

        self.only_books_cb = QCheckBox('Искать только в категории «Книги»')
        self.only_books_cb.setChecked(self.settings['only_books'])
        form.addRow(self.only_books_cb)

        self.max_results_spin = QSpinBox()
        self.max_results_spin.setRange(1, 20)
        self.max_results_spin.setValue(self.settings['max_results'])
        form.addRow('Макс. результатов:', self.max_results_spin)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(10, 120)
        self.timeout_spin.setSuffix(' сек')
        self.timeout_spin.setValue(self.settings['timeout'])
        form.addRow('Таймаут запроса:', self.timeout_spin)

        self.venv_edit = QLineEdit()
        self.venv_edit.setText(self.settings['venv_path'])
        self.venv_edit.setPlaceholderText(
            '~/.local/share/calibre/ozon_venv'
        )
        self.venv_edit.setToolTip(
            'Путь к виртуальному окружению с установленным curl_cffi'
        )
        form.addRow('Путь к venv:', self.venv_edit)

        self.proxy_edit = QLineEdit()
        self.proxy_edit.setText(self.settings['proxy'])
        self.proxy_edit.setPlaceholderText('http://user:pass@host:port')
        self.proxy_edit.setToolTip('Прокси-сервер для обхода блокировки')
        form.addRow('Прокси (опционально):', self.proxy_edit)

        hint = QLabel(
            '<b>Установка curl_cffi для обхода Cloudflare:</b><br><br>'
            '<b>Gentoo / Arch (PEP 668):</b><br>'
            '<code>python -m venv ~/.local/share/calibre/ozon_venv</code><br>'
            '<code>~/.local/share/calibre/ozon_venv/bin/pip install curl_cffi</code><br><br>'
            '<b>Другие дистрибутивы:</b><br>'
            '<code>pip install curl_cffi</code><br><br>'
            '<b>Запасной вариант:</b><br>'
            '<code>pip install cloudscraper</code> (в тот же venv)'
        )
        hint.setWordWrap(True)
        hint.setStyleSheet('color: #666; padding: 8px;')

        layout.addLayout(form)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        self.settings['delay_seconds'] = self.delay_spin.value()
        self.settings['only_books'] = self.only_books_cb.isChecked()
        self.settings['max_results'] = self.max_results_spin.value()
        self.settings['timeout'] = self.timeout_spin.value()
        self.settings['venv_path'] = os.path.expanduser(
            self.venv_edit.text().strip()
        )
        self.settings['proxy'] = self.proxy_edit.text().strip()
        save_settings(self.settings)
        super().accept()
