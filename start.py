#!/usr/bin/env python3
"""
Spuštění FabiniFlow jedním příkazem:  python3 start.py

- při prvním spuštění vytvoří virtuální prostředí .venv a nainstaluje závislosti,
- vytvoří .env z .env.example (doplňte OPENROUTER_API_KEY),
- spustí aplikaci na http://127.0.0.1:4445 (port lze změnit proměnnou FABINI_PORT).

Na serveru stačí zkopírovat .env.example do .env a doplnit OPENROUTER_API_KEY –
aplikace pak naslouchá na 0.0.0.0:4445 a otevírá se adresou serveru s portem.

Volby:  --reset   obnoví data z data/seed před spuštěním
"""

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REQUIREMENTS = ROOT / "requirements.txt"
STAMP = VENV / ".requirements"


def ensure_venv() -> None:
    if not PYTHON.exists():
        print("→ Vytvářím virtuální prostředí .venv", flush=True)
        venv.create(VENV, with_pip=True)
    wanted = REQUIREMENTS.read_text()
    if not STAMP.exists() or STAMP.read_text() != wanted:
        print("→ Instaluji závislosti z requirements.txt", flush=True)
        subprocess.run([PYTHON, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", REQUIREMENTS], check=True)
        STAMP.write_text(wanted)


def ensure_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        shutil.copy(ROOT / ".env.example", env)
        print("→ Vytvořen .env – doplňte do něj OPENROUTER_API_KEY (bez klíče agent neodpovídá)", flush=True)


def env_value(name: str, default: str) -> str:
    """Hodnota z prostředí, jinak z .env, jinak výchozí."""
    if os.environ.get(name):
        return os.environ[name]
    for line in (ROOT / ".env").read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == name and value.strip():
            return value.strip()
    return default


def main() -> None:
    if sys.version_info < (3, 12):
        sys.exit("FabiniFlow potřebuje Python 3.12 nebo novější.")
    ensure_venv()
    ensure_env()
    host = env_value("FABINI_HOST", "127.0.0.1")
    port = env_value("FABINI_PORT", "4445")
    env = {**os.environ, "FABINI_PORT": port}
    manage = [PYTHON, ROOT / "manage.py"]
    if "--reset" in sys.argv:
        subprocess.run([*manage, "reset_data"], check=True, env=env)
    # Bez DEBUG runserver statické soubory neservíruje – pro demo stačí --insecure.
    extra = ["--insecure"] if env_value("DJANGO_DEBUG", "1") != "1" else []
    print(f"→ FabiniFlow běží na http://{host}:{port}/  (ukončení Ctrl+C)", flush=True)
    try:
        subprocess.run([*manage, "runserver", f"{host}:{port}", *extra], env=env)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
