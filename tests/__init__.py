"""Тесты: только stdlib (unittest). Запуск из .digest: python3 -m unittest.

Сети в тестах нет: `net.send` и `urllib.request.urlopen` подменены заглушкой, которая
падает на любом вызове (вторая — на случай кода в обход `net`); тесты сетевых модулей
ставят поверх `net.send` `fakes.FakeNet` с очередью ответов.
"""
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from study import net


def offline(url, *args, **kwargs):
    raise AssertionError(f"сеть в тестах запрещена: {getattr(url, 'full_url', url)}")


net.send = urllib.request.urlopen = offline
