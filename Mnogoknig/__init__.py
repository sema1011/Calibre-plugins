# -*- coding: utf-8 -*-
"""
Mnogoknig Metadata Source Plugin for Calibre
Search and fetch metadata & covers from mnogoknig.com/ru
"""

__license__ = 'GPL v3'
__copyright__ = '2026, Mnogoknig Plugin Author'
__docformat__ = "plaintext"

import html as html_module
import io
import re
import subprocess
from datetime import datetime
from urllib.parse import quote

from calibre.ebooks.metadata.sources.base import Source
from calibre.ebooks.metadata.book.base import Metadata
from calibre.utils.date import as_utc

try:
    from PIL import Image
except ImportError:
    Image = None


BASE_URL = 'https://mnogoknig.com'


class Mnogoknig(Source):
    name = 'Mnogoknig'
    author = 'GigaCode'
    description = u'Fetch metadata from mnogoknig.com/ru'
    version = (1, 0, 3)
    minimum_calibre_version = (8, 9, 0)

    capabilities = frozenset(['identify', 'cover'])
    touched_fields = frozenset([
        'title', 'authors', 'author_sort', 'identifiers',
        'comments', 'series', 'series_index', 'pubdate',
        'languages', 'publisher',
    ])

    search_results_limit = 10

    # ── URL / identifier helpers ──────────────────────────────────

    def get_book_url(self, identifiers):
        """Return a link to the book on mnogoknig.com."""
        mn_id = identifiers.get('mnogoknig')
        if mn_id:
            return ('mnogoknig', mn_id, f'{BASE_URL}/ru/products/{mn_id}')
        return None

    def get_book_url_name(self, idtype, idval, url):
        return 'Mnogoknig'

    def get_cached_cover_url(self, identifiers):
        """Return cached cover URL for the book identified by identifiers."""
        mn_id = identifiers.get('mnogoknig')
        if mn_id and hasattr(self, 'cached_identifier_to_cover_url'):
            return self.cached_identifier_to_cover_url(mn_id)
        return None

    def id_from_url(self, url):
        """Parse URL and return (identifier_type, identifier_value)."""
        m = re.search(r'/products/(\d+)', url)
        if m:
            return ('mnogoknig', m.group(1))
        return None

    # ── HTTP (curl) ───────────────────────────────────────────────

    def _fetch(self, log, url, timeout=30, abort=None):
        """Fetch URL using curl — more reliable than Python SSL."""
        if abort is not None and abort.is_set():
            return None
        try:
            cmd = [
                'curl', '-s', '-L',
                '--connect-timeout', '10',
                '--max-time', str(timeout),
                '-A', 'Mozilla/5.0 (X11; Linux x86_64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36',
                '-H', 'Accept: text/html,application/xhtml+xml,'
                      'application/xml;q=0.9,*/*;q=0.8',
                '-H', 'Accept-Language: ru-RU,ru;q=0.9,en;q=0.8',
                url,
            ]
            result = subprocess.run(cmd, capture_output=True,
                                    timeout=timeout + 2)
            if result.returncode == 0 and result.stdout:
                return result.stdout.decode('utf-8', errors='replace')
            log.error('Curl failed: %s' %
                      result.stderr.decode('utf-8', errors='replace')[:200])
            return None
        except Exception as e:
            log.error('Curl fetch failed for %s: %s' % (url, e))
            return None

    # ── identify ──────────────────────────────────────────────────

    def identify(self, log, result_queue, abort, title=None, authors=None,
                 identifiers=None, timeout=30):
        """Search for books on mnogoknig.com."""
        if identifiers is None:
            identifiers = {}

        log.info('Identify: searching mnogoknig for: %s by %s' %
                 (title, authors))

        # Direct product lookup by mnogoknig id
        mn_id = identifiers.get('mnogoknig', '')
        if mn_id:
            product_url = '%s/ru/products/%s' % (BASE_URL, mn_id)
            log.info('Identify: Direct product lookup: %s' % product_url)
            self._get_book_from_url(log, result_queue, product_url, timeout,
                                    source_relevance=0, abort=abort)
            return

        # Search
        query = title or ''
        if authors:
            query = '%s %s' % (query, ' '.join(authors))
        query = query.strip()
        if not query:
            log.info('Identify: No search query provided')
            return

        search_url = '%s/ru/search?query=%s' % (BASE_URL, quote(query))
        log.info('Identify: Search URL: %s' % search_url)

        html = self._fetch(log, search_url, timeout, abort)
        if not html:
            log.info('Identify: No search results')
            return

        self._parse_search_results(log, result_queue, html, timeout, abort)

    def _parse_search_results(self, log, result_queue, html, timeout, abort):
        """Parse search results HTML and add to result queue."""
        product_pattern = (
            r'wire:key="add-product-(\d+)"[^>]*'
            r'aria-label="[^:]+:\s*([^"]*)"'
        )
        matches = re.findall(product_pattern, html)

        seen = set()
        count = 0
        for product_id, title in matches:
            if abort.is_set():
                return
            if product_id in seen:
                continue
            seen.add(product_id)

            if count >= self.search_results_limit:
                break
            count += 1

            product_url = '%s/ru/products/%s' % (BASE_URL, product_id)
            log.info('Identify: Found product from search: %s (ID: %s)' %
                     (title, product_id))
            self._get_book_from_url(
                log, result_queue, product_url, timeout,
                source_relevance=count - 1, abort=abort,
            )

    def _get_book_from_url(self, log, result_queue, product_url, timeout,
                           source_relevance=0, abort=None):
        """Fetch book details from product page."""
        log.info('Get book from URL: %s' % product_url)

        html = self._fetch(log, product_url, timeout, abort)
        if not html:
            log.error('No HTML for product page')
            return

        mi, cover_url = self._extract_metadata(html, log)
        if not mi:
            return

        product_id = re.search(r'/products/(\d+)', product_url)
        if product_id:
            mi.set_identifier('mnogoknig', product_id.group(1))
            if cover_url and hasattr(self, 'cache_identifier_to_cover_url'):
                self.cache_identifier_to_cover_url(
                    product_id.group(1), cover_url)

        mi.source_relevance = source_relevance
        self.clean_downloaded_metadata(mi)
        result_queue.put(mi)
        log.info('Identify: Found book: %s by %s' % (mi.title, mi.authors))

    # ── metadata extraction ──────────────────────────────────────

    def _extract_metadata(self, html, log):
        """Extract book metadata from product page HTML."""
        meta_title = re.search(r'<title>([^<]+)</title>', html)
        if not meta_title:
            return None, None

        title = meta_title.group(1)
        # Remove site name only (не трогаем | и — в самом названии)
        title = re.sub(r'\s*\|\s*MnogoKnig\.com.*$', '', title,
                       flags=re.IGNORECASE)
        title = title.strip()

        # Приоритет: <h1> содержит чистое название без author prefix
        h1 = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
        if h1:
            h1_title = re.sub(r'<[^>]+>', '', h1.group(1)).strip()
            if h1_title and len(h1_title) >= 3:
                title = h1_title

        if not title or len(title) < 3:
            return None, None

        # Authors
        authors = []
        author_matches = re.findall(
            r'<span[^>]*class="[^"]*font-semibold[^"]*"[^>]*>Автор:</span>\s*'
            r'<a[^>]*>([^<]+)</a>',
            html, re.DOTALL,
        )
        for author in author_matches:
            author = author.strip()
            if author:
                for a in re.split(r'[,&]', author):
                    a = a.strip()
                    if a:
                        authors.append(a)

        if not authors:
            authors = ['Неизвестный автор']

        mi = Metadata(title, authors=authors)
        cover_url = None

        # ISBN
        isbn_match = re.search(
            r'<span[^>]*class="[^"]*font-semibold[^"]*"[^>]*>ISBN:</span>\s*'
            r'\s*([^<]+)',
            html, re.DOTALL,
        )
        if isbn_match:
            isbn = isbn_match.group(1).strip()
            if isbn:
                mi.set_identifier('isbn', isbn)

        # Publisher
        publisher_match = re.search(
            r'<span[^>]*class="[^"]*font-semibold[^"]*"[^>]*>Издательство:</span>\s*'
            r'[^<]*<a[^>]*>([^<]+)</a>',
            html, re.DOTALL,
        )
        if publisher_match:
            mi.publisher = publisher_match.group(1).strip()

        # Series + series_index
        series_match = re.search(
            r'<span[^>]*class="[^"]*font-semibold[^"]*"[^>]*>Серия:</span>\s*'
            r'(?:<a[^>]*>)?([^<]+)',
            html, re.DOTALL,
        )
        if series_match:
            series_text = re.sub(r'\s+', ' ', series_match.group(1)).strip()
            idx_match = re.search(r'^(.+?)\s*[#№]\s*(\d+)\s*$', series_text)
            if idx_match:
                mi.series = idx_match.group(1).strip()
                try:
                    mi.series_index = int(idx_match.group(2))
                except ValueError:
                    mi.series = series_text
            else:
                mi.series = series_text

        # Year
        year_match = re.search(
            r'<span[^>]*class="[^"]*font-semibold[^"]*"[^>]*>Год издания:</span>\s*'
            r'\s*([^<]+)',
            html, re.DOTALL,
        )
        if year_match:
            year = year_match.group(1).strip()
            if year:
                try:
                    mi.pubdate = as_utc(datetime.strptime(year, '%Y'))
                except Exception:
                    pass

        # Comments
        meta_desc = re.search(
            r'<meta[^>]*itemprop="description"[^>]*content="([^"]*)"', html,
        )
        if meta_desc:
            mi.comments = html_module.unescape(meta_desc.group(1))

        # Cover URL
        cover_match = re.search(
            r'<img[^>]*src="(https://static\.mnogoknig\.com/media/[^"]*\.webp)"'
            r'[^>]*alt="([^"]*)"[^>]*data-fancybox="gallery"',
            html,
        )
        if cover_match:
            cover_url = cover_match.group(1)
            log.info('Cover URL: %s' % cover_url)

        return mi, cover_url

    # ── download_cover ────────────────────────────────────────────

    def download_cover(self, log, result_queue, abort, title=None, authors=None,
                       identifiers=None, timeout=30, get_best_cover=False):
        """Download cover for a book from mnogoknig.com."""
        if identifiers is None:
            identifiers = {}

        log.info('Download cover: %s' % title)

        cover_url = None
        mn_id = identifiers.get('mnogoknig', '')

        # 1) Try cached cover URL
        if mn_id and hasattr(self, 'cached_identifier_to_cover_url'):
            cover_url = self.cached_identifier_to_cover_url(mn_id)
            if cover_url:
                log.info('Using cached cover URL: %s' % cover_url)

        # 2) Fetch product page by id
        if not cover_url and mn_id:
            product_url = '%s/ru/products/%s' % (BASE_URL, mn_id)
            log.info('Fetching product page for cover: %s' % product_url)
            html = self._fetch(log, product_url, timeout, abort)
            if html:
                cover_match = re.search(
                    r'<img[^>]*src="(https://static\.mnogoknig\.com/media/[^"]*\.webp)"'
                    r'[^>]*alt="[^"]*"[^>]*data-fancybox="gallery"',
                    html,
                )
                if cover_match:
                    cover_url = cover_match.group(1)
                    log.info('Found cover URL: %s' % cover_url)
                    if hasattr(self, 'cache_identifier_to_cover_url'):
                        self.cache_identifier_to_cover_url(mn_id, cover_url)

        # 3) Search by title + authors
        if not cover_url:
            query = title or ''
            if authors:
                query = '%s %s' % (query, ' '.join(authors))

            search_url = '%s/ru/search?query=%s' % (BASE_URL, quote(query))
            log.info('Cover search URL: %s' % search_url)

            html = self._fetch(log, search_url, timeout, abort)
            if html:
                cover_match = re.search(
                    r'<img[^>]*src="(https://static\.mnogoknig\.com/media/[^"]*\.webp)"'
                    r'[^>]*alt="[^"]*"[^>]*data-fancybox="gallery"',
                    html,
                )
                if cover_match:
                    cover_url = cover_match.group(1)

        if not cover_url:
            log.info('No cover found')
            return

        # Download cover as binary data
        log.info('Cover download: fetching %s' % cover_url)

        try:
            cmd = [
                'curl', '-s', '-L',
                '--connect-timeout', '10',
                '--max-time', str(timeout),
                '-A', 'Mozilla/5.0 (X11; Linux x86_64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36',
                cover_url,
            ]
            result = subprocess.run(cmd, capture_output=True,
                                    timeout=timeout + 5)
            raw_data = result.stdout

            log.info('Cover download: curl returned %d bytes, returncode=%d' %
                     (len(raw_data), result.returncode))
            if result.stderr:
                log.info('Cover download: curl stderr: %s' %
                         result.stderr.decode('utf-8', errors='replace')[:200])

            if not raw_data:
                log.error('Cover download: no data received from curl')
                return

            # Convert WebP → PNG (Calibre doesn't support WebP)
            if Image is None:
                log.error('Cover download: PIL not available, cannot convert WebP')
                return
            try:
                img = Image.open(io.BytesIO(raw_data))
                buf = io.BytesIO()
                img.save(buf, format='PNG')
                raw_data = buf.getvalue()
                log.info('Cover download: converted WebP to PNG, %d bytes' %
                         len(raw_data))
            except Exception as e:
                log.error('Cover download: PIL conversion failed: %s' % e)
                return

            result_queue.put((self, raw_data))
            log.info('Cover downloaded successfully (%d bytes)' % len(raw_data))

        except subprocess.TimeoutExpired:
            log.error('Cover download: curl timed out')
        except Exception as e:
            log.error('Cover download failed: %s' % e)
