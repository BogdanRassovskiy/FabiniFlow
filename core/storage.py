"""
Jednoduché JSON úložiště.

Každá kolekce (orders, payments, ...) je jeden soubor `<DATA_DIR>/<kolekce>.json`
obsahující seznam záznamů. Pro účely case study to stačí; rozhraní je ale
schválně úzké (get/list/insert/update/upsert), aby šlo později vyměnit za DB.

Zápis je atomický (temp soubor + os.replace) a chráněný zámkem, aby souběžné
požadavky (UI + běžící agent ve vlákně) nepřepsaly data jeden druhému.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator

from django.conf import settings

Record = dict[str, Any]

_lock = threading.RLock()


class NotFound(LookupError):
    pass


def _path(collection: str) -> Path:
    if not collection.isidentifier():
        raise ValueError(f"Neplatný název kolekce: {collection!r}")
    return Path(settings.DATA_DIR) / f"{collection}.json"


def _read(collection: str) -> list[Record]:
    path = _path(collection)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write(collection: str, records: list[Record]) -> None:
    path = _path(collection)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{collection}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def atomic(fn: Callable) -> Callable:
    """Dekorátor: celá operace čti–uprav–zapiš proběhne pod zámkem úložiště.

    Bez toho by např. zpráva zákazníka a souběžný zápis agenta (oba načtou
    konverzaci, každý ji upraví a uloží) jeden druhému přepsaly změny.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with _lock:
            return fn(*args, **kwargs)
    return wrapper


@contextmanager
def transaction(collection: str) -> Iterator[list[Record]]:
    """Načte kolekci, dovolí ji upravit na místě a na konci ji uloží."""
    with _lock:
        records = _read(collection)
        yield records
        _write(collection, records)


def list_all(collection: str, where: Callable[[Record], bool] | None = None) -> list[Record]:
    with _lock:
        records = _read(collection)
    if where:
        records = [r for r in records if where(r)]
    return copy.deepcopy(records)


def get(collection: str, key: str, value: Any) -> Record:
    for record in list_all(collection):
        if record.get(key) == value:
            return record
    raise NotFound(f"{collection}: {key}={value!r} nenalezeno")


def find(collection: str, key: str, value: Any) -> Record | None:
    try:
        return get(collection, key, value)
    except NotFound:
        return None


def insert(collection: str, record: Record) -> Record:
    with transaction(collection) as records:
        records.append(copy.deepcopy(record))
    return record


def update(collection: str, key: str, value: Any, changes: Record) -> Record:
    with transaction(collection) as records:
        for record in records:
            if record.get(key) == value:
                record.update(copy.deepcopy(changes))
                return copy.deepcopy(record)
    raise NotFound(f"{collection}: {key}={value!r} nenalezeno")


def reset_from_seed() -> list[str]:
    """Smaže živá data a nahradí je výchozími daty ze SEED_DIR."""
    seed_dir = Path(settings.SEED_DIR)
    data_dir = Path(settings.DATA_DIR)
    with _lock:
        if data_dir.exists():
            shutil.rmtree(data_dir)
        data_dir.mkdir(parents=True)
        copied = []
        for src in sorted(seed_dir.glob("*.json")):
            shutil.copy(src, data_dir / src.name)
            copied.append(src.stem)
    return copied


def ensure_initialized() -> None:
    """Při prvním spuštění naplní DATA_DIR ze seed dat."""
    if not Path(settings.DATA_DIR).exists():
        reset_from_seed()
