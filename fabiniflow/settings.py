"""
Nastavení projektu FabiniFlow.

Doménová data (obchody, objednávky, platby, sklady, synchronizace, běhy agenta,
trace log) se neukládají do databáze, ale do JSON souborů v adresáři DATA_DIR.
Proto nejsou zapnuté aplikace závislé na ORM (admin, auth, sessions).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-insecure-key")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
# Demo: aplikace se otevírá přímo adresou serveru s portem, proto povolíme libovolný host.
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "core",
    "shops",
    "agent",
    "simulator",
    "web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    # CSRF ochrana je pro demo vypnutá (bez přihlášení a domény by jen překážela).
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "fabiniflow.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "web.context_processors.attention",
                "web.context_processors.asset_version",
            ],
        },
    },
]

WSGI_APPLICATION = "fabiniflow.wsgi.application"

# Žádná relační databáze – vše je v JSON (viz core.storage).
DATABASES = {}

LANGUAGE_CODE = "cs"
TIME_ZONE = "Europe/Prague"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

# --- FabiniFlow ---

# Výchozí (seed) data, která se při resetu zkopírují do DATA_DIR.
SEED_DIR = BASE_DIR / "data" / "seed"
# Živý stav aplikace; je v .gitignore a lze ho kdykoli obnovit příkazem `reset_data`.
DATA_DIR = Path(os.getenv("FABINI_DATA_DIR", BASE_DIR / "data" / "runtime"))

# Simulované externí systémy (aplikace simulator). V produkci by se zde
# jen změnila adresa na skutečné API – agent zůstává stejný.
# Port aplikace (start.py). Simulované externí systémy běží ve stejném procesu pod /mock/.
PORT = int(os.getenv("FABINI_PORT", "4445"))
MOCK_API_BASE_URL = os.getenv("MOCK_API_BASE_URL", f"http://127.0.0.1:{PORT}/mock")
# Jak dlouho „visí“ systém v režimu timeout (klient má vlastní kratší timeout).
MOCK_TIMEOUT_SECONDS = 15

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
if OPENROUTER_API_KEY.endswith("..."):  # nevyplněná ukázková hodnota z .env.example
    OPENROUTER_API_KEY = ""
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_DEFAULT_MODEL = os.getenv("OPENROUTER_DEFAULT_MODEL", "anthropic/claude-haiku-5.5")

# Doporučené modely v UI (všechny podporují tool calling i obrázky na vstupu).
# Ceny USD / 1M tokenů vstup/výstup dle OpenRouter k 30. 10. 2026.
# Ostatní modely se v UI nabízejí dynamicky z /models, filtrované na `tools`.
OPENROUTER_RECOMMENDED_MODELS = [
    {"id": "anthropic/claude-haiku-5.5", "label": "Claude Haiku 5.5 – levný, spolehlivé nástroje, obrázky", "price": "0.10 / 0.50"},
    {"id": "google/gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash Lite – levný, obrázky", "price": "0.10 / 0.40"},
]
