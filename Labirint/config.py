"""Диалог настроек плагина Labirint.ru Metadata Source."""

from qt.core import (
    QDialog, QDialogButtonBox, QVBoxLayout, QSpinBox,
    QFormLayout, QLabel,
)

from calibre.utils.config import JSONConfig

prefs = JSONConfig('plugins/labirint_metadata')
prefs.defaults['delay_seconds'] = 1
prefs.defaults['max_results'] = 5
prefs.defaults['timeout'] = 30


def save_settings(settings):
    for k, v in settings.items():
        prefs[k] = v


class ConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Настройки источника Labirint.ru')
        self.setMinimumWidth(400)

        self.settings = get_settings()
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 30)
        self.delay_spin.setSuffix(' сек')
        self.delay_spin.setValue(self.settings['delay_seconds'])
        self.delay_spin.setToolTip('Защита от блокировки')
        form.addRow('Задержка между запросами:', self.delay_spin)

        self.max_results_spin = QSpinBox()
        self.max_results_spin.setRange(1, 20)
        self.max_results_spin.setValue(self.settings['max_results'])
        form.addRow('Макс. результатов:', self.max_results_spin)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(10, 120)
        self.timeout_spin.setSuffix(' сек')
        self.timeout_spin.setValue(self.settings['timeout'])
        form.addRow('Таймаут запроса:', self.timeout_spin)

        hint = QLabel(
            '<b>Labirint.ru</b> — книжный интернет-магазин.<br><br>'
            'Плагин ищет метаданные книг по названию, автору или ISBN.'
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
        self.settings['max_results'] = self.max_results_spin.value()
        self.settings['timeout'] = self.timeout_spin.value()
        save_settings(self.settings)
        super().accept()
