"""Labirint.ru Metadata Source Plugin for Calibre."""

import re
import time
import urllib.parse
from threading import Thread

from calibre.ebooks.metadata.sources.base import Source
from calibre.ebooks.metadata.book.base import Metadata
from calibre.utils.logging import Log


class Labirint(Source):
    name = 'Labirint'
    description = _('Downloads book metadata from Labirint.ru')
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'sema1011'
    version = (1, 3, 1)
    minimum_calibre_version = (5, 0, 0)

    capabilities = frozenset(['identify', 'cover'])
    touched_fields = frozenset([
        'title', 'authors', 'identifier:isbn', 'comments',
        'publisher', 'pubdate',
    ])
    has_html_comments = True

    BASE_URL = 'https://www.labirint.ru'

    # Задержки для обхода блокировок
    _last_request_time = 0.0
    _delay_lock = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        import threading
        self._delay_lock = threading.Lock()

    # ---- Настройки ----------------------------------------------------

    def _get_settings(self):
        from calibre_plugins.Labirint.config import get_settings, DEFAULTS
        try:
            return get_settings()
        except Exception:
            return dict(DEFAULTS)

    def _apply_delay(self, log):
        settings = self._get_settings()
        delay = settings.get('delay_seconds', 1)
        if delay <= 0:
            return
        with self._delay_lock:
            now = time.time()
            elapsed = now - self._last_request_time
            wait = delay - elapsed
            if wait > 0:
                time.sleep(wait)
            self._last_request_time = time.time()

    # ---- HTTP запросы -------------------------------------------------

    def _get_page(self, url, timeout=30, max_retries=2, log=None):
        """Запрос страницы с retry logic."""
        for attempt in range(max_retries + 1):
            try:
                self._apply_delay(log)
                br = self.browser
                resp = br.open_novisit(url, timeout=timeout)
                return resp.read().decode('utf-8', errors='replace')
            except Exception as e:
                err_str = str(e).lower()
                if 'certificate' in err_str or 'ssl' in err_str:
                    log(f'Labirint: SSL error: {e}')
                    return None
                if attempt < max_retries:
                    wait = (attempt + 1) * 2
                    log(f'Labirint: error (retry {attempt+1}/{max_retries}): {e}, waiting {wait}s')
                    time.sleep(wait)
                else:
                    log(f'Labirint: error after {max_retries} retries: {e}')
                    return None
        return None

    # ---- Поиск --------------------------------------------------------

    def _search_by_isbn(self, log, isbn, timeout=30):
        clean = re.sub(r'[^0-9Xx]', '', isbn)
        path = f'/search/?text={clean}'
        data = self._get_page(f'{self.BASE_URL}{path}', timeout, log=log)
        return self._parse_search_results(log, data, isbn=clean) if data else []

    def _search_by_title_author(self, log, title, authors, timeout=30):
        query = ' '.join(filter(None, [title] + (authors or [])))
        path = f'/search/?text={urllib.parse.quote(query)}'
        data = self._get_page(f'{self.BASE_URL}{path}', timeout, log=log)
        return self._parse_search_results(log, data, title=title, authors=authors) if data else []

    def _is_book_match(self, title, author, search_title, search_authors, isbn):
        """Проверяет, совпадает ли книга с поисковым запросом."""
        if not title:
            return False

        # Фильтрация по названию
        if search_title:
            title_lower = title.lower()
            search_lower = search_title.lower()
            # Проверяем частичное совпадение
            if len(search_lower) > 10:
                if search_lower not in title_lower and title_lower not in search_lower:
                    return False

        # Фильтрация по автору
        if search_authors:
            author_lower = (author or '').lower()
            for auth in search_authors:
                auth_lower = auth.lower()
                if auth_lower and auth_lower not in author_lower and author_lower not in auth_lower:
                    return False

        return True

    # ---- Парсинг результатов поиска -----------------------------------

    def _parse_search_results(self, log, html, title=None, authors=None, isbn=None):
        results = []
        if not html:
            return []

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')

        # Ищем карточки товаров
        cards = soup.find_all('div', class_='product-card')
        if not cards:
            return []

        settings = self._get_settings()
        max_results = settings.get('max_results', 5)

        for card in cards[:max_results]:
            # Название
            name = card.find(class_='product-card__name')
            title_text = name.get_text(strip=True) if name else None
            if not title_text:
                continue

            # Автор
            author = card.find(class_='product-card__author')
            auth = author.get_text(strip=True) if author else None

            # Цена
            price = card.find(class_='product-card__price-current')
            price_text = price.get_text(strip=True) if price else None

            # Изображение
            img = card.find('img', class_='book-img-cover')
            img_url = img.get('data-src') or img.get('src', '') if img else ''

            # Ссылка на товар (извлекаем book_id)
            link_tag = card.find('a', href=re.compile(r'/books/'))
            book_id = ''
            if link_tag:
                href = link_tag.get('href', '')
                match = re.search(r'/books/(\d+)/', href)
                if match:
                    book_id = match.group(1)

            # Извлекаем ISBN из описания карточки (для поиска по ISBN)
            card_isbns = []
            if isbn:
                desc = card.get_text()
                card_isbns = re.findall(r'(\d{13}|\d{10})', desc)

            # Фильтрация по совпадению ISBN (если ищем по ISBN)
            if isbn and card_isbns:
                clean_isbn = re.sub(r'[^0-9Xx]', '', isbn)
                matched = False
                for ci in card_isbns:
                    if re.sub(r'[^0-9Xx]', '', ci) == clean_isbn:
                        matched = True
                        break
                if not matched:
                    continue

            # Фильтрация по совпадению названия/автора
            if not self._is_book_match(title_text, auth, title, authors, isbn):
                continue

            results.append({
                'title': title_text,
                'author': auth,
                'price': price_text,
                'img_url': img_url,
                'book_id': book_id,
            })

        return results

    # ---- Страница товара ---------------------------------------------

    def _fetch_product_page(self, log, book_id, timeout=30):
        path = f'/books/{book_id}/'
        return self._get_page(f'{self.BASE_URL}{path}', timeout, log=log)

    def _parse_book_details(self, log, html, search_result):
        if not html:
            return None

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')

        try:
            # Название из h1
            h1 = soup.find('h1')
            title = h1.get_text(strip=True) if h1 else search_result['title']

            # Автор из h1 (формат: "Title: Author")
            author = ''
            if h1:
                author_match = re.search(r':\s*([^:]+)$', title)
                if author_match:
                    author = author_match.group(1).strip()

            # Если автор не найден в h1, используем из поиска
            if not author:
                author = search_result.get('author', '')

            # Аннотация из meta og:description
            desc = soup.find('meta', property='og:description')
            description = desc.get('content', '') if desc else ''

            # ISBN из текста страницы
            isbn = ''
            full_text = soup.get_text()
            isbn_match = re.search(r'ISBN[:\s]*(\d{13}|\d{10})', full_text)
            if isbn_match:
                isbn = re.sub(r'[^0-9Xx]', '', isbn_match.group(1))

            # Обложка
            img = soup.find('img', class_='book-img-cover')
            cover_url = img.get('data-src') or img.get('src', '') if img else ''
            if not cover_url:
                cover_url = search_result.get('img_url', '')

            # Цена
            price = soup.find(class_='product-card__price-current')
            price_text = price.get_text(strip=True) if price else None

            # Формируем MI
            book_id = search_result.get('book_id', '')
            authors_list = [a.strip() for a in author.split(',') if a.strip()] if author else ['Unknown']
            mi = Metadata(title, authors_list)
            mi.source_relevance = 0

            if isbn:
                mi.set_identifier('isbn', isbn)
            if description:
                mi.comments = description
                mi.has_html_comments = True
            if cover_url:
                pass
            mi.url = f'{self.BASE_URL}/books/{book_id}/'

            return mi
        except Exception as e:
            log(f'Labirint: error parsing book details: {e}')
            return None

    # ---- Интерфейс Source ---------------------------------------------

    def identify(self, log, result_queue, abort, title=None,
                 authors=None, identifiers=None, timeout=30):
        if identifiers is None:
            identifiers = {}

        settings = self._get_settings()
        effective_timeout = settings.get('timeout', 30)
        max_results = settings.get('max_results', 5)

        log('Labirint: starting identify')

        isbn = identifiers.get('isbn')
        labirint_id = identifiers.get('labirint')

        search_results = []

        if labirint_id:
            log(f'Labirint: searching by labirint ID: {labirint_id}')
            data = self._fetch_product_page(log, labirint_id, effective_timeout)
            if data:
                mi = self._parse_book_details(log, data, {'title': '', 'author': '', 'book_id': labirint_id})
                if mi:
                    mi.source_relevance = 0
                    self.clean_downloaded_metadata(mi)
                    result_queue.put(mi)
                    return None

        if isbn:
            log(f'Labirint: searching by ISBN: {isbn}')
            search_results = self._search_by_isbn(log, isbn, effective_timeout)
            if not search_results:
                log('Labirint: no results by ISBN')

        if not search_results and (title or authors):
            log(f'Labirint: searching by title/author: {title} / {authors}')
            search_results = self._search_by_title_author(log, title, authors, effective_timeout)

        if not search_results:
            log('Labirint: no search results')
            return None

        threads = []

        def parse_and_queue(sr, idx):
            if abort.is_set():
                return
            if sr.get('book_id'):
                data = self._fetch_product_page(log, sr['book_id'], effective_timeout)
                if data:
                    mi = self._parse_book_details(log, data, sr)
                    if mi:
                        mi.source_relevance = idx
                        self.clean_downloaded_metadata(mi)
                        result_queue.put(mi)

        limit = min(len(search_results), max_results)
        for idx, sr in enumerate(search_results[:limit]):
            t = Thread(target=parse_and_queue, args=(sr, idx))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=effective_timeout + 10)

        return None

    def download_cover(self, log, result_queue, abort,
                       title=None, authors=None,
                       identifiers=None, timeout=30,
                       get_best_cover=False):
        if identifiers is None:
            identifiers = {}

        settings = self._get_settings()
        effective_timeout = settings.get('timeout', 30)

        labirint_id = identifiers.get('labirint')
        if not labirint_id:
            isbn = identifiers.get('isbn')
            if isbn:
                results = self._search_by_isbn(log, isbn, effective_timeout)
                if results:
                    labirint_id = results[0].get('book_id', '')

        if not labirint_id:
            return None

        cover_url = ''
        data = self._fetch_product_page(log, labirint_id, effective_timeout)
        if data:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(data, 'html.parser')
            img = soup.find('img', class_='book-img-cover')
            if img:
                cover_url = img.get('data-src') or img.get('src', '')

        if not cover_url:
            log('Labirint: no cover URL found')
            return None

        log(f'Labirint: downloading cover from {cover_url}')
        self._apply_delay(log)
        cover_data = self._get_page(cover_url, effective_timeout, log=log)
        if cover_data and len(cover_data) > 100:
            result_queue.put((self, cover_data.encode('utf-8')))

        return None

    def get_book_url(self, identifiers):
        labirint_id = identifiers.get('labirint')
        if labirint_id:
            return ('labirint', labirint_id,
                    f'{self.BASE_URL}/books/{labirint_id}/')
        return None

    def get_book_url_name(self, idtype, idval, url):
        return 'Labirint.ru'

    def id_from_url(self, url):
        m = re.search(r'/books/(\d+)/', url)
        if m:
            return ('labirint', m.group(1))
        return None
