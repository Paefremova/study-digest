"""Тесты: только stdlib (unittest). Запуск из .digest: python3 -m unittest.

Сети в тестах нет: `urllib.request.urlopen` подменён заглушкой, которая падает на любом
вызове — через `net.send` или в обход него; тесты сетевых модулей ставят поверх `net.send`
`fakes.FakeNet` с очередью ответов.
"""
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))


def offline(url, *args, **kwargs):
    raise AssertionError(f"сеть в тестах запрещена: {getattr(url, 'full_url', url)}")


urllib.request.urlopen = offline
