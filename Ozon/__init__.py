"""Ozon.ru Metadata Source Plugin for Calibre."""

import os
import json
import re
import time
import glob
import urllib.parse
from threading import Thread

from calibre.ebooks.metadata.sources.base import Source
from calibre.ebooks.metadata.book.base import Metadata
from calibre.utils.date import parse_only_date


class OzonMetadata(Source):
    name = 'Ozon.ru'
    description = _('Downloads book metadata from Ozon.ru')
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Calibre User'
    version = (1, 6, 0)
    minimum_calibre_version = (5, 0, 0)

    capabilities = frozenset(['identify', 'cover'])
    touched_fields = frozenset([
        'title', 'authors', 'identifier:isbn', 'comments',
        'publisher', 'pubdate', 'rating', 'tags',
        'identifier:ozon',
    ])
    has_html_comments = True
    supports_gzip_transfer_encoding = True
    ignore_ssl_errors = True

    OZON_BASE_URL = 'https://www.ozon.ru'

    # Мобильный API (не за Cloudflare) — нужен с мобильными заголовками
    MOBILE_API = 'https://api.ozon.ru/composer-api.bx/page/json/v2'
    # Веб API (за Cloudflare) — нужен curl_cffi
    WEB_API = 'https://www.ozon.ru/api/composer-api.bx/page/json/v2'

    # Заголовки мобильного приложения Ozon для Android
    # Несколько версий для fallback при блокировке
    MOBILE_HEADERS_VERSIONS = [
        {
            'x-o3-app-name': 'ozonapp_android',
            'User-Agent': 'ozonapp_android/25.12+3456',
            'x-o3-device-type': 'mobile',
            'x-o3-app-version': '25.12(3456)',
        },
        {
            'x-o3-app-name': 'ozonapp_android',
            'User-Agent': 'ozonapp_android/24.11+2890',
            'x-o3-device-type': 'mobile',
            'x-o3-app-version': '24.11(2890)',
        },
        {
            'x-o3-app-name': 'ozonapp_android',
            'User-Agent': 'ozonapp_android/23.10+2345',
            'x-o3-device-type': 'mobile',
            'x-o3-app-version': '23.10(2345)',
        },
    ]

    # Базовые заголовки (общие для всех версий)
    MOBILE_HEADERS_BASE = {
        'x-o3-protocol-version': '1',
        'Content-Type': 'application/json;charset=UTF-8',
        'Accept': 'application/json;charset=utf-8',
        'Accept-Encoding': 'gzip, deflate',
        'x-o3-sample-trace': 'false',
        'Connection': 'Keep-Alive',
        'x-o3-logger': 'analytics',
        'Cache-Control': 'no-cache',
        'x-o3-timezone': 'UTC+3',
        'x-o3-locale': 'ru',
    }

    @property
    def MOBILE_HEADERS(self):
        """Возвращает текущие мобильные заголовки (первая версия)."""
        headers = dict(self.MOBILE_HEADERS_BASE)
        headers.update(self.MOBILE_HEADERS_VERSIONS[0])
        return headers

    # Индекс текущей версии мобильных заголовков
    _mobile_headers_index = 0

    # Заголовки для веб-API (через curl_cffi)
    WEB_HEADERS = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Sec-Ch-Ua': '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Upgrade-Insecure-Requests': '1',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    }

    # Заголовки для API запросов через веб
    WEB_API_HEADERS = {
        'Accept': 'application/json',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'Content-Type': 'application/json',
        'Origin': 'https://www.ozon.ru',
        'Referer': 'https://www.ozon.ru/',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
    }

    # Заголовки для cloudscraper
    CLOUDSCRAPER_HEADERS = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/131.0.0.0 Safari/537.36',
    }

    OZON_BOOK_CATEGORIES = {
        'knigi', 'book', 'books',
        'hudozhestvennaya-literatura',
        'nauka-i-obrazovanie',
        'komiksy-i-manga',
        'delovaya-literatura',
        'detskaya-literatura',
    }

    OZON_CATEGORY_KEYWORDS = (
        'книг', 'литератур', 'издани', 'учебник',
        'художествен', 'словар', 'энциклопед',
        'manga', 'комикс', 'сказк', 'повесть',
        'роман', 'рассказ',
    )

    VENV_PATHS = [
        os.path.expanduser('~/.local/share/calibre/ozon_venv'),
        os.path.expanduser('~/.calibre/ozon_venv'),
        '/usr/local/lib/calibre/ozon_venv',
    ]

    # Профили impersonation для curl_cffi (BrowserType enum)
    # Проверить: ~/.local/share/calibre/ozon_venv/bin/python3 -c "from curl_cffi.requests import BrowserType; print([m.name for m in BrowserType])"
    IMPERSONATION_PROFILES = [
        'chrome131', 'chrome133a', 'chrome136', 'chrome142', 'chrome145',
        'chrome124', 'chrome123', 'chrome120', 'chrome119', 'chrome116',
        'chrome110', 'chrome107', 'chrome104', 'chrome101', 'chrome100',
        'chrome99',
        'edge101', 'edge99',
        'safari184', 'safari180', 'safari170',
        'firefox147', 'firefox144', 'firefox135', 'firefox133',
    ]

    _last_request_time = 0.0
    _delay_lock = None
    _curl_cffi_session = None
    _curl_cffi_available = None
    _curl_cffi_profile_index = 0
    _cf_clearance = None
    _cf_clearance_failed = False  # Кэш неудачи cf_clearance
    _cf_clearance_attempt_count = 0  # Счётчик попыток получения cf_clearance
    _cf_clearance_max_attempts = 2  # Максимум попыток на сессию

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        import threading
        self._delay_lock = threading.Lock()

    # ---- Настройки ----------------------------------------------------

    def _get_settings(self):
        from calibre_plugins.ozon_metadata.config import get_settings, DEFAULTS
        try:
            return get_settings()
        except Exception:
            return dict(DEFAULTS)

    # ---- Поиск site-packages в venv -----------------------------------

    def _find_venv_site_packages(self):
        for venv_path in self.VENV_PATHS:
            if not os.path.isdir(venv_path):
                continue
            patterns = [
                os.path.join(venv_path, 'lib', 'python*', 'site-packages'),
                os.path.join(venv_path, 'lib64', 'python*', 'site-packages'),
            ]
            for pattern in patterns:
                matches = glob.glob(pattern)
                if matches:
                    return matches[0]
        return None

    # ---- Инициализация curl_cffi --------------------------------------

    def _init_curl_cffi(self, log):
        """Инициализирует curl_cffi сессию.

        Не тестируем ozon.ru при старте — просто создаём сессию.
        Тестирование и переключение профилей происходит при ошибках 403.
        """
        if self._curl_cffi_available is not None:
            return self._curl_cffi_available

        import sys

        cffi_requests = None

        try:
            from curl_cffi import requests as cffi_requests
        except ImportError:
            venv_site = self._find_venv_site_packages()
            if venv_site:
                log(f'Ozon: adding venv site-packages: {venv_site}')
                if venv_site not in sys.path:
                    sys.path.insert(0, venv_site)
                try:
                    from curl_cffi import requests as cffi_requests
                except ImportError:
                    cffi_requests = None
            else:
                cffi_requests = None

        if cffi_requests is None:
            self._curl_cffi_available = False
            log('Ozon: curl_cffi not available')
            return False

        # Создаём сессию с первым профилем
        for profile in self.IMPERSONATION_PROFILES:
            try:
                session = cffi_requests.Session(impersonate=profile)
                self._curl_cffi_session = session
                self._curl_cffi_available = True
                self._curl_cffi_profile_index = (
                    self.IMPERSONATION_PROFILES.index(profile)
                )
                log(f'Ozon: curl_cffi ready (impersonate={profile})')
                return True
            except Exception as e:
                log(f'Ozon: curl_cffi {profile} init failed: {e}')

        self._curl_cffi_available = False
        log('Ozon: curl_cffi not available after all profiles')
        return False

    # ---- Cloudflare clearance -----------------------------------------

    def _get_cf_clearance(self, log):
        """Получает cf_clearance cookie через curl_cffi.

        Возвращает cookie string или None.
        Кэширует неудачу в _cf_clearance_failed.
        Сбрасывает кэш при успешном запросе.
        """
        if self._cf_clearance:
            return self._cf_clearance
        
        # Сбрасываем кэш неудачи после успешного запроса
        if self._cf_clearance_failed and self._cf_clearance_attempt_count >= self._cf_clearance_max_attempts:
            log('Ozon: cf_clearance max attempts reached, resetting')
            self._cf_clearance_failed = False
            self._cf_clearance_attempt_count = 0

        if self._cf_clearance_failed:
            return None

        if not self._init_curl_cffi(log):
            self._cf_clearance_failed = True
            return None

        settings = self._get_settings()
        proxy = settings.get('proxy', '')

        for profile in self.IMPERSONATION_PROFILES:
            try:
                from curl_cffi import requests as cffi_requests
                session = cffi_requests.Session(impersonate=profile)

                if proxy:
                    session.proxies = {'http': proxy, 'https': proxy}

                resp = session.get(
                    'https://www.ozon.ru/',
                    timeout=15,
                    allow_redirects=True,
                    headers={
                        'Accept': 'text/html,application/xhtml+xml,'
                                  'application/xml;q=0.9,*/*;q=0.8',
                        'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
                    },
                )
                log(f'Ozon: cf_clearance check {profile} -> '
                    f'{resp.status_code}')

                # Проверяем cookies
                cookies = session.cookies
                cf = cookies.get('cf_clearance')
                if cf:
                    self._cf_clearance = f'cf_clearance={cf}'
                    self._cf_clearance_failed = False
                    log('Ozon: cf_clearance obtained via cookies')
                    return self._cf_clearance

                # Пробуем получить из set-cookie headers
                set_cookie = resp.headers.get('set-cookie')
                if set_cookie and 'cf_clearance' in set_cookie:
                    import re
                    m = re.search(
                        r'cf_clearance=([^;]+)', set_cookie)
                    if m:
                        self._cf_clearance = f'cf_clearance={m.group(1)}'
                        self._cf_clearance_failed = False
                        log('Ozon: cf_clearance from header')
                        return self._cf_clearance

            except Exception as e:
                log(f'Ozon: cf_clearance {profile} failed: {e}')

        self._cf_clearance_failed = True
        log('Ozon: cf_clearance not obtained (cached)')
        return None

    # ---- HTTP запросы -------------------------------------------------

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
                log(f'Ozon: задержка {wait:.1f} сек')
                time.sleep(wait)
            self._last_request_time = time.time()

    def _get_mechanize(self, log, url, timeout, headers=None, max_retries=2):
        """Запрос через встроенный mechanize с заданными заголовками.
        
        Поддерживает retry logic для временных ошибок.
        Автоматически распаковывает gzip-сжатие.
        """
        import gzip
        import io
        
        for attempt in range(max_retries + 1):
            try:
                br = self.browser
                br.addheaders = list(headers.items()) if headers else []
                resp = br.open_novisit(url, timeout=timeout)
                
                raw_data = resp.read()
                
                # Автоматически распаковываем gzip если нужно
                if raw_data and len(raw_data) > 2:
                    # Проверяем magic number для gzip (0x1f8b)
                    if raw_data[:2] == b'\x1f\x8b':
                        try:
                            with gzip.GzipFile(fileobj=io.BytesIO(raw_data)) as gz:
                                raw_data = gz.read()
                            log('Ozon: decompressed gzip response')
                        except Exception as e:
                            log(f'Ozon: gzip decompression failed: {e}, using raw')
                    
                    # Проверяем Content-Encoding header
                    content_encoding = resp.headers.get('content-encoding', '')
                    if 'gzip' in content_encoding.lower() and raw_data[:2] != b'\x1f\x8b':
                        try:
                            with gzip.GzipFile(fileobj=io.BytesIO(raw_data)) as gz:
                                raw_data = gz.read()
                            log('Ozon: decompressed gzip via header')
                        except Exception as e:
                            log(f'Ozon: gzip decompression via header failed: {e}')
                
                return raw_data
            except Exception as e:
                err_str = str(e).lower()
                # Не повторяемся при критических ошибках
                if 'certificate' in err_str or 'ssl' in err_str:
                    log(f'Ozon: mechanize SSL error: {e}')
                    return None
                if attempt < max_retries:
                    wait = (attempt + 1) * 2
                    log(f'Ozon: mechanize error (retry {attempt+1}/{max_retries}): {e}, waiting {wait}s')
                    time.sleep(wait)
                else:
                    log(f'Ozon: mechanize error after {max_retries} retries: {e}')
                    return None
        return None

    def _get_curl_cffi(self, log, url, timeout, headers=None, max_retries=3):
        """Запрос через curl_cffi (TLS impersonation).

        НЕ задаём User-Agent вручную — curl_cffi сам подставит
        правильный под выбранный профиль chrome.
        При 403: 1) пробуем cf_clearance (один раз), 2) переключаем профиль.
        При 429: retry с exponential backoff.
        """
        cf_tried = False
        retry_count = 0
        rate_limited = False

        while retry_count <= max_retries:
            try:
                # Добавляем cf_clearance cookie если есть
                req_headers = dict(headers) if headers else {}
                if self._cf_clearance:
                    req_headers['Cookie'] = self._cf_clearance

                resp = self._curl_cffi_session.get(
                    url, timeout=timeout, allow_redirects=True,
                    headers=req_headers,
                )

                if resp.status_code == 200:
                    retry_count = 0  # Сбрасываем счётчик при успехе
                    rate_limited = False
                    # Сбрасываем счётчик cf_clearance при успехе
                    if self._cf_clearance_attempt_count > 0:
                        log('Ozon: request successful, resetting cf_clearance attempts')
                        self._cf_clearance_attempt_count = 0
                        self._cf_clearance_failed = False
                    return resp.content

                # При 429: retry с exponential backoff
                if resp.status_code == 429:
                    if not rate_limited:
                        rate_limited = True
                        wait = min((retry_count + 1) * 5, 30)
                        log(f'Ozon: 429 Too Many Requests, retrying in {wait}s')
                        time.sleep(wait)
                        retry_count += 1
                        continue
                    else:
                        log('Ozon: 429 after retries, giving up')
                        return None

                # При 403: сначала cf_clearance (один раз), потом профиль
                if resp.status_code == 403:
                    if not cf_tried and not self._cf_clearance_failed:
                        log('Ozon: 403, trying cf_clearance')
                        self._cf_clearance_attempt_count += 1
                        self._get_cf_clearance(log)
                        cf_tried = True
                        retry_count = 0  # Сбрасываем счётчик после cf_clearance
                        continue  # Повторяем запрос с cookie

                    log('Ozon: 403, switching profile')
                    self._try_next_profile(log)
                    if not self._curl_cffi_session:
                        log('Ozon: no more profiles available')
                        return None
                    retry_count = 0  # Сбрасываем счётчик при переключении профиля
                    continue  # Повторяем с новым профилем

                # Другие ошибки: retry с exponential backoff
                if retry_count < max_retries:
                    wait = min((retry_count + 1) * 2, 10)
                    log(f'Ozon: curl_cffi {resp.status_code} (retry {retry_count+1}/{max_retries}), waiting {wait}s')
                    time.sleep(wait)
                    retry_count += 1
                    continue

                log(f'Ozon: curl_cffi {resp.status_code} for {url[:100]}')
                return None

            except Exception as e:
                err_str = str(e).lower()
                # Не повторяемся при критических ошибках
                if 'ssl' in err_str or 'certificate' in err_str or 'connection refused' in err_str:
                    log(f'Ozon: curl_cffi critical error: {e}')
                    return None
                if retry_count < max_retries:
                    wait = min((retry_count + 1) * 2, 10)
                    log(f'Ozon: curl_cffi error (retry {retry_count+1}/{max_retries}): {e}, waiting {wait}s')
                    time.sleep(wait)
                    retry_count += 1
                else:
                    log(f'Ozon: curl_cffi error after {max_retries} retries: {e}')
                    return None

        log('Ozon: curl_cffi max retries exceeded')
        return None

    def _try_next_profile(self, log):
        """Переключает на следующий профиль impersonation."""
        if not self._curl_cffi_session:
            return

        current_idx = self._curl_cffi_profile_index
        start_idx = (current_idx + 1) % len(self.IMPERSONATION_PROFILES)

        for i in range(len(self.IMPERSONATION_PROFILES)):
            next_idx = (start_idx + i) % len(self.IMPERSONATION_PROFILES)
            profile = self.IMPERSONATION_PROFILES[next_idx]

            if next_idx == current_idx:
                continue

            try:
                from curl_cffi import requests as cffi_requests
                new_session = cffi_requests.Session(impersonate=profile)
                self._curl_cffi_session = new_session
                self._curl_cffi_profile_index = next_idx
                log(f'Ozon: switched to profile={profile}')
                return
            except Exception as e:
                log(f'Ozon: failed to switch to {profile}: {e}')
                continue

        self._curl_cffi_available = False
        log('Ozon: all impersonation profiles exhausted')

    def _try_next_mobile_headers(self, log):
        """Переключает на следующую версию мобильных заголовков."""
        current_idx = self._mobile_headers_index
        next_idx = (current_idx + 1) % len(self.MOBILE_HEADERS_VERSIONS)
        
        if next_idx == current_idx:
            log('Ozon: all mobile headers versions exhausted')
            return False
        
        self._mobile_headers_index = next_idx
        log(f'Ozon: switched to mobile headers version {next_idx + 1}')
        return True

    # ---- API запросы --------------------------------------------------

    def _fetch_api_json(self, log, path, timeout=30):
        """Пробует получить JSON через мобильный API, затем через веб API.

        path — путь типа /search/?text=запрос&from_global=true
        
        Поддерживает fallback на разные версии мобильных заголовков.
        """
        encoded_path = urllib.parse.quote(path, safe='')
        settings = self._get_settings()
        proxy = settings.get('proxy', '')

        # 1. Мобильный API (api.ozon.ru) — не за Cloudflare
        mobile_url = f'{self.MOBILE_API}?url={encoded_path}'
        log(f'Ozon: trying mobile API: {mobile_url[:120]}')

        # Пробуем все версии мобильных заголовков
        for version_idx in range(len(self.MOBILE_HEADERS_VERSIONS)):
            # mechanize с мобильными заголовками
            mobile_headers = dict(self.MOBILE_HEADERS_BASE)
            mobile_headers.update(self.MOBILE_HEADERS_VERSIONS[version_idx])
            mobile_headers['Host'] = 'api.ozon.ru'
            mobile_headers['x-ozon-request-id'] = self._generate_request_id()
            
            self._apply_delay(log)
            raw = self._get_mechanize(log, mobile_url, timeout, mobile_headers)
            if raw:
                data = self._try_parse_json(log, raw)
                if data:
                    log(f'Ozon: mobile API (mechanize) success with version {version_idx + 1}')
                    return data
            
            # curl_cffi с мобильными заголовками
            if self._init_curl_cffi(log):
                self._apply_delay(log)
                mobile_headers_cffi = dict(self.MOBILE_HEADERS_BASE)
                mobile_headers_cffi.update(self.MOBILE_HEADERS_VERSIONS[version_idx])
                mobile_headers_cffi['Host'] = 'api.ozon.ru'
                mobile_headers_cffi['x-ozon-request-id'] = self._generate_request_id()
                raw = self._get_curl_cffi(log, mobile_url, timeout, mobile_headers_cffi)
                if raw:
                    data = self._try_parse_json(log, raw)
                    if data:
                        log(f'Ozon: mobile API via curl_cffi success with version {version_idx + 1}')
                        return data
            
            if version_idx < len(self.MOBILE_HEADERS_VERSIONS) - 1:
                log(f'Ozon: mobile API version {version_idx + 1} failed, trying next...')

        log('Ozon: all mobile API versions failed')

        # 2. Веб API (www.ozon.ru) — за Cloudflare, нужен curl_cffi
        web_url = f'{self.WEB_API}?url={encoded_path}'
        log(f'Ozon: trying web API: {web_url[:120]}')

        if self._init_curl_cffi(log):
            api_headers = dict(self.WEB_API_HEADERS)
            api_headers['x-ozon-request-id'] = self._generate_request_id()
            if proxy:
                self._curl_cffi_session.proxies = {
                    'http': proxy, 'https': proxy,
                }
            self._apply_delay(log)
            raw = self._get_curl_cffi(log, web_url, timeout, api_headers)
            if raw:
                data = self._try_parse_json(log, raw)
                if data:
                    log('Ozon: web API success')
                    return data
                log('Ozon: web API returned non-JSON')

        # 3. Прямой HTML запрос к Ozon (через curl_cffi)
        html_url = f'{self.OZON_BASE_URL}{path}'
        log(f'Ozon: trying HTML direct: {html_url[:120]}')
        if self._init_curl_cffi(log):
            self._apply_delay(log)
            raw = self._get_curl_cffi(log, html_url, timeout, {
                **dict(self.WEB_HEADERS),
                'x-ozon-request-id': self._generate_request_id(),
            })
            if raw:
                html = raw.decode('utf-8', errors='replace')
                data = self._extract_json_from_html(log, html)
                if data:
                    log('Ozon: HTML direct success')
                    return data

        # 4. HTML fallback через API endpoint
        api_html_url = f'{self.OZON_BASE_URL}/search/?text={urllib.parse.quote(path.split("text=")[1].split("&")[0] if "text=" in path else "book")}&from_global=true'
        log(f'Ozon: trying API HTML fallback: {api_html_url[:120]}')
        if self._init_curl_cffi(log):
            self._apply_delay(log)
            raw = self._get_curl_cffi(log, api_html_url, timeout, {
                **dict(self.WEB_HEADERS),
                'x-ozon-request-id': self._generate_request_id(),
            })
            if raw:
                html = raw.decode('utf-8', errors='replace')
                data = self._extract_json_from_html(log, html)
                if data:
                    log('Ozon: API HTML fallback success')
                    return data

        # 5. Cloudscraper fallback (если установлен)
        log('Ozon: trying cloudscraper fallback')
        raw = self._get_cloudscraper(log, api_html_url, timeout)
        if raw:
            html = raw.decode('utf-8', errors='replace')
            data = self._extract_json_from_html(log, html)
            if data:
                log('Ozon: cloudscraper success')
                return data

        return None

    def _generate_request_id(self):
        """Генерирует уникальный ID запроса."""
        import uuid
        return str(uuid.uuid4())

    def _get_cloudscraper(self, log, url, timeout=30):
        """Запрос через cloudscraper (обход Cloudflare)."""
        try:
            import cloudscraper
            scraper = cloudscraper.create_scraper(
                browser={'browser': 'chrome', 'platform': 'windows',
                         'mobile': False},
                debug=False,
            )
            settings = self._get_settings()
            proxy = settings.get('proxy', '')
            if proxy:
                scraper.proxies = {'http': proxy, 'https': proxy}

            resp = scraper.get(
                url, timeout=timeout,
                headers=dict(self.CLOUDSCRAPER_HEADERS),
            )
            if resp.status_code == 200:
                return resp.content
            log(f'Ozon: cloudscraper {resp.status_code} for {url[:100]}')
            return None
        except ImportError:
            log('Ozon: cloudscraper not installed')
            return None
        except Exception as e:
            log(f'Ozon: cloudscraper error: {e}')
            return None

    def _try_parse_json(self, log, raw):
        try:
            return json.loads(raw.decode('utf-8', errors='replace'))
        except (json.JSONDecodeError, ValueError):
            return None

    # ---- Поиск --------------------------------------------------------

    def _search_by_isbn(self, log, isbn, timeout=30):
        clean = re.sub(r'[^0-9Xx]', '', isbn)
        path = f'/search/?text={clean}&from_global=true'
        data = self._fetch_api_json(log, path, timeout)
        return self._parse_search_results(log, data) if data else []

    def _search_by_title_author(self, log, title, authors, timeout=30):
        settings = self._get_settings()
        query = ' '.join(filter(None, [title] + (authors or [])))

        if settings.get('only_books', True):
            path = f'/category/knigi-15500/?text={query}&from_global=true'
        else:
            path = f'/search/?text={query}&from_global=true'

        log(f'Ozon: search path: {path[:100]}')
        data = self._fetch_api_json(log, path, timeout)
        return self._parse_search_results(log, data) if data else []

    # ---- Парсинг результатов поиска -----------------------------------

    def _parse_search_results(self, log, data):
        results = []
        items = self._find_items_recursive(data)
        if not items:
            log('Ozon: no items found in response')
            return []

        settings = self._get_settings()
        max_results = settings.get('max_results', 5)
        only_books = settings.get('only_books', True)

        filtered = []
        for item in items:
            if only_books and not self._is_book_category(item):
                continue
            filtered.append(item)
            if len(filtered) >= max_results:
                break

        log(f'Ozon: {len(items)} items found, '
            f'{len(filtered)} after filtering')

        for item in filtered:
            info = self._extract_book_info(item)
            if info:
                results.append(info)
        return results

    def _find_items_recursive(self, obj, depth=0, max_depth=5):
        if depth > max_depth:
            return []
        if isinstance(obj, dict):
            if 'items' in obj and isinstance(obj['items'], list):
                if obj['items'] and isinstance(obj['items'][0], dict):
                    first = obj['items'][0]
                    if any(k in first for k in
                           ('sku', 'productId', 'id',
                            'title', 'name')):
                        return obj['items']
            for v in obj.values():
                r = self._find_items_recursive(v, depth + 1, max_depth)
                if r:
                    return r
        elif isinstance(obj, list):
            for item in obj:
                r = self._find_items_recursive(
                    item, depth + 1, max_depth)
                if r:
                    return r
        return []

    def _extract_book_info(self, item):
        title = (item.get('title') or
                 item.get('name') or
                 item.get('shortName'))
        if not title:
            return None
        sku = (item.get('sku') or
               item.get('productId') or
               item.get('id'))
        if not sku:
            return None
        url = item.get('url') or item.get('link')
        if url and not url.startswith('http'):
            url = self.OZON_BASE_URL + url
        cover_url = None
        images = item.get('images') or []
        if images and isinstance(images, list):
            cover_url = images[0]
        elif item.get('image'):
            cover_url = item.get('image')
        elif item.get('mainImage'):
            cover_url = item.get('mainImage')
        return {
            'title': title, 'sku': str(sku),
            'url': url, 'cover_url': cover_url,
        }

    # ---- Фильтрация по категории -------------------------------------

    def _is_book_category(self, item):
        settings = self._get_settings()
        if not settings.get('only_books', True):
            return True

        url = (item.get('url') or
               item.get('link') or '').lower()
        for cat in self.OZON_BOOK_CATEGORIES:
            if cat in url:
                return True

        cat_path = (item.get('categoryPath') or
                    item.get('category') or '').lower()
        for kw in self.OZON_CATEGORY_KEYWORDS:
            if kw in cat_path:
                return True

        breadcrumbs = (item.get('breadcrumbs') or
                       item.get('categoryBreadcrumbs'))
        if breadcrumbs and isinstance(breadcrumbs, list):
            for bc in breadcrumbs:
                bc_text = ''
                if isinstance(bc, dict):
                    bc_text = (bc.get('text', '') or
                              bc.get('name', '') or
                              bc.get('title', ''))
                elif isinstance(bc, str):
                    bc_text = bc
                bc_lower = bc_text.lower()
                for kw in self.OZON_CATEGORY_KEYWORDS:
                    if kw in bc_lower:
                        return True
        return False

    # ---- Страница товара ---------------------------------------------

    def _fetch_product_page(self, log, sku, timeout=30):
        path = f'/product/{sku}/'
        return self._fetch_api_json(log, path, timeout)

    def _parse_book_details(self, log, data, search_result):
        try:
            characteristics = {}
            self._find_characteristics(data, characteristics)
            description = self._find_description(data)
            rating = self._find_rating(data)

            cover_url = search_result.get('cover_url')
            found_cover = self._find_cover(data)
            if found_cover:
                cover_url = found_cover

            title = self._clean_title(search_result['title'])
            sku = search_result['sku']

            isbn = (characteristics.get('ISBN') or
                    characteristics.get('isbn'))
            if isbn:
                isbn = re.sub(r'[^0-9Xx]', '', isbn)

            authors = []
            author_str = (
                characteristics.get('Автор')
                or characteristics.get('author')
                or characteristics.get('Писатель')
                or characteristics.get('Автор на обложке')
            )
            if author_str:
                authors = self._parse_authors(author_str)
            else:
                authors = self._extract_authors_from_title(
                    search_result['title'])

            publisher = (
                characteristics.get('Издательство')
                or characteristics.get('publisher')
                or characteristics.get('Производитель')
            )

            pubdate = None
            year_str = (
                characteristics.get('Год издания')
                or characteristics.get('year')
                or characteristics.get('Год выпуска')
            )
            if year_str:
                try:
                    year = int(
                        re.search(r'(\d{4})', str(year_str)).group(1))
                    pubdate = parse_only_date(f'{year}-01-01')
                except (ValueError, AttributeError):
                    pass

            pages = None
            pages_str = (
                characteristics.get('Количество страниц')
                or characteristics.get('pages')
            )
            if pages_str:
                try:
                    pages = int(
                        re.search(r'(\d+)', str(pages_str)).group(1))
                except (ValueError, AttributeError):
                    pass

            tags = []
            genre = (
                characteristics.get('Жанр')
                or characteristics.get('genre')
                or characteristics.get('Категория')
            )
            if genre:
                tags = [g.strip()
                        for g in re.split(r'[,;]', genre) if g.strip()]

            mi = Metadata(title, authors or ['Unknown'])
            mi.set_identifier('ozon', sku)
            if isbn:
                mi.set_identifier('isbn', isbn)
            if description:
                mi.comments = description
                mi.has_html_comments = True
            if publisher:
                mi.publisher = publisher
            if pubdate:
                mi.pubdate = pubdate
            if rating and isinstance(rating, (int, float)) \
                    and 0 < rating <= 5:
                mi.rating = int(rating * 2)
            if tags:
                mi.tags = tags

            extra = []
            if pages:
                extra.append(f'Страниц: {pages}')
            if characteristics.get('Серия'):
                extra.append(
                    f"Серия: {characteristics['Серия']}")
            binding = (
                characteristics.get('Переплёт')
                or characteristics.get('Тип обложки')
            )
            if binding:
                extra.append(f'Переплёт: {binding}')
            if characteristics.get('Язык'):
                extra.append(
                    f"Язык: {characteristics['Язык']}")

            if extra:
                details_html = '<br/>'.join(
                    f'<b>{d}</b>' for d in extra)
                if mi.comments:
                    mi.comments += (
                        '<hr/><p><b>Детали издания (Ozon):</b>'
                        f'<br/>{details_html}</p>'
                    )
                else:
                    mi.comments = (
                        f'<p><b>Детали издания (Ozon):</b>'
                        f'<br/>{details_html}</p>'
                    )

            if cover_url:
                self.cache_cover_url(sku, cover_url)
            mi.url = search_result.get(
                'url',
                f'{self.OZON_BASE_URL}/product/{sku}/')
            mi.source_relevance = 0
            return mi
        except Exception as e:
            log(f'Ozon: error parsing book details: {e}')
            return None

    # ---- Рекурсивные поиски в JSON ------------------------------------

    def _find_characteristics(self, obj, result, depth=0, max_depth=6):
        if depth > max_depth:
            return
        if isinstance(obj, dict):
            for key in ('characteristics', 'attributes',
                        'specs', 'properties'):
                if key in obj and isinstance(obj[key], list):
                    for char in obj[key]:
                        if isinstance(char, dict):
                            name = (char.get('name') or
                                   char.get('key') or
                                   char.get('title'))
                            values = (char.get('values') or
                                     char.get('value') or
                                     char.get('items'))
                            if name and values:
                                if isinstance(values, list):
                                    val = ', '.join(
                                        v.get('text', str(v))
                                        if isinstance(v, dict)
                                        else str(v)
                                        for v in values
                                    )
                                else:
                                    val = str(values)
                                result[name] = val
            for v in obj.values():
                self._find_characteristics(
                    v, result, depth + 1, max_depth)
        elif isinstance(obj, list):
            for item in obj:
                self._find_characteristics(
                    item, result, depth + 1, max_depth)

    def _find_description(self, obj, depth=0, max_depth=6):
        if depth > max_depth:
            return None
        if isinstance(obj, dict):
            for key in ('description', 'comment', 'about',
                        'text', 'content'):
                if key in obj and isinstance(obj[key], str) \
                        and len(obj[key]) > 50:
                    return obj[key]
            for v in obj.values():
                r = self._find_description(v, depth + 1, max_depth)
                if r:
                    return r
        elif isinstance(obj, list):
            for item in obj:
                r = self._find_description(
                    item, depth + 1, max_depth)
                if r:
                    return r
        return None

    def _find_rating(self, obj, depth=0, max_depth=6):
        if depth > max_depth:
            return None
        if isinstance(obj, dict):
            for key in ('rating', 'mark', 'score',
                        'averageRating'):
                if key in obj:
                    val = obj[key]
                    if isinstance(val, (int, float)) \
                            and 0 < val <= 5:
                        return float(val)
                    if isinstance(val, str):
                        try:
                            fval = float(val.replace(',', '.'))
                            if 0 < fval <= 5:
                                return fval
                        except ValueError:
                            pass
            for v in obj.values():
                r = self._find_rating(v, depth + 1, max_depth)
                if r is not None:
                    return r
        elif isinstance(obj, list):
            for item in obj:
                r = self._find_rating(item, depth + 1, max_depth)
                if r is not None:
                    return r
        return None

    def _find_cover(self, obj, depth=0, max_depth=6):
        if depth > max_depth:
            return None
        if isinstance(obj, dict):
            for key in ('image', 'cover', 'mainImage',
                        'imageUrl', 'coverUrl'):
                if key in obj and isinstance(obj[key], str):
                    url = obj[key]
                    if ('ozon' in url or 'ozone' in url or
                            url.startswith('http')) and \
                            any(url.lower().endswith(ext)
                                for ext in
                                ('.jpg', '.jpeg', '.png', '.webp')):
                        return url
            for v in obj.values():
                r = self._find_cover(v, depth + 1, max_depth)
                if r:
                    return r
        elif isinstance(obj, list):
            for item in obj:
                r = self._find_cover(item, depth + 1, max_depth)
                if r:
                    return r
        return None

    # ---- Вспомогательные методы --------------------------------------

    def _clean_title(self, title):
        title = re.sub(r'\s*\|.*$', '', title)
        title = re.sub(r'\s*—.*$', '', title)
        title = re.sub(
            r'\s*купить на OZON.*$', '',
            title, flags=re.IGNORECASE)
        return title.strip()

    def _parse_authors(self, author_str):
        if not author_str:
            return ['Unknown']
        authors = re.split(r'[,;]', author_str)
        return [a.strip() for a in authors if a.strip()] or ['Unknown']

    def _extract_authors_from_title(self, title):
        match = re.search(
            r'\|\s*([\w\s.]+?)(?:\s*\||\s*$)', title)
        return self._parse_authors(
            match.group(1)) if match else []

    def _extract_json_from_html(self, log, html):
        merged = {}
        for pattern in [
            r'window\.__OZON_DATA\s*=\s*({.*?});\s*</script>',
            r'window\.__INITIAL_STATE__\s*=\s*({.*?});\s*</script>',
            r'window\.__NUXT__\s*=\s*({.*?});\s*</script>',
        ]:
            for m in re.finditer(pattern, html, re.DOTALL):
                try:
                    data = json.loads(m.group(1))
                    if isinstance(data, dict):
                        merged.update(data)
                except (json.JSONDecodeError, ValueError):
                    pass
        for m in re.finditer(
            r'<script[^>]*type=["\']application/json["\'][^>]*>'
            r'(.*?)</script>',
            html, re.DOTALL
        ):
            try:
                data = json.loads(m.group(1))
                if isinstance(data, dict):
                    merged.update(data)
            except (json.JSONDecodeError, ValueError):
                pass
        if not merged:
            log('Ozon: JSON not found in HTML')
            return None
        return merged

    # ---- Интерфейс Source ---------------------------------------------

    def identify(self, log, result_queue, abort, title=None,
                 authors=None, identifiers=None, timeout=30):
        if identifiers is None:
            identifiers = {}

        settings = self._get_settings()
        effective_timeout = settings.get('timeout', 30)
        max_results = settings.get('max_results', 5)

        log('Ozon: starting identify')

        isbn = identifiers.get('isbn')
        ozon_id = identifiers.get('ozon')

        if ozon_id:
            log(f'Ozon: searching by ozon ID: {ozon_id}')
            data = self._fetch_product_page(
                log, ozon_id, effective_timeout)
            if data:
                mi = self._parse_book_details(log, data, {
                    'title': '', 'sku': ozon_id,
                    'url': f'{self.OZON_BASE_URL}'
                           f'/product/{ozon_id}/',
                    'cover_url': None,
                })
                if mi:
                    mi.source_relevance = 0
                    self.clean_downloaded_metadata(mi)
                    result_queue.put(mi)
                    return None

        search_results = []

        if isbn:
            log(f'Ozon: searching by ISBN: {isbn}')
            search_results = self._search_by_isbn(
                log, isbn, effective_timeout)
            if not search_results:
                log('Ozon: no results by ISBN')

        if not search_results and (title or authors):
            log(f'Ozon: searching by title/author: '
                f'{title} / {authors}')
            search_results = self._search_by_title_author(
                log, title, authors, effective_timeout)

        if not search_results:
            log('Ozon: no search results')
            return None

        threads = []

        def parse_and_queue(sr, idx):
            if abort.is_set():
                return
            data = self._fetch_product_page(
                log, sr['sku'], effective_timeout)
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

        ozon_id = identifiers.get('ozon')
        if not ozon_id:
            isbn = identifiers.get('isbn')
            if isbn:
                results = self._search_by_isbn(
                    log, isbn, effective_timeout)
                if results:
                    ozon_id = results[0]['sku']
                    if results[0].get('cover_url'):
                        self.cache_cover_url(
                            ozon_id, results[0]['cover_url'])

        if not ozon_id:
            return None

        cover_url = self.get_cached_cover_url({'ozon': ozon_id})
        if not cover_url:
            data = self._fetch_product_page(
                log, ozon_id, effective_timeout)
            if data:
                found = self._find_cover(data)
                if found:
                    cover_url = found
                    self.cache_cover_url(ozon_id, cover_url)

        if not cover_url:
            log('Ozon: no cover URL found')
            return None

        log(f'Ozon: downloading cover from {cover_url}')
        # Обложки на CDN — скачиваем через curl_cffi или mechanize
        if self._init_curl_cffi(log):
            self._apply_delay(log)
            cover_data = self._get_curl_cffi(
                log, cover_url, effective_timeout)
        else:
            self._apply_delay(log)
            cover_data = self._get_mechanize(
                log, cover_url, effective_timeout)
        if cover_data and len(cover_data) > 100:
            result_queue.put((self, cover_data))

        return None

    def get_book_url(self, identifiers):
        ozon_id = identifiers.get('ozon')
        if ozon_id:
            return ('ozon', ozon_id,
                    f'{self.OZON_BASE_URL}/product/{ozon_id}/')
        return None

    def get_book_url_name(self, idtype, idval, url):
        return 'Ozon.ru'

    def id_from_url(self, url):
        m = re.search(
            r'ozon\.ru/product/(?:[^/]*)?/?(\d+)', url)
        return ('ozon', m.group(1)) if m else None

    # ---- Конфигурация через GUI ---------------------------------------

    def is_customizable(self):
        return True

    def config_widget(self):
        from calibre_plugins.ozon_metadata.config import ConfigDialog
        return ConfigDialog()

    def save_settings(self, config_widget):
        config_widget.accept()
