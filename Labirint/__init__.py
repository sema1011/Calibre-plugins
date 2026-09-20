"""Labirint.ru Metadata Source Plugin for Calibre."""

import re
import time
import urllib.parse
from threading import Thread

from calibre.ebooks.metadata.sources.base import Source
from calibre.ebooks.metadata.book.base import Metadata


class Labirint(Source):
    name = 'Labirint'
    description = _('Downloads book metadata from Labirint.ru')
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'sema1011'
    version = (1, 3, 18)
    minimum_calibre_version = (5, 0, 0)

    capabilities = frozenset(['identify', 'cover'])
    touched_fields = frozenset([
        'title', 'authors', 'identifier:isbn', 'comments',
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

    def _get_page(self, url, timeout=10, max_retries=1, log=None):
        """Запрос страницы с retry logic."""
        for attempt in range(max_retries + 1):
            try:
                self._apply_delay(log)
                br = self.browser
                log(f'Labirint: fetching {url}')
                resp = br.open_novisit(url, timeout=timeout)
                data = resp.read().decode('utf-8', errors='replace')
                log(f'Labirint: fetched {len(data)} bytes')
                return data
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

    def _search_by_title_author(self, log, title, authors, timeout=30):
        # Ищем только по названию (авторы делают запрос слишком длинным)
        query = title or ''
        # Пробуем два формата URL: /search/query/ и /search/?text=query
        for path in [
            f'/search/{urllib.parse.quote(query)}/',
            f'/search/?text={urllib.parse.quote(query)}',
        ]:
            data = self._get_page(f'{self.BASE_URL}{path}', timeout, log=log)
            if data:
                results = self._parse_search_results(log, data, title=title, authors=authors)
                if results:
                    return results
        return []

    def _filter_by_keywords(self, title_text, search_title, min_keyword_len=4):
        """Проверяет, содержит ли результат хотя бы одно ключевое слово из запроса.
        Берём только первые слова из title (без авторов)."""
        if not search_title:
            return True
        
        # Берём только первые 3 слова из title (без авторов)
        title_words = search_title.split()[:3]
        if not title_words:
            return True
        
        title_lower = title_text.lower()
        # Результат должен содержать хотя бы одно ключевое слово
        for word in title_words:
            if len(word) > 2 and word.lower() in title_lower:
                return True
        
        return False

    # ---- Парсинг результатов поиска -----------------------------------

    def _parse_search_results(self, log, html, title=None, authors=None, isbn=None):
        results = []
        if not html:
            return []

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')

        # Ищем карточки товаров — пробуем несколько возможных структур
        cards = soup.find_all('div', class_='product-card')
        if not cards:
            cards = soup.find_all('div', class_=re.compile(r'product-card|js-product-card', re.I))
        if not cards:
            # Fallback: ищем все ссылки на /books/
            book_links = soup.find_all('a', href=re.compile(r'/books/\d+/'))
            if book_links:
                log(f'Labirint: found {len(book_links)} book links (fallback parsing)')
                for link in book_links[:5]:
                    parent = link.find_parent(['div', 'li'])
                    if parent:
                        title_text = parent.find(['h1', 'h2', 'h3', 'h4', 'span'], class_=re.compile(r'name|title', re.I))
                        if title_text:
                            title_text = title_text.get_text(strip=True)
                            author_tag = parent.find(class_=re.compile(r'author', re.I))
                            auth = author_tag.get_text(strip=True) if author_tag else None
                            
                            img = parent.find('img')
                            img_url = img.get('data-src') or img.get('src', '') if img else ''
                            
                            href = link.get('href', '')
                            book_id_match = re.search(r'/books/(\d+)/', href)
                            book_id = book_id_match.group(1) if book_id_match else ''
                            
                            # Фильтр: результат должен содержать ключевые слова
                            if title and not self._filter_by_keywords(title_text, title):
                                continue
                            
                            results.append({
                                'title': title_text,
                                'author': auth,
                                'img_url': img_url,
                                'book_id': book_id,
                            })
                return results
            return []

        settings = self._get_settings()
        max_results = settings.get('max_results', 5)

        for card in cards[:max_results]:
            # Название
            name = card.find(class_=re.compile(r'product-card__name|name', re.I))
            title_text = name.get_text(strip=True) if name else None
            if not title_text:
                continue

            # Автор
            author = card.find(class_=re.compile(r'product-card__author|author', re.I))
            auth = author.get_text(strip=True) if author else None

            # Изображение
            img = card.find('img', class_=re.compile(r'book-img-cover|cover', re.I))
            img_url = img.get('data-src') or img.get('src', '') if img else ''

            # Ссылка на товар (извлекаем book_id)
            link_tag = card.find('a', href=re.compile(r'/books/'))
            book_id = ''
            if link_tag:
                href = link_tag.get('href', '')
                match = re.search(r'/books/(\d+)/', href)
                if match:
                    book_id = match.group(1)

            results.append({
                'title': title_text,
                'author': auth,
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
            title = h1.get_text(strip=True) if h1 else search_result.get('title', 'Unknown')

            # Автор из h1 (формат: "Title: Author")
            author = ''
            if h1:
                author_match = re.search(r':\s*([^:]+)$', title)
                if author_match:
                    author = author_match.group(1).strip()

            # Если автор не найден в h1, используем из поиска
            if not author:
                author = search_result.get('author', '')

            # Аннотация — приоритет: JSON из script > div > og:description
            description = ''
            
            # 1. Ищем в JSON данных (полная аннотация)
            for script in soup.find_all('script'):
                if script.string:
                    try:
                        import json
                        data = json.loads(script.string)
                        if isinstance(data, list) and len(data) > 52:
                            desc = data[52]
                            if desc and len(desc) > 200:
                                desc = re.sub(r'<br\s*/?>', '\n\n', desc)
                                desc = re.sub(r'<[^>]+>', '', desc)
                                description = desc.strip()
                                break
                    except:
                        pass
            
            # 2. Fallback: div с текстом
            if not description:
                desc_div = soup.find('div', class_=re.compile(r'_text_ctofl_17|annotation|description', re.I))
                if desc_div:
                    description = desc_div.get_text(strip=True)
            
            # 3. Fallback: og:description
            if not description:
                meta = soup.find('meta', property='og:description')
                description = meta.get('content', '') if meta else ''

            # ISBN из текста страницы (ищем в разных форматах)
            isbn = ''
            # Ищем ISBN в HTML (не только в тексте, но и в атрибутах)
            isbn_patterns = [
                r'ISBN[:\s]*["\']?(\d{3}[-\s]?\d{1,5}[-\s]?\d{1,5}[-\s]?\d{1,5}[-\s]?\d{1,5})',
                r'isbn[:\s]*["\']?(\d{3}[-\s]?\d{1,5}[-\s]?\d{1,5}[-\s]?\d{1,5}[-\s]?\d{1,5})',
                r'ISBN[:\s]*["\']?(\d{13})',
                r'isbn[:\s]*["\']?(\d{13})',
            ]
            for pattern in isbn_patterns:
                isbn_match = re.search(pattern, html, re.I)
                if isbn_match:
                    isbn = re.sub(r'[^0-9Xx]', '', isbn_match.group(1))
                    if len(isbn) in (10, 13):
                        break

            # Обложка — ищем в нескольких местах
            cover_url = ''
            # 1. img с cover в src
            imgs = soup.find_all('img')
            for img in imgs:
                src = img.get('data-src') or img.get('src', '')
                if src and ('cover' in src.lower() or 'imo10.labirint.ru' in src):
                    cover_url = src
                    if not src.startswith('http'):
                        cover_url = 'https:' + src if src.startswith('//') else self.BASE_URL + src
                    # Убираем размер из URL (242-0 → 400-0 для JPEG)
                    cover_url = re.sub(r'/\d+-0$', '/400-0', cover_url)
                    break
            # 2. Fallback — из поиска
            if not cover_url:
                cover_url = search_result.get('img_url', '')

            # Формируем MI
            book_id = search_result.get('book_id', '')
            authors_list = [a.strip() for a in author.split(',') if a.strip()] if author else ['Unknown']
            mi = Metadata(title, authors_list)
            mi.source_relevance = 0

            if isbn:
                mi.set_identifier('isbn', isbn)
            if book_id:
                mi.set_identifier('labirint', book_id)
            if description:
                mi.comments = description
                mi.has_html_comments = True
            mi.url = f'{self.BASE_URL}/books/{book_id}/' if book_id else ''

            log(f'Labirint: parsed — title={title[:50]}, author={author[:30]}, isbn={isbn}, cover={bool(cover_url)}')
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
        effective_timeout = settings.get('timeout', 10)
        max_results = settings.get('max_results', 5)

        log('Labirint: starting identify')

        labirint_id = identifiers.get('labirint')

        # Проверяем, не передан ли URL
        labirint_url = identifiers.get('labirint_url') or identifiers.get('url')
        if labirint_url:
            labirint_id = self.id_from_url(labirint_url)
            if labirint_id:
                labirint_id = labirint_id[1]  # extract ID from tuple

        if labirint_id:
            log(f'Labirint: loading by ID: {labirint_id}')
            data = self._fetch_product_page(log, labirint_id, effective_timeout)
            if data:
                mi = self._parse_book_details(log, data, {'title': '', 'author': '', 'book_id': labirint_id})
                if mi:
                    mi.source_relevance = 0
                    self.clean_downloaded_metadata(mi)
                    result_queue.put(mi)
                    return None

        # Ищем только по названию/автору
        if not (title or authors):
            log('Labirint: no title/authors to search')
            return None

        log(f'Labirint: searching by title/author: {title} / {authors}')
        search_results = self._search_by_title_author(log, title, authors, effective_timeout)

        if not search_results:
            log('Labirint: no search results')
            return None

        log(f'Labirint: found {len(search_results)} results')

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
            t.join(timeout=effective_timeout + 5)

        return None

    def download_cover(self, log, result_queue, abort,
                       title=None, authors=None,
                       identifiers=None, timeout=30,
                       get_best_cover=False):
        if identifiers is None:
            identifiers = {}

        settings = self._get_settings()
        effective_timeout = settings.get('timeout', 10)

        labirint_id = identifiers.get('labirint')
        log(f'Labirint: download_cover start, labirint_id={labirint_id}')

        if not labirint_id:
            isbn = identifiers.get('isbn')
            if isbn:
                log(f'Labirint: searching by ISBN for cover: {isbn}')
                results = self._search_by_title_author(log, isbn, [], effective_timeout)
                if results:
                    labirint_id = results[0].get('book_id', '')
                    log(f'Labirint: found book_id={labirint_id} by ISBN')
                else:
                    log('Labirint: no results by ISBN for cover')
            else:
                log('Labirint: no labirint_id or isbn for cover')
                return None

        if not labirint_id:
            return None

        log(f'Labirint: fetching product page for cover: {labirint_id}')
        data = self._fetch_product_page(log, labirint_id, effective_timeout)
        if not data:
            log('Labirint: no data for cover')
            return None

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(data, 'html.parser')
        
        # Ищем обложку
        cover_url = ''
        imgs = soup.find_all('img')
        log(f'Labirint: found {len(imgs)} images on page')
        for i, img in enumerate(imgs):
            src = img.get('data-src') or img.get('src', '')
            if src and ('cover' in src.lower() or 'imo10.labirint.ru' in src):
                cover_url = src
                if not src.startswith('http'):
                    cover_url = 'https:' + src if src.startswith('//') else self.BASE_URL + src
                # Убираем размер из URL (242-0 → 400-0 для JPEG)
                cover_url = re.sub(r'/\d+-0$', '/400-0', cover_url)
                log(f'Labirint: found cover image at index {i}: {cover_url[:100]}')
                break

        if not cover_url:
            log('Labirint: no cover URL found in images')
            return None

        log(f'Labirint: downloading cover from {cover_url}')
        # Скачиваем обложку как бинарные данные (без decode)
        try:
            self._apply_delay(log)
            br = self.browser
            resp = br.open_novisit(cover_url, timeout=effective_timeout)
            cover_data = resp.read()  # raw bytes
            log(f'Labirint: cover downloaded, size={len(cover_data)} bytes')
            if cover_data and len(cover_data) > 100:
                # Конвертирую WebP → JPEG (Labirint всегда отдаёт WebP)
                try:
                    from PIL import Image
                    import io
                    img = Image.open(io.BytesIO(cover_data))
                    if img.format == 'WEBP':
                        jpeg_buf = io.BytesIO()
                        img.convert('RGB').save(jpeg_buf, format='JPEG')
                        cover_data = jpeg_buf.getvalue()
                        log(f'Labirint: cover converted to JPEG, size={len(cover_data)}')
                except Exception as conv_err:
                    log(f'Labirint: cover conversion failed: {conv_err}')
                
                result_queue.put((self, cover_data))
            else:
                log(f'Labirint: cover too small, size={len(cover_data)}')
        except Exception as e:
            log(f'Labirint: cover download error: {e}')

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
