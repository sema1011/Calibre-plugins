# Calibre Plugins

Набор плагинов для Calibre — источники метаданных для популярных российских книжных магазинов.

## Плагины

### Labirint

Источник метаданных для получения информации о книгах с labirint.ru.

**Возможности:**
- Поиск по названию и автору
- Получение аннотации, обложки, ISBN
- Поддержка поиска по ID книги Labirint (`labirint:963343`)
- Автоматическая конвертация обложек WebP → JPEG
- Парсинг JSON-LD данных для полной аннотации

**Установка:**
1. Соберите ZIP: `cd Labirint && zip -r Labirint.zip __init__.py config.py plugin-import-name-Labirint.txt`
2. В Calibre: Настройки → Плагины → Загрузить плагин из файла
3. Выберите созданный `Labirint.zip`
4. Перезапустите Calibre
5. Включите источник в Настройки → Загрузка метаданных

### Litres Metadata

Источник метаданных для получения информации о книгах с ЛитРес (litres.ru).

**Возможности:**
- Поиск по названию, автору, ISBN
- Получение аннотации, обложки, рейтинга
- Извлечение информации о серии
- Поддержка ISBN и внутреннего ID ЛитРес

**Установка:**
1. Соберите ZIP: `cd Litres && zip -r LitRes_Metadata.zip __init__.py`
2. В Calibre: Настройки → Плагины → Загрузить плагин из файла
3. Выберите созданный `LitRes_Metadata.zip`
4. Перезапустите Calibre
5. Включите источник в Настройки → Загрузка метаданных

### ChitaiGorod

Источник метаданных для получения информации о книгах с chitai-gorod.ru.

**Возможности:**
- Поиск по названию, автору, ISBN
- Получение аннотации, обложки, издательства
- Парсинг JSON-LD structured data
- Fallback на HTML-парсинг

**Установка:**
1. Соберите ZIP: `cd chitai-gorod && zip -r chitai-gorod.zip __init__.py`
2. В Calibre: Настройки → Плагины → Загрузить плагин из файла
3. Выберите созданный `chitai-gorod.zip`
4. Перезапустите Calibre
5. Включите источник в Настройки → Загрузка метаданных

## Совместимость

Все плагины совместимы с Calibre 9.15 и выше.

| Плагин | Минимальная версия Calibre |
|--------|---------------------------|
| Labirint | 5.0.0 |
| Litres Metadata | 5.0.0 |
| ChitaiGorod | 8.9.0 |

## Лицензия

GPL-3.0 — см. файл [LICENSE](LICENSE)

## Разработка

Для создания ZIP-плагина из исходников:

```bash
cd Labirint
zip -r Labirint.zip __init__.py config.py plugin-import-name-Labirint.txt

cd ../Litres
zip -r LitRes_Metadata.zip __init__.py

cd ../chitai-gorod
zip -r chitai-gorod.zip __init__.py
```
