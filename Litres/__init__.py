__license__ = 'GPL-3.0-only'
__copyright__ = '2026'
__docformat__ = 'restructuredtext en'

"""
Плагин источника метаданных для Calibre — получает информацию о книгах с ЛитРес.

Источники данных:
  - Поиск:  https://api.litres.ru/foundation/api/search
  - Детали: https://api.litres.ru/foundation/api/arts/{id}
  - Обложка: https://www.litres.ru/pub/c/cover/{id}.jpg

Установка:
  1. Запакуйте этот файл в ZIP-архив (например, LitRes_Metadata.zip).
  2. В Calibre: Настройки -> Плагины -> Загрузить плагин из файла.
  3. Перезапустите Calibre.
  4. Включите источник в Настройки -> Загрузка метаданных.
"""

import json
import re
import socket
import urllib.request
from queue import Empty, Queue
from urllib.parse import urlencode

from calibre.ebooks.metadata.book.base import Metadata
from calibre.ebooks.metadata.sources.base import Source
from calibre.utils.date import parse_only_date


class LitResMetadata(Source):

    name = 'LitRes Metadata'
    description = 'Получает метаданные и обложки книг с ЛитРес (litres.ru).'
    author = 'sema1011'
    version = (1, 0, 7)
    minimum_calibre_version = (8, 9, 0)

    capabilities = frozenset(['identify', 'cover'])
    touched_fields = frozenset([
        'title', 'authors', 'comments', 'identifier:isbn',
        'identifier:litres', 'publisher', 'pubdate', 'rating', 'tags',
    ])

    supports_gzip_transfer_encoding = True
    ignore_ssl_errors = True
    cached_cover_url_is_reliable = True

    LITRES_API_SEARCH = 'https://api.litres.ru/foundation/api/search'
    LITRES_API_ARTS   = 'https://api.litres.ru/foundation/api/arts'
    LITRES_SITE       = 'https://www.litres.ru'
    LITRES_COVER_URL  = 'https://cdn.litres.ru/pub/c/cover_{id}'

    # ------------------------------------------------------------------
    # HTTP-запросы
    # ------------------------------------------------------------------

    def _make_request(self, url, log, timeout=30):
        log('LitRes: запрос: ' + url)
        try:
            import urllib.error
            req = urllib.request.Request(url)
            req.add_header('Accept', 'application/json')
            req.add_header('Accept-Language', 'ru-RU,ru;q=0.9,en;q=0.8')
            req.add_header('User-Agent', 'Mozilla/5.0 (Calibre LitRes Metadata)')
            req.add_header('app-id', '1')
            req.add_header('ui-language-code', 'ru')
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            if isinstance(raw, bytes):
                raw = raw.decode('utf-8', errors='replace')
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            log('LitRes: HTTP ' + str(e.code) + ': ' + str(e.reason))
        except socket.timeout:
            log('LitRes: таймаут запроса')
        except Exception as e:
            log('LitRes: ошибка запроса: ' + str(e))
        return None

    def _search_books(self, query, log, timeout=30, limit=20):
        params = urlencode({
            'q': query,
            'limit': limit,
            'offset': 0,
            'types': ['text_book', 'audiobook'],
        }, doseq=True)
        url = self.LITRES_API_SEARCH + '?' + params
        data = self._make_request(url, log, timeout)
        if not data:
            return []

        results = []
        if isinstance(data, dict):
            payload = data.get('payload', data)
            if isinstance(payload, dict):
                arts = payload.get('data', [])
            else:
                arts = []
        else:
            arts = []

        for art in arts:
            if isinstance(art, dict):
                instance = art.get('instance', art)
                if isinstance(instance, dict):
                    results.append(instance)
                else:
                    results.append(art)

        return results

    def _get_book_details(self, book_id, log, timeout=30):
        url = self.LITRES_API_ARTS + '/' + str(book_id)
        data = self._make_request(url, log, timeout)
        if not data:
            return None
        if isinstance(data, dict):
            payload = data.get('payload', data)
            if isinstance(payload, dict):
                return payload.get('data', payload)
            return payload
        return data

    # ------------------------------------------------------------------
    # Извлечение полей из данных API
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_title(art):
        for key in ('title', 'name', 'book_title', 'book-title'):
            if key in art and art[key]:
                title = str(art[key]).strip()
                # Убираем суффиксы формата: (pdf+epub), (EPUB, FB2), и т.д.
                title = re.sub(r'\s*\(.*?(?:pdf|epub|fb2|mobi|txt|rtf|djvu|cbz).*?\)', '', title, flags=re.I)
                title = title.strip()
                return title
        return ''

    @staticmethod
    def _extract_authors(art):
        authors = []
        persons = art.get('persons') or []
        if isinstance(persons, list):
            for p in persons:
                if isinstance(p, dict):
                    role = p.get('role', '')
                    is_author = False
                    if isinstance(role, str):
                        is_author = 'author' in role.lower() or role == '1'
                    elif isinstance(role, int):
                        is_author = role == 1
                    if not is_author:
                        continue
                    name = (p.get('full_name') or p.get('name')
                            or p.get('display_name') or '')
                    if not name:
                        parts = []
                        for k in ('first_name', 'first-name',
                                  'middle_name', 'middle-name',
                                  'last_name', 'last-name'):
                            v = p.get(k)
                            if v:
                                parts.append(str(v).strip())
                        name = ' '.join(parts)
                    if name:
                        authors.append(name.strip())

        if not authors:
            raw = (art.get('authors') or art.get('author')
                   or art.get('writers') or [])
            if isinstance(raw, str):
                authors = [raw.strip()]
            elif isinstance(raw, list):
                for a in raw:
                    if isinstance(a, str):
                        authors.append(a.strip())
                    elif isinstance(a, dict):
                        name = (a.get('full_name') or a.get('name')
                                or a.get('full-name')
                                or a.get('display_name') or '')
                        if not name:
                            parts = []
                            for k in ('first_name', 'first-name',
                                      'middle_name', 'middle-name',
                                      'last_name', 'last-name'):
                                v = a.get(k)
                                if v:
                                    parts.append(str(v).strip())
                            name = ' '.join(parts)
                        if name:
                            authors.append(name.strip())

        return [a for a in authors if a]

    @staticmethod
    def _extract_isbn(art):
        isbn = art.get('isbn') or art.get('ISBN')
        if isbn and isinstance(isbn, str):
            isbn = isbn.strip()
            if re.match(r'^[\d\-Xx]{10,17}$', isbn):
                return isbn
        return None

    @staticmethod
    def _extract_id(art):
        for key in ('id', 'art_id', 'art-id', 'hub_id', 'uuid'):
            if key in art and art[key]:
                return str(art[key])
        return None

    @staticmethod
    def _extract_comments(art):
        for key in ('annotation', 'html_annotation', 'description',
                    'html_description', 'comments',
                    'full_annotation', 'description_html',
                    'full_description', 'description_text'):
            val = art.get(key)
            if val and isinstance(val, str) and val.strip():
                return val.strip()
        # Проверяем вложенные структуры
        for section_key in ('meta', 'metadata', 'info'):
            section = art.get(section_key)
            if isinstance(section, dict):
                for key in ('annotation', 'description', 'comments',
                            'full_annotation', 'html_annotation'):
                    val = section.get(key)
                    if val and isinstance(val, str) and val.strip():
                        return val.strip()
        return ''

    @staticmethod
    def _extract_publisher(art):
        for key in ('publisher', 'publishing_house', 'publishing-house'):
            val = art.get(key)
            if val and isinstance(val, str) and val.strip():
                return val.strip()
            if val and isinstance(val, dict):
                name = val.get('name') or val.get('title')
                if name:
                    return str(name).strip()
        return None

    @staticmethod
    def _extract_pubdate(art):
        for key in ('pubdate', 'publish_date', 'publish-date',
                    'year', 'publication_year', 'date'):
            val = art.get(key)
            if val:
                val_str = str(val).strip()
                if re.match(r'^\d{4}$', val_str):
                    try:
                        return parse_only_date('01.01.' + val_str)
                    except Exception:
                        pass
                try:
                    return parse_only_date(val_str)
                except Exception:
                    pass
        return None

    @staticmethod
    def _extract_tags(art):
        tags = []
        genres = art.get('genres') or art.get('tags') or art.get('genre') or []
        if isinstance(genres, str):
            genres = [genres]
        for g in genres:
            if isinstance(g, str):
                tags.append(g.strip())
            elif isinstance(g, dict):
                name = g.get('title') or g.get('name')
                if name:
                    tags.append(str(name).strip())
        return [t for t in tags if t]

    @staticmethod
    def _extract_rating(art):
        for key in ('rated_avg', 'rating', 'rating_avg'):
            if key in art and art[key] is not None:
                rating = art[key]
                break
        else:
            return None
        try:
            val = float(rating)
            # ЛитРес использует 5-балльную шкалу, Calibre — 10-балльную
            if val <= 5.0:
                val = val * 2.0
            return int(round(max(0.0, min(10.0, val))))
        except (ValueError, TypeError):
            pass
        return None

    @staticmethod
    def _extract_cover_url(art):
        # Прямые поля
        for key in ('cover_url', 'cover', 'cover_preview',
                    'cover_image', 'image_url'):
            val = art.get(key)
            if val and isinstance(val, str):
                if val.startswith('http'):
                    return val
                if val.startswith('/'):
                    return LitResMetadata.LITRES_SITE + val
        # Вложенные структуры
        for section_key in ('meta', 'instance', 'book', 'data',
                            'cover_info', 'image'):
            section = art.get(section_key)
            if isinstance(section, dict):
                for key in ('cover', 'cover_url', 'cover_image',
                            'cover_preview', 'image', 'image_url'):
                    val = section.get(key)
                    if val and isinstance(val, str):
                        if val.startswith('http'):
                            return val
                        if val.startswith('/'):
                            return LitResMetadata.LITRES_SITE + val
                    elif isinstance(val, dict):
                        url = val.get('url') or val.get('src') or val.get('link')
                        if url and isinstance(url, str):
                            if url.startswith('http'):
                                return url
                            if url.startswith('/'):
                                return LitResMetadata.LITRES_SITE + url
        # Фоллбэк: конструируем по book ID
        book_id = LitResMetadata._extract_id(art)
        if book_id:
            return LitResMetadata.LITRES_COVER_URL.format(id=book_id)
        return None

    @staticmethod
    def _extract_series(art):
        sequences = art.get('sequences') or art.get('series') or art.get('serial')
        if not sequences:
            return None, None
        if isinstance(sequences, str):
            return sequences.strip(), None
        if isinstance(sequences, dict):
            name = sequences.get('title') or sequences.get('name')
            num = sequences.get('number') or sequences.get('sequence')
            if name:
                return str(name).strip(), (str(num) if num else None)
        if isinstance(sequences, list) and sequences:
            s = sequences[0]
            if isinstance(s, dict):
                name = s.get('title') or s.get('name')
                num = s.get('sequence_number') or s.get('number') or s.get('sequence')
                if name:
                    return str(name).strip(), (str(num) if num else None)
            elif isinstance(s, str):
                return s.strip(), None
        return None, None

    # ------------------------------------------------------------------
    # Построение объекта Metadata
    # ------------------------------------------------------------------

    def _build_metadata(self, art, log, source_relevance=0):
        title = self._extract_title(art)
        authors = self._extract_authors(art)
        book_id = self._extract_id(art)

        if not title or not authors:
            log('LitRes: пропущена книга без названия или автора')
            return None

        mi = Metadata(title, authors)
        if book_id:
            mi.set_identifier('litres', book_id)
        mi.source_relevance = source_relevance

        isbn = self._extract_isbn(art)
        if isbn:
            mi.isbn = isbn

        comments = self._extract_comments(art)
        if comments:
            mi.comments = comments

        publisher = self._extract_publisher(art)
        if publisher:
            mi.publisher = publisher

        pubdate = self._extract_pubdate(art)
        if pubdate:
            mi.pubdate = pubdate

        tags = self._extract_tags(art)
        if tags:
            mi.tags = tags

        rating = self._extract_rating(art)
        if rating is not None:
            mi.rating = rating

        series_name, series_num = self._extract_series(art)
        if series_name:
            mi.series = series_name
            if series_num:
                try:
                    mi.series_index = float(series_num)
                except ValueError:
                    pass

        cover_url = self._extract_cover_url(art)
        if cover_url and book_id:
            self.cache_identifier_to_cover_url(book_id, cover_url)
            mi.cover_url = cover_url

        return mi

    # ------------------------------------------------------------------
    # Кэш обложек
    # ------------------------------------------------------------------

    def get_cached_cover_url(self, identifiers):
        """Возвращает закэшированный URL обложки по идентификаторам."""
        identifiers = identifiers or {}
        litres_id = identifiers.get('litres', None)
        if litres_id:
            return self.cached_identifier_to_cover_url(litres_id)
        return None

    # ------------------------------------------------------------------
    # Методы интерфейса Calibre Source
    # ------------------------------------------------------------------

    def identify(self, log, result_queue, abort,
                 title=None, authors=None, identifiers=None,
                 timeout=30):
        log('LitRes: начало поиска метаданных')
        identifiers = identifiers or {}

        litres_id = identifiers.get('litres', None)
        if litres_id:
            log('LitRes: поиск по litres ID: ' + str(litres_id))
            art = self._get_book_details(litres_id, log, timeout)
            if art:
                mi = self._build_metadata(art, log, source_relevance=0)
                if mi:
                    result_queue.put(mi)
                    log('LitRes: книга найдена по ID')
                    return None
            log('LitRes: книга не найдена по ID, ищем по названию/автору')

        isbn = identifiers.get('isbn', None)
        search_tokens = []
        if isbn:
            search_tokens.append(isbn)

        if title:
            search_tokens += list(self.get_title_tokens(title,
                                strip_joiners=False, strip_subtitle=False))
        if authors:
            search_tokens += list(self.get_author_tokens(authors,
                                only_first_author=True))

        if not search_tokens:
            log('LitRes: нет данных для поиска')
            return None

        query = ' '.join(search_tokens)
        log('LitRes: поисковый запрос: ' + query)

        arts = self._search_books(query, log, timeout, limit=20)
        log('LitRes: найдено результатов: ' + str(len(arts)))

        if abort.is_set():
            log('LitRes: поиск прерван')
            return None

        if not arts and isbn and title:
            log('LitRes: повторный поиск без ISBN')
            title_tokens = list(self.get_title_tokens(title))
            query2 = ' '.join(title_tokens)
            if authors:
                query2 += ' ' + ' '.join(
                    self.get_author_tokens(authors, only_first_author=True))
            arts = self._search_books(query2, log, timeout, limit=20)
            log('LitRes: найдено при повторном поиске: ' + str(len(arts)))

        if not arts:
            log('LitRes: ничего не найдено')
            return None

        relevance = 0
        for art in arts:
            if abort.is_set():
                log('LitRes: поиск прерван')
                break
            mi = self._build_metadata(art, log, source_relevance=relevance)
            relevance += 1
            if mi:
                result_queue.put(mi)

        log('LitRes: поиск завершён')
        return None

    def download_cover(self, log, result_queue, abort,
                       title=None, authors=None, identifiers=None,
                       timeout=30, get_best_cover=False):
        log('LitRes: загрузка обложки')
        identifiers = identifiers or {}

        cover_url = self.get_cached_cover_url(identifiers)

        if not cover_url:
            log('LitRes: нет закэшированного URL, ищем книгу')
            tmp_queue = Queue()
            self.identify(log, tmp_queue, abort,
                         title=title, authors=authors,
                         identifiers=identifiers, timeout=timeout)
            try:
                while True:
                    mi = tmp_queue.get_nowait()
                    litres_id = mi.identifiers.get('litres', '') if mi.identifiers else ''
                    if litres_id:
                        cover_url = self.cached_identifier_to_cover_url(litres_id)
                        if cover_url:
                            break
                    else:
                        litres_id = ''
            except Empty:
                pass

        # Фоллбэк: если ID был в identifiers — конструируем URL напрямую
        if not cover_url:
            litres_id = identifiers.get('litres', '')
            if litres_id:
                cover_url = self.LITRES_COVER_URL.format(id=litres_id)

        # Фоллбэк: если ID не был найден через кэш, пробуем поискать
        # среди результатов identify ещё раз
        if not cover_url and not identifiers.get('litres'):
            log('LitRes: фоллбэк обложки — повторный поиск')
            tmp_queue = Queue()
            self.identify(log, tmp_queue, abort,
                         title=title, authors=authors,
                         identifiers=identifiers, timeout=timeout)
            try:
                while True:
                    mi = tmp_queue.get_nowait()
                    litres_id = mi.identifiers.get('litres', '') if mi.identifiers else ''
                    if litres_id:
                        cover_url = self.LITRES_COVER_URL.format(id=litres_id)
                        if cover_url:
                            break
            except Empty:
                pass

        if not cover_url:
            log('LitRes: URL обложки не найден')
            return

        log('LitRes: загрузка обложки: ' + cover_url)
        try:
            import requests
            resp = requests.get(
                cover_url,
                headers={'User-Agent': 'Mozilla/5.0 (Calibre LitRes Metadata)'},
                timeout=timeout + 10,
                verify=False,
            )
            raw = resp.content
            if raw and len(raw) > 1024:
                result_queue.put((self, raw))
                log('LitRes: обложка загружена (' + str(len(raw)) + ' байт)')
            else:
                log('LitRes: обложка слишком мала или пустая')
        except Exception as e:
            log('LitRes: ошибка загрузки обложки: ' + str(e))

    def get_book_url(self, identifiers=None):
        identifiers = identifiers or {}
        litres_id = identifiers.get('litres', None)
        if litres_id:
            url = self.LITRES_SITE + '/book/' + str(litres_id) + '/'
            return ('litres', litres_id, url)
        return None

    def get_book_url_name(self, idtype, idval, url):
        return 'LitRes Metadata'

    def id_from_url(self, url):
        match = re.match(r'https?://(?:www\.)?litres\.ru/(?:book|audiobook|work)/(\d+)/?', url)
        if match:
            return ('litres', match.group(1))
        return None
