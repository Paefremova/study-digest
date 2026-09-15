"""Тесты: только stdlib (unittest). Запуск из .digest: python3 -m unittest.

Сети в тестах нет: `net.send` подменён заглушкой, которая падает на любом вызове;
тесты сетевых модулей ставят поверх неё `fakes.FakeNet` с очередью ответов.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from study import net


def offline(url, *args, **kwargs):
    raise AssertionError(f"сеть в тестах запрещена: {url}")


net.send = offline
