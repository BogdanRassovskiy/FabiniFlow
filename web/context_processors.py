from pathlib import Path

from agent import approvals

_CSS = Path(__file__).parent / "static" / "web" / "app.css"


def attention(request):
    """Počet položek čekajících na člověka – pro červený odznak v navigaci."""
    return {"attention_counts": approvals.pending_counts()}


def asset_version(request):
    """Verze CSS podle času změny souboru – prohlížeč tak nepoužije starou kopii z cache."""
    return {"asset_version": int(_CSS.stat().st_mtime)}
