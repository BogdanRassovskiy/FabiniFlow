from pathlib import Path

from agent import approvals

_STATIC = Path(__file__).parent / "static" / "web"


def attention(request):
    """Počet položek čekajících na člověka – pro červený odznak v navigaci."""
    return {"attention_counts": approvals.pending_counts()}


def asset_version(request):
    """Verze CSS/JS podle poslední změny souborů – prohlížeč tak nepoužije starou kopii z cache."""
    return {"asset_version": int(max(f.stat().st_mtime for f in _STATIC.iterdir()))}
