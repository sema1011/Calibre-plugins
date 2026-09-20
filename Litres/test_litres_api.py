#!/usr/bin/env python3
"""Тест API ЛитРеса - запустите: python3 test_litres_api.py"""
import urllib.request
import urllib.parse
import json

def test_api(params, label, extra_headers=None):
    url = "https://api.litres.ru/foundation/api/search?" + urllib.parse.urlencode(params)
    print(f"\n=== {label} ===")
    print(f"URL: {url}")
    req = urllib.request.Request(url)
    req.add_header('Accept', 'application/json')
    req.add_header('User-Agent', 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36')
    if extra_headers:
        for k, v in extra_headers.items():
            req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        raw = resp.read().decode('utf-8')
        data = json.loads(raw)
        print(f"Status: {resp.status}")
        print(f"Response keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
        print(json.dumps(data, indent=2, ensure_ascii=False)[:2000])
    except Exception as e:
        print(f"Error: {e}")
        if hasattr(e, 'read'):
            print(f"Body: {e.read().decode('utf-8')[:1000]}")

# Тест 1: минимальные параметры
test_api({'q': 'Фленов', 'limit': 5}, "Тест 1: q + limit")

# Тест 2: с offset
test_api({'q': 'Фленов', 'limit': 5, 'offset': 0}, "Тест 2: q + limit + offset")

# Тест 3: с types
test_api({'q': 'Фленов', 'limit': 5, 'types': 'text_book,audiobook'}, "Тест 3: + types=text_book,audiobook")

# Тест 5: с заголовком app-id
test_api({'q': 'Фленов', 'limit': 5}, "Тест 5: + app-id заголовок",
         extra_headers={'app-id': '1', 'ui-language-code': 'ru'})
