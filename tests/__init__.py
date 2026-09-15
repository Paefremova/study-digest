"""Тесты: только stdlib (unittest). Запуск из .digest: python3 -m unittest discover -s tests."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
