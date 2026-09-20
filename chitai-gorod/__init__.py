#!/usr/bin/env python
# -*- coding: utf-8 -*-

__license__ = 'GPL v3'
__copyright__ = '2026, Your Name'

import json
import re
from urllib.parse import quote

from calibre.ebooks.metadata.sources.base import Source
from calibre.ebooks.metadata.book.base import Metadata


class ChitaiGorod(Source):
    name = 'ChitaiGorod'
    description = 'Метаданные книг с сайта chitai-gorod.ru'
    author = 'sema1011'
    version = (1, 4, 0)
    minimum_calibre_version = (8, 9, 0)

    capabilities = frozenset(['identify', 'cover'])
    touched_fields = frozenset(['title', 'authors', 'identifier:isbn',
                                'identifier:chitaigorod', 'pubdate',
                                'publisher', 'comments'])
    supports_gzip_transfer_encoding = True
    has_html_comments = True

    BASE_URL = 'https://www.chitai-gorod.ru'
    API_URL = 'https://web-agr.chitai-gorod.ru'

    def identify(self, log, result_queue, abort,
                 title=None, authors=None, identifiers={}, timeout=30):
        br = self.browser

        token = self._get_anon_token(br, log, timeout)
        if not token:
            log.error('ChitaiGorod: не удалось получить анонимный токен')
            return

        isbn = identifiers.get('isbn', None)
        is_isbn = bool(isbn)
        query_parts = []
        if isbn:
            query_parts.append(isbn)
        else:
            if title:
                query_parts.append(title)
            if authors:
                query_parts.extend(authors[:2])
        if not query_parts:
            return

        query = ' '.join(query_parts)
        log.info(f'ChitaiGorod: поисковый запрос: {query}')

        products = self._api_search(br, log, query, token, timeout, abort,
                                     is_isbn=is_isbn)
        if not products:
            log.info('ChitaiGorod: API не дал результатов, пробуем HTML')
            products = self._html_search(br, log, query, timeout, abort)

        # Если поиск по ISBN и ничего не найдено — пробуем прямой lookup
        if not products and is_isbn:
            log.info('ChitaiGorod: поиск по ISBN не дал результатов, '
                     'пробуем прямой lookup')
            mi = self._find_product_by_isbn(br, log, isbn, token, timeout)
            if mi:
                result_queue.put(mi)
                return

        if not products:
            log.info('ChitaiGorod: ничего не найдено')
            return

        log.info(f'ChitaiGorod: найдено {len(products)} результатов')

        for prod in products[:5]:
            if abort.is_set():
                return
            mi = self._fetch_metadata(br, log, prod, token, timeout)
            if mi:
                result_queue.put(mi)

    # ── URL-хелперы ────────────────────────────────────────────────

    def _normalize_cover_url(self, val):
        """Нормализует значение обложки в абсолютный URL."""
        if isinstance(val, dict):
            val = val.get('url') or val.get('src') or val.get('link') or ''
        if not val or not isinstance(val, str):
            return None
        val = val.strip()
        if not val:
            return None
        if val.startswith(('http://', 'https://')):
            return val
        if val.startswith('//'):
            return 'https:' + val
        # Статика (обложки) на content.img-gorod.ru, а не на www.chitai-gorod.ru
        if val.startswith('/pim/') or val.startswith('/content/'):
            return 'https://content.img-gorod.ru' + val
        if val.startswith('/'):
            return self.BASE_URL + val
        # Относительный URL без ведущего / (например: pim/products/...)
        if not val.startswith(('http', '//')):
            if val.startswith('pim/') or val.startswith('content/'):
                return 'https://content.img-gorod.ru/' + val
            return self.BASE_URL + '/' + val
        return None

    def _extract_cover_from_item(self, item):
        """Извлекает URL обложки из dict (ответ API)."""
        if not isinstance(item, dict):
            return None

        # Прямые поля
        for key in ('cover', 'image', 'cover_url', 'picture', 'photo'):
            val = item.get(key)
            if val:
                url = self._normalize_cover_url(val)
                if url:
                    return url

        # Массив изображений
        for arr_key in ('images', 'pictures', 'photos', 'gallery'):
            images = item.get(arr_key)
            if isinstance(images, list) and images:
                for img in images:
                    url = self._normalize_cover_url(img)
                    if url:
                        return url
                # Если элементы — dict с полем url/image
                for img in images:
                    if isinstance(img, dict):
                        url = (img.get('url') or img.get('src')
                               or img.get('image') or img.get('thumb'))
                        normalized = self._normalize_cover_url(url)
                        if normalized:
                            return normalized

        return None

    # ── Токен ──────────────────────────────────────────────────────

    def _get_anon_token(self, br, log, timeout):
        url = f'{self.API_URL}/web/api/v1/auth/anonymous'
        old_headers = list(br.addheaders)
        try:
            br.addheaders = old_headers + [
                ('Content-Type', 'application/json'),
                ('Accept', 'application/json'),
            ]
            resp = br.open_novisit(url, data=b'{}', timeout=timeout)
            raw = resp.read().decode('utf-8', errors='replace')
            data = json.loads(raw)

            token = self._find_token(data)
            if token:
                if token.startswith('Bearer '):
                    token = token[7:]
                log.info('ChitaiGorod: анонимный токен получен')
                return token
            log.error(f'ChitaiGorod: токен не найден в ответе: {raw[:300]}')
        except Exception as e:
            log.error(f'ChitaiGorod: ошибка получения токена: {e}')
        finally:
            br.addheaders = old_headers
        return None

    def _find_token(self, obj, depth=0):
        if depth > 5:
            return None
        if isinstance(obj, str):
            if (obj.startswith('eyJ') or obj.startswith('Bearer eyJ')) and len(obj) > 20:
                return obj[7:] if obj.startswith('Bearer ') else obj
            return None
        if isinstance(obj, dict):
            for key in ('token', 'accessToken', 'access_token',
                        'jwt', 'value', 'data'):
                if key in obj:
                    result = self._find_token(obj[key], depth + 1)
                    if result:
                        return result
            for val in obj.values():
                result = self._find_token(val, depth + 1)
                if result:
                    return result
        if isinstance(obj, list):
            for item in obj:
                result = self._find_token(item, depth + 1)
                if result:
                    return result
        return None

    # ── Поиск ───────────────────────────────────────────────────────

    def _api_search(self, br, log, query, token, timeout, abort, is_isbn=False):
        """Ищет книги через JSON API. Возвращает список dict.
        
        Args:
            is_isbn: если True, не использует recommend/semantic fallback,
                     т.к. это бессмысленно для ISBN.
        """
        # ОСНОВНОЙ эндпоинт поиска — /web/api/v2/search/product
        api_url = (f'{self.API_URL}/web/api/v2/search/product'
                   f'?phrase={quote(query)}&page=1&perPage=10'
                   f'&include=authors,publisher,series,cover')
        products = self._api_get_products(br, log, api_url, token, timeout)

        # Если нет результатов — пробуем recommend/semantic как fallback
        # Но только если это НЕ поиск по ISBN (semantic не вернёт точное совпадение)
        if not products and not is_isbn:
            log.info('ChitaiGorod: v2/search/product пуст, пробуем recommend')
            api_url = (f'{self.API_URL}/web/api/v1/recommend/semantic'
                       f'?phrase={quote(query)}&page=1&perPage=10')
            products = self._api_get_products(br, log, api_url, token, timeout)

        return products

    def _api_get_products(self, br, log, url, token, timeout):
        old_headers = list(br.addheaders)
        try:
            br.addheaders = old_headers + [
                ('Authorization', f'Bearer {token}'),
                ('Accept', 'application/json'),
            ]
            resp = br.open_novisit(url, timeout=timeout)
            raw = resp.read().decode('utf-8', errors='replace')
            data = json.loads(raw)
        except Exception as e:
            log.error(f'ChitaiGorod: ошибка API-поиска: {e}')
            return []
        finally:
            br.addheaders = old_headers

        # JSON:API формат: data.relationships.products.data (IDs) + included
        products = self._parse_jsonapi_products(data, log)
        if products:
            log.info(f'ChitaiGorod: API вернул {len(products)} продуктов')
            return products

        # Fallback: старый формат — data как список
        items = data.get('data', data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            if isinstance(items, dict):
                for key in ('products', 'items', 'results', 'list'):
                    if key in items and isinstance(items[key], list):
                        items = items[key]
                        break
                else:
                    items = []
            else:
                items = []

        products = []
        for item in items:
            if not isinstance(item, dict):
                continue
            slug = item.get('slug') or item.get('url') or ''
            prod_id = item.get('id') or item.get('product_id') or ''
            if '/' in str(slug):
                slug = str(slug).rstrip('/').rsplit('/', 1)[-1]
            if not slug and prod_id:
                slug = str(prod_id)
            if slug:
                products.append({
                    'slug': str(slug),
                    'id': str(prod_id) if prod_id else '',
                    'raw': item,
                })

        log.info(f'ChitaiGorod: API вернул {len(products)} продуктов')
        return products

    def _parse_jsonapi_products(self, data, log):
        """Парсит ответ API в формате JSON:API.
        
        Формат:
          data.relationships.products.data -> список {id, type}
          data.included -> массив полных объектов с attributes
          data.attributes.strategy -> 'byFullCastToNumeric' и т.п.
        """
        if not isinstance(data, dict):
            return []

        included = data.get('included', [])
        if not included:
            return []

        # Строим карту ID -> full object из included
        included_map = {}
        for item in included:
            if isinstance(item, dict) and item.get('type') == 'product':
                pid = item.get('id', '')
                if pid:
                    included_map[str(pid)] = item

        # Получаем product IDs из relationships
        main_data = data.get('data', {})
        if not isinstance(main_data, dict):
            return []

        # Путь 1: data.relationships.products.data
        relationships = main_data.get('relationships', {})
        if isinstance(relationships, dict):
            prod_rel = relationships.get('products', {})
            if isinstance(prod_rel, dict):
                prod_data = prod_rel.get('data', [])
                if isinstance(prod_data, list):
                    return self._build_products_from_ids(
                        prod_data, included_map, log)

        # Путь 2: data.included напрямую как продукты
        products = []
        for item in included:
            if isinstance(item, dict) and item.get('type') == 'product':
                attrs = item.get('attributes', item)
                products.append({
                    'slug': self._extract_slug_from_attrs(attrs),
                    'id': str(item.get('id', '')),
                    'raw': attrs,
                })
        return products

    def _build_products_from_ids(self, prod_ids, included_map, log):
        """Создаёт список продуктов из ID + included map."""
        products = []
        for p in prod_ids:
            if not isinstance(p, dict):
                continue
            pid = str(p.get('id', ''))
            if not pid:
                continue
            full = included_map.get(pid)
            if full:
                attrs = full.get('attributes', full)
                products.append({
                    'slug': self._extract_slug_from_attrs(attrs),
                    'id': pid,
                    'raw': attrs,
                })
        return products

    def _extract_slug_from_attrs(self, attrs):
        """Извлекает slug из атрибутов продукта (JSON:API)."""
        if not isinstance(attrs, dict):
            return ''
        # Поле url: "product/python-bystryy-start-2839795"
        url = attrs.get('url', '')
        if url and isinstance(url, str):
            slug = url.strip('/')
            if '/' in slug:
                slug = slug.rsplit('/', 1)[-1]
            if slug:
                return slug
        # Поле slug
        slug = attrs.get('slug', '')
        if slug and isinstance(slug, str):
            slug = slug.strip('/')
            if '/' in slug:
                slug = slug.rsplit('/', 1)[-1]
            if slug:
                return slug
        # Поле code (совпадает с id для продуктов)
        code = attrs.get('code', '')
        if code:
            return str(code)
        return ''

    def _html_search(self, br, log, query, timeout, abort):
        """Ищет книги через HTML-страницу поиска.
        
        Для SPA-сайта также пытается извлечь данные из встроенного JSON.
        """
        url = f'{self.BASE_URL}/search/?q={quote(query)}'
        old_headers = list(br.addheaders)
        try:
            br.addheaders = old_headers
            resp = br.open_novisit(url, timeout=timeout)
            html = resp.read().decode('utf-8', errors='replace')
        except Exception as e:
            log.error(f'ChitaiGorod: ошибка HTML-поиска: {e}')
            return []
        finally:
            br.addheaders = old_headers

        products = []

        # 1. Обычные ссылки на продукты
        for m in re.finditer(r'href="/product/([^"?]+)', html):
            if abort.is_set():
                return []
            slug = m.group(1).split('?')[0]
            products.append({'slug': slug, 'id': '', 'raw': {}})

        # 2. Извлекаем JSON-данные из Nuxt/NUXT
        for m in re.finditer(r'window\.__NUXT__\s*=\s*(.*?);</script>',
                              html, re.DOTALL):
            if abort.is_set():
                return []
            try:
                nuxt = json.loads(m.group(1))
                self._extract_products_from_nuxt(nuxt, products)
            except (json.JSONDecodeError, ValueError):
                pass

        # 3. Ищем JSON с product data в других script тегах
        for m in re.finditer(r'"slug":"([^"]+)","id":(\d+)', html):
            if abort.is_set():
                return []
            slug = m.group(1)
            prod_id = m.group(2)
            products.append({
                'slug': slug, 'id': prod_id, 'raw': {},
            })

        # Убираем дубликаты
        seen = set()
        unique = []
        for p in products:
            key = (p['slug'], p['id'])
            if key not in seen:
                seen.add(key)
                unique.append(p)

        log.info(f'ChitaiGorod: HTML-поиск вернул {len(unique)} ссылок')
        return unique

    def _extract_products_from_nuxt(self, data, products):
        """Рекурсивно извлекает продукты из Nuxt JSON."""
        if not isinstance(data, dict):
            return
        for key in ('products', 'items', 'results', 'data'):
            if key in data:
                val = data[key]
                if isinstance(val, list):
                    self._parse_nuxt_products_list(val, products)
                elif isinstance(val, dict):
                    self._extract_products_from_nuxt(val, products)
        # Рекурсия по всем ключам
        for val in data.values():
            if isinstance(val, (dict, list)):
                self._extract_products_from_nuxt(val, products)

    def _parse_nuxt_products_list(self, items, products):
        """Извлекает продукты из списка Nuxt JSON."""
        for item in items:
            if not isinstance(item, dict):
                continue
            slug = item.get('slug', '')
            prod_id = item.get('id', '')
            if slug:
                if '/' in str(slug):
                    slug = str(slug).rstrip('/').rsplit('/', 1)[-1]
                products.append({
                    'slug': str(slug),
                    'id': str(prod_id) if prod_id else '',
                    'raw': item,
                })

    # ── Метаданные ─────────────────────────────────────────────────

    def _fetch_metadata(self, br, log, prod, token, timeout):
        slug = prod.get('slug', '')
        prod_id = prod.get('id', '')
        raw = prod.get('raw', {})

        # 1. Сначала пробуем API — там полные данные (аннотация, ISBN, обложка)
        if slug:
            mi = self._api_product(br, log, slug, token, timeout)
            if mi:
                log.info(f'ChitaiGorod [API]: {mi.title} — '
                         f'ISBN: {mi.identifiers.get("isbn", "-")}')
                self._set_ids(mi, slug, prod_id)
                return mi

        # 2. Если API не сработал — пробуем HTML
        if slug:
            mi = self._html_product(br, log, slug, timeout)
            if mi:
                log.info(f'ChitaiGorod [HTML]: {mi.title} — '
                         f'ISBN: {mi.identifiers.get("isbn", "-")}')
                self._set_ids(mi, slug, prod_id)
                return mi

        # 3. Fallback: парсим данные из поискового ответа (ограниченные)
        mi = self._parse_product_data(raw, log)
        if mi:
            log.info(f'ChitaiGorod [search data]: {mi.title}')
            self._set_ids(mi, slug, prod_id)
            return mi

        return None

    def _set_ids(self, mi, slug, prod_id):
        if prod_id:
            mi.set_identifier('chitaigorod', prod_id)
        elif slug:
            m = re.search(r'(\d{4,})$', slug)
            if m:
                mi.set_identifier('chitaigorod', m.group(1))
        mi.source_relevance = 1

    def _parse_product_data(self, item, log):
        """Создаёт Metadata из dict (ответ API).
        
        Поддерживает как прямой формат, так и JSON:API attributes.
        """
        if not isinstance(item, dict) or not item:
            return None

        # Если это JSON:API wrapper, извлекаем attributes
        if 'attributes' in item and 'type' in item:
            item = item['attributes']

        title = item.get('title') or item.get('name')
        if not title:
            return None

        # --- Авторы ---
        authors = []
        author_data = item.get('authors') or item.get('author') or item.get('coauthors')
        if isinstance(author_data, list):
            for a in author_data:
                name = self._parse_author_name(a)
                if name:
                    authors.append(name)
        elif author_data:
            name = self._parse_author_name(author_data)
            if name:
                authors.append(name)

        mi = Metadata(title, authors or ['Неизвестный автор'])

        # --- ISBN ---
        isbn = item.get('isbn') or item.get('ISBN')
        if isbn:
            # ISBN может быть списком: ['978-5-4461-1800-7']
            if isinstance(isbn, list) and isbn:
                isbn = isbn[0]
            isbn = re.sub(r'[^0-9Xx]', '', str(isbn))
            if len(isbn) in (10, 13):
                mi.set_identifier('isbn', isbn)

        # --- Издательство ---
        publisher = item.get('publisher') or item.get('publishing_house')
        if publisher:
            pub_name = self._parse_publisher_name(publisher)
            if pub_name:
                mi.publisher = pub_name

        # --- Серия (не в comments, в Calibre нет поля series в базовом Metadata) ---
        # series = item.get('publisherSeries') or item.get('series')

        # --- Год ---
        year = (item.get('year') or item.get('publication_year')
                or item.get('yearPublishing'))
        if year:
            try:
                mi.pubdate = datetime(int(year), 1, 1)
            except (ValueError, TypeError):
                pass

        # --- Описание ---
        desc = item.get('description') or item.get('annotation')
        if desc:
            clean = re.sub(r'<[^>]+>', '', str(desc)).strip()
            if clean and len(clean) > 20:
                mi.comments = (mi.comments + '\n' if mi.comments else '') + clean

        # --- Страницы ---
        pages = item.get('pages')
        if pages:
            mi.comments = (mi.comments + '\n' if mi.comments else '') + \
                          f'Страниц: {pages}'

        # --- Обложка ---
        cover_url = self._extract_cover_from_item(item)
        if cover_url:
            mi.cover_url = cover_url

        return mi

    def _api_product(self, br, log, slug, token, timeout):
        url = f'{self.API_URL}/web/api/v1/products/slug/{slug}'
        old_headers = list(br.addheaders)
        try:
            br.addheaders = old_headers + [
                ('Authorization', f'Bearer {token}'),
                ('Accept', 'application/json'),
            ]
            resp = br.open_novisit(url, timeout=timeout)
            raw = resp.read().decode('utf-8', errors='replace')
            data = json.loads(raw)
        except Exception as e:
            log.warning(f'ChitaiGorod [API]: ошибка для {slug}: {e}')
            return None
        finally:
            br.addheaders = old_headers

        item = data.get('data', data) if isinstance(data, dict) else data
        if not isinstance(item, dict):
            log.warning(f'ChitaiGorod [API]: data не dict для {slug}')
            return None

        # Debug: log ключевые поля
        has_isbn = 'isbn' in item
        has_desc = bool(item.get('description'))
        has_pic = bool(item.get('picture'))
        log.info(f'ChitaiGorod [API] {slug}: isbn={has_isbn} desc={has_desc} pic={has_pic}')

        mi = self._parse_product_data(item, log)
        if mi:
            log.info(f'ChitaiGorod [API]: {mi.title} — '
                     f'ISBN: {mi.identifiers.get("isbn", "-")}, '
                     f'cover: {bool(mi.cover_url)}, '
                     f'comments: {len(mi.comments)}')
        return mi

    def _html_product(self, br, log, slug, timeout):
        url = f'{self.BASE_URL}/product/{slug}'
        old_headers = list(br.addheaders)
        try:
            br.addheaders = old_headers
            resp = br.open_novisit(url, timeout=timeout)
            html = resp.read().decode('utf-8', errors='replace')
        except Exception as e:
            log.error(f'ChitaiGorod [HTML]: ошибка загрузки {slug}: {e}')
            return None
        finally:
            br.addheaders = old_headers

        title = None
        m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.IGNORECASE)
        if m:
            title = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        if not title:
            m = re.search(
                r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"',
                html, re.IGNORECASE)
            if m:
                title = m.group(1).strip()
        if not title:
            log.warning(f'ChitaiGorod [HTML]: нет названия для {slug}')
            return None

        author = None
        for pat in [
            r'"author"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]*)"',
            r'"firstName"\s*:\s*"([^"]*)"',
            r'<meta[^>]*itemprop="author"[^>]*content="([^"]*)"',
            r'class="[^"]*author[^"]*"[^>]*>([^<]+)<',
        ]:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                author = m.group(1).strip()
                break

        mi = Metadata(title, [author] if author else ['Неизвестный автор'])

        m = re.search(r'ISBN[:\s]*([0-9Xx\-]{10,17})\b', html, re.IGNORECASE)
        if m:
            isbn = re.sub(r'[^0-9Xx]', '', m.group(1))
            if len(isbn) in (10, 13):
                mi.set_identifier('isbn', isbn)

        # Издательство — несколько паттернов для разных вариантов HTML
        for pat in [
            r'Издательство[^>]*>.*?<[^>]*>([^<]+)',
            r'Издательство\s*[:\s]*\s*([^<\n]+)',
            r'<meta[^>]*itemprop="publisher"[^>]*content="([^"]*)"',
        ]:
            m = re.search(pat, html, re.DOTALL | re.IGNORECASE)
            if m:
                mi.publisher = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                if mi.publisher:
                    break

        m = re.search(
            r'Год[^>]*>.*?(\d{4})',
            html, re.DOTALL | re.IGNORECASE)
        if m:
            try:
                mi.pubdate = datetime(int(m.group(1)), 1, 1)
            except (ValueError, TypeError):
                pass

        # --- Аннотация (описание) ---
        desc = self._extract_annotation(html, log)
        if desc and len(desc) > 20:
            mi.comments = desc

        m = re.search(
            r'<meta[^>]*property="og:image"[^>]*content="([^"]*)"',
            html, re.IGNORECASE)
        if m:
            mi.cover_url = m.group(1)

        log.info(f'ChitaiGorod [HTML]: {title} — '
                 f'ISBN: {mi.identifiers.get("isbn", "-")}')
        return mi

    def download_cover(self, log, result_queue, abort,
                       title=None, authors=None, identifiers={}, timeout=30):
        """Скачивает обложку по ISBN или slug/chitaigorod id.
        
        Calibre передаёт title, authors, identifiers, timeout — мы ищем
        продукт через API и скачиваем обложку.
        """
        br = self.browser
        isbn = identifiers.get('isbn', None)
        chg_id = identifiers.get('chitaigorod', None)

        log.info(f'ChitaiGorod [cover]: start chg_id={chg_id} isbn={isbn}')

        url = None

        # 1. Сначала ищем продукт по ISBN — получаем slug
        if isbn:
            log.info(f'ChitaiGorod [cover]: search by ISBN={isbn}')
            token = self._get_anon_token(br, log, timeout)
            if token:
                products = self._api_search(br, log, str(isbn), token,
                                            timeout, abort, is_isbn=True)
                if products:
                    slug = products[0].get('slug', '')
                    if slug:
                        log.info(f'ChitaiGorod [cover]: found slug={slug} by ISBN')
                        url = self._fetch_cover_from_api(log, slug, timeout, abort)
                        if url:
                            log.info(f'ChitaiGorod [cover]: API cover for slug={slug}')
                        else:
                            url = self._fetch_cover_from_html(log, slug, timeout, abort)
                            if url:
                                log.info(f'ChitaiGorod [cover]: HTML cover for slug={slug}')
                    else:
                        log.error('ChitaiGorod [cover]: search returned no slug')
                else:
                    log.error('ChitaiGorod [cover]: search by ISBN returned no products')

        # 2. Если ISBN не помог — пробуем chg_id (числовой ID)
        if not url and chg_id:
            log.info(f'ChitaiGorod [cover]: trying chg_id={chg_id}')
            url = self._fetch_cover_from_api(log, str(chg_id), timeout, abort)
            if url:
                log.info(f'ChitaiGorod [cover]: found via chg_id API')
            else:
                url = self._fetch_cover_from_html(log, str(chg_id), timeout, abort)
                if url:
                    log.info(f'ChitaiGorod [cover]: found via chg_id HTML')

        # 3. По title+authors — ищем через API
        if not url:
            query_parts = []
            if title:
                query_parts.append(title)
            if authors:
                query_parts.extend(authors[:2])
            if query_parts:
                query = ' '.join(query_parts)
                log.info(f'ChitaiGorod [cover]: search by query={query}')
                token = self._get_anon_token(br, log, timeout)
                if token:
                    products = self._api_search(br, log, query, token,
                                                timeout, abort)
                    if products:
                        slug = products[0].get('slug', '')
                        if slug:
                            url = self._fetch_cover_from_api(log, slug, timeout, abort)
                            if url:
                                log.info(f'ChitaiGorod [cover]: found via query')
                            else:
                                url = self._fetch_cover_from_html(log, slug, timeout, abort)

        if not url:
            log.error('ChitaiGorod [cover]: обложка не найдена')
            result_queue.put((self, None))
            return

        try:
            resp = br.open_novisit(url, timeout=timeout)
            cover_data = resp.read()
            log.info(f'ChitaiGorod [cover]: загружена ({len(cover_data)} байт)')
            result_queue.put((self, cover_data))
        except Exception as e:
            log.error(f'ChitaiGorod [cover]: ошибка загрузки: {e}')
            result_queue.put((self, None))
            return

    def _fetch_cover_from_api(self, log, slug, timeout, abort):
        """Пытается получить обложку через API продукта."""
        api_url = f'{self.API_URL}/web/api/v1/products/slug/{slug}'
        br = self.browser

        log.info(f'ChitaiGorod [cover API]: request {api_url}')

        # Пытаемся получить токен
        token = self._get_anon_token(br, log, timeout)
        if not token:
            log.error('ChitaiGorod [cover API]: не удалось получить токен')
            return None

        old_headers = list(br.addheaders)
        try:
            br.addheaders = old_headers + [
                ('Authorization', f'Bearer {token}'),
                ('Accept', 'application/json'),
            ]
            resp = br.open_novisit(api_url, timeout=timeout)
            raw = resp.read().decode('utf-8', errors='replace')
            data = json.loads(raw)
        except Exception as e:
            log.error(f'ChitaiGorod [cover API]: ошибка: {e}')
            return None
        finally:
            br.addheaders = old_headers

        item = data.get('data', data) if isinstance(data, dict) else data
        if not isinstance(item, dict):
            log.error(f'ChitaiGorod [cover API]: data не dict')
            return None

        # Debug: log ключевые поля
        has_pic = bool(item.get('picture'))
        has_desc = bool(item.get('description'))
        log.info(f'ChitaiGorod [cover API]: response picture={has_pic} desc={has_desc}')

        cover_url = self._extract_cover_from_item(item)
        if cover_url:
            log.info(f'ChitaiGorod [cover API]: cover_url={cover_url}')
        else:
            log.error(f'ChitaiGorod [cover API]: cover_url не найден, keys={list(item.keys())[:15]}')
        return cover_url

    def _fetch_cover_from_html(self, log, slug, timeout, abort):
        """Пытается получить обложку из HTML страницы продукта."""
        html_url = f'{self.BASE_URL}/product/{slug}'
        br = self.browser

        log.info(f'ChitaiGorod [cover HTML]: request {html_url}')

        old_headers = list(br.addheaders)
        try:
            br.addheaders = old_headers
            resp = br.open_novisit(html_url, timeout=timeout)
            html = resp.read().decode('utf-8', errors='replace')
        except Exception as e:
            log.error(f'ChitaiGorod [cover HTML]: ошибка: {e}')
            return None
        finally:
            br.addheaders = old_headers

        log.info(f'ChitaiGorod [cover HTML]: HTML len={len(html)}')

        # og:image
        m = re.search(
            r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']*)["\']',
            html, re.IGNORECASE)
        if m:
            cover = m.group(1).strip()
            normalized = self._normalize_cover_url(cover)
            if normalized:
                log.info(f'ChitaiGorod [cover HTML]: og:image={normalized}')
                return normalized
            else:
                log.info(f'ChitaiGorod [cover HTML]: og:image not normalized: {cover}')

        # Изображение в h1 или рядом
        m = re.search(
            r'<h1[^>]*>.*?<img[^>]*src=["\']([^"\']*)["\']',
            html, re.DOTALL | re.IGNORECASE)
        if m:
            cover = m.group(1).strip()
            normalized = self._normalize_cover_url(cover)
            if normalized:
                log.info(f'ChitaiGorod [cover HTML]: h1>img={normalized}')
                return normalized

        # Первое изображение с обложкой (по alt/class)
        for pat in [
                r'<img[^>]*alt=["\'].*?обложка.*?["\'][^>]*src=["\']([^"\']*)["\']',
                r'<img[^>]*src=["\']([^"\']*)["\'][^>]*alt=["\'].*?обложка.*?["\']',
                r'<img[^>]*class=["\'][^"\']*cover[^"\']*["\'][^>]*src=["\']([^"\']*)["\']',
                r'<img[^>]*src=["\']([^"\']*)["\'][^>]*class=["\'][^"\']*cover[^"\']*["\']']:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                cover = m.group(1).strip()
                normalized = self._normalize_cover_url(cover)
                if normalized:
                    log.info(f'ChitaiGorod [cover HTML]: pattern match={normalized}')
                    return normalized

        log.error('ChitaiGorod [cover HTML]: обложка не найдена в HTML')
        return None
