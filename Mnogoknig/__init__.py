# -*- coding: utf-8 -*-
"""
Mnogoknig Metadata Source Plugin for Calibre
Search and fetch metadata & covers from mnogoknig.com/ru
"""

__license__ = 'GPL v3'
__copyright__ = '2026, Mnogoknig Plugin Author'
__docformat__ = "plaintext"

import re
from urllib.parse import quote

from calibre.ebooks.metadata.sources.base import Source
from calibre.ebooks.metadata.book.base import Metadata


BASE_URL = 'https://mnogoknig.com'


class Mnogoknig(Source):
    name = 'Mnogoknig'
    author = 'GigaCode'
    description = u'Fetch metadata from mnogoknig.com/ru'
    version = (1, 0, 2)
    minimum_calibre_version = (8, 9, 0)

    capabilities = frozenset(['identify', 'cover'])
    touched_fields = frozenset([
        'title', 'authors', 'author_sort', 'identifiers',
        'comments', 'series', 'series_index', 'pubdate',
        'languages', 'publisher',
    ])

    search_results_limit = 10

    def _fetch(self, log, url, timeout=30):
        """Fetch URL using curl - more reliable than Python SSL"""
        import subprocess
        
        try:
            cmd = [
                'curl', '-s', '-L', '--connect-timeout', '10', '--max-time', str(timeout),
                '-A', 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                '-H', 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                '-H', 'Accept-Language: ru-RU,ru;q=0.9,en;q=0.8',
                url
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
            if result.returncode == 0 and result.stdout:
                return result.stdout.decode('utf-8', errors='replace')
            else:
                log.error('Curl failed: %s' % result.stderr.decode()[:200])
                return None
        except Exception as e:
            log.error('Curl fetch failed for %s: %s' % (url, e))
            return None

    def identify(self, log, result_queue, abort, title=None, authors=None,
                 identifiers=None, timeout=30):
        """Search for books on mnogoknig.com"""
        if identifiers is None:
            identifiers = {}

        log.info('Identify: searching mnogoknig for: %s by %s' % (title, authors))

        # Check if we have a mnogoknig product ID in identifiers
        mn_id = identifiers.get('mnogoknig', '')
        if mn_id:
            product_url = '%s/ru/products/%s' % (BASE_URL, mn_id)
            log.info('Identify: Direct product lookup: %s' % product_url)
            self._get_book_from_url(log, result_queue, product_url, timeout)
            return

        # Perform search
        query = title or ''
        if authors:
            author_str = ' '.join(authors)
            query = '%s %s' % (query, author_str)
        query = query.strip()
        if not query:
            log.info('Identify: No search query provided')
            return

        # URL-encode the query
        from urllib.parse import quote
        search_url = '%s/ru/search?query=%s' % (BASE_URL, quote(query.encode('utf-8')))
        log.info('Identify: Search URL: %s' % search_url)

        html = self._fetch(log, search_url, timeout)
        if not html:
            log.info('Identify: No search results')
            return

        # Parse search results
        self._parse_search_results(log, result_queue, html, timeout)

    def _parse_search_results(self, log, result_queue, html, timeout):
        """Parse search results HTML and add to result queue"""
        # Pattern: wire:key="add-product-ID" aria-label="Add to wishlist: Title"
        product_pattern = r'wire:key="add-product-(\d+)"[^>]*aria-label="[^:]+:\s*([^"]*)"'

        matches = re.findall(product_pattern, html)

        seen = set()
        count = 0
        for product_id, title in matches:
            if product_id in seen:
                continue
            seen.add(product_id)

            if count >= self.search_results_limit:
                break
            count += 1

            product_url = '%s/ru/products/%s' % (BASE_URL, product_id)
            log.info('Identify: Found product from search: %s (ID: %s)' % (title, product_id))
            self._get_book_from_url(log, result_queue, product_url, timeout)

    def _get_book_from_url(self, log, result_queue, product_url, timeout):
        """Fetch book details from product page"""
        log.info('Get book from URL: %s' % product_url)

        html = self._fetch(log, product_url, timeout)
        if not html:
            log.error('No HTML for product page')
            return

        # Extract metadata from HTML
        mi, cover_url = self._extract_metadata(html, log)
        if mi:
            # Add mnogoknig identifier
            product_id = re.search(r'/products/(\d+)', product_url)
            if product_id:
                if mi.identifiers is None:
                    mi.identifiers = {}
                mi.identifiers['mnogoknig'] = product_id.group(1)

            # Cache cover URL
            if cover_url:
                if product_id:
                    self.cache_identifier_to_cover_url(
                        product_id.group(1), cover_url
                    )

            result_queue.put(mi)
            log.info('Identify: Found book: %s by %s' % (mi.title, mi.authors))

    def _extract_metadata(self, html, log):
        """Extract book metadata from product page HTML"""
        # Extract title from page title
        meta_title = re.search(r'<title>([^<]+)</title>', html)
        if not meta_title:
            return None, None

        title = meta_title.group(1)
        # Clean up: remove site name
        title = re.sub(r'\s*\|\s*MnogoKnig\.com.*$', '', title)
        title = re.sub(r'\s*\|\s*.*$', '', title)
        title = title.strip()

        if not title:
            return None, None

        # Strip author prefix from title: "Author — Title" or "Author - Title"
        # Use em-dash (—) or en-dash (–) as separator
        title = re.sub(r'^.+?[—–]\s*', '', title)
        title = title.strip()

        if not title or len(title) < 3:
            return None, None

        # Fallback: if title looks like a publisher name, try h1
        publisher_match = re.search(
            r'<span[^>]*class="[^"]*font-semibold[^"]*"[^>]*>Издательство:</span>\s*'
            r'[^<]*<a[^>]*>([^<]+)</a>',
            html, re.DOTALL
        )
        if publisher_match and title.lower() == publisher_match.group(1).strip().lower():
            h1 = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
            if h1:
                title = re.sub(r'<[^>]+>', '', h1.group(1)).strip()
                title = re.sub(r'^.+?[—–]\s*', '', title).strip()
                if not title or len(title) < 3:
                    return None, None

        # Extract authors
        authors = []
        author_matches = re.findall(
            r'<span[^>]*class="[^"]*font-semibold[^"]*"[^>]*>Автор:</span>\s*'
            r'<a[^>]*>([^<]+)</a>',
            html, re.DOTALL
        )
        for author in author_matches:
            author = author.strip()
            if author:
                # Split comma or & separated authors
                for a in re.split(r'[,&]', author):
                    a = a.strip()
                    if a:
                        authors.append(a)

        if not authors:
            authors = ['Unknown']

        mi = Metadata(title, authors=authors)
        cover_url = None

        # Extract ISBN
        isbn_match = re.search(
            r'<span[^>]*class="[^"]*font-semibold[^"]*"[^>]*>ISBN:</span>\s*'
            r'\s*([^<]+)',
            html, re.DOTALL
        )
        if isbn_match:
            isbn = isbn_match.group(1).strip()
            if isbn:
                if mi.identifiers is None:
                    mi.identifiers = {}
                mi.identifiers['isbn'] = isbn

        # Extract publisher
        publisher_match = re.search(
            r'<span[^>]*class="[^"]*font-semibold[^"]*"[^>]*>Издательство:</span>\s*'
            r'[^<]*<a[^>]*>([^<]+)</a>',
            html, re.DOTALL
        )
        if publisher_match:
            mi.publisher = publisher_match.group(1).strip()

        # Extract series and series_index
        series_match = re.search(
            r'<span[^>]*class="[^"]*font-semibold[^"]*"[^>]*>Серия:</span>\s*'
            r'(?:<a[^>]*>)?([^<]+)',
            html, re.DOTALL
        )
        if series_match:
            series_text = re.sub(r'\s+', ' ', series_match.group(1)).strip()
            # Extract series index: "Series Name #1" or "Series Name 1" or "Series Name №1"
            idx_match = re.search(r'^(.+?)\s*[#№]\s*(\d+)\s*$', series_text)
            if idx_match:
                mi.series = idx_match.group(1).strip()
                try:
                    mi.series_index = int(idx_match.group(2))
                except ValueError:
                    mi.series = series_text
            else:
                mi.series = series_text

        # Extract year
        year_match = re.search(
            r'<span[^>]*class="[^"]*font-semibold[^"]*"[^>]*>Год издания:</span>\s*'
            r'\s*([^<]+)',
            html, re.DOTALL
        )
        if year_match:
            year = year_match.group(1).strip()
            if year:
                try:
                    from calibre.utils.date import as_utc
                    from datetime import datetime
                    mi.pubdate = as_utc(datetime.strptime(year, '%Y'))
                except Exception:
                    pass

        # Extract comments/annotation
        meta_desc = re.search(
            r'<meta[^>]*itemprop="description"[^>]*content="([^"]*)"', html
        )
        if meta_desc:
            mi.comments = meta_desc.group(1)

        # Extract cover URL
        cover_match = re.search(
            r'<img[^>]*src="(https://static\.mnogoknig\.com/media/[^"]*\.webp)"[^>]*'
            r'alt="([^"]*)"[^>]*data-fancybox="gallery"',
            html
        )
        if cover_match:
            cover_url = cover_match.group(1)
            log.info('Cover URL: %s' % cover_url)

        return mi, cover_url

    def download_cover(self, log, result_queue, abort, title=None, authors=None,
                       identifiers=None, timeout=30):
        """Download cover for a book from mnogoknig.com"""
        if identifiers is None:
            identifiers = {}

        log.info('Download cover: %s' % title)

        cover_url = None

        # Check for mnogoknig identifier
        mn_id = identifiers.get('mnogoknig', '')

        if mn_id:
            # Try cached cover URL first
            cover_url = self.cached_identifier_to_cover_url(mn_id)
            if cover_url:
                log.info('Using cached cover URL: %s' % cover_url)
            else:
                # Fetch product page to get cover
                product_url = '%s/ru/products/%s' % (BASE_URL, mn_id)
                log.info('Fetching product page for cover: %s' % product_url)
                html = self._fetch(log, product_url, timeout)
                if html:
                    cover_match = re.search(
                        r'<img[^>]*src="(https://static\.mnogoknig\.com/media/[^"]*\.webp)"[^>]*'
                        r'alt="[^"]*"[^>]*data-fancybox="gallery"',
                        html
                    )
                    if cover_match:
                        cover_url = cover_match.group(1)
                        log.info('Found cover URL: %s' % cover_url)
                        # Cache it
                        self.cache_identifier_to_cover_url(mn_id, cover_url)

        # If no cover URL from identifier, search
        if not cover_url:
            query = title or ''
            if authors:
                query = '%s %s' % (query, ' '.join(authors))

            search_url = '%s/ru/search?query=%s' % (BASE_URL, quote(query.encode('utf-8')))
            log.info('Cover search URL: %s' % search_url)

            html = self._fetch(log, search_url, timeout)
            if html:
                cover_match = re.search(
                    r'<img[^>]*src="(https://static\.mnogoknig\.com/media/[^"]*\.webp)"[^>]*'
                    r'alt="[^"]*"[^>]*data-fancybox="gallery"',
                    html
                )
                if cover_match:
                    cover_url = cover_match.group(1)

        if not cover_url:
            log.info('No cover found')
            return

        # Download cover as binary data
        import subprocess
        
        log.info('Cover download: fetching %s' % cover_url)
        
        try:
            cmd = [
                'curl', '-s', '-L', '--connect-timeout', '10', '--max-time', str(timeout),
                '-A', 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                cover_url
            ]
            log.info('Cover download: curl cmd: %s' % ' '.join(cmd[:3]) + ' ...')
            
            result = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
            raw_data = result.stdout
            
            log.info('Cover download: curl returned %d bytes, returncode=%d' % (len(raw_data), result.returncode))
            if result.stderr:
                log.info('Cover download: curl stderr: %s' % result.stderr.decode()[:200])
            
            if raw_data:
                # Convert WebP to PNG using PIL (calibre doesn't support WebP)
                try:
                    from PIL import Image
                    import io
                    img = Image.open(io.BytesIO(raw_data))
                    buf = io.BytesIO()
                    img.save(buf, format='PNG')
                    raw_data = buf.getvalue()
                    log.info('Cover download: converted WebP to PNG, %d bytes' % len(raw_data))
                except Exception as e:
                    log.info('Cover download: PIL conversion failed: %s, using webp' % e)
                
                # Return raw bytes - calibre will auto-detect format
                result_queue.put((self, raw_data))
                log.info('Cover downloaded successfully (%d bytes)' % len(raw_data))
            else:
                log.error('Cover download: no data received from curl')
        except subprocess.TimeoutExpired:
            log.error('Cover download: curl timed out')
        except Exception as e:
            log.error('Cover download failed: %s' % e)
            import traceback
            log.error(traceback.format_exc())
