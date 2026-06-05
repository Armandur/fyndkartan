"""Auth-gating-tester: säkerställ att API:t INTE släpper igenom anonym åtkomst.

Hela /v1-datalagret är gatat (`require_consumer`: inloggad app-användare ELLER X-API-Key) och
konsol-/drift-endpoints kräver admin (`require_admin`). Det här är säkerhets-invarianten: utan auth
ska gatade endpoints ge 401/403, och de få öppna ska svara. Via Starlette TestClient (ingen riktig
server, ingen lifespan -> gatingen avvisar före route-logiken).

Kör: `.venv/bin/python tests/test_auth.py` (eller pytest).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402

client = TestClient(app, raise_server_exceptions=False)

# (metod, path) som KRÄVER auth -> ska ge 401/403 utan inloggning.
_GATED = [
    ("GET", "/v1/products/catalog/browse?q=test"),
    ("GET", "/v1/products/7310865088307"),
    ("GET", "/v1/products/7310865088307/history"),
    ("GET", "/v1/products/7310865088307/stores"),
    ("GET", "/v1/compare/near?lat=59.3&lng=18.0"),
    ("GET", "/v1/admin/overview"),
    ("GET", "/v1/admin/manufacturers"),
    ("GET", "/v1/admin/categories"),
    ("GET", "/v1/tags"),         # utbruten till routes/admin_vocab.py - säkra att gatingen följde med
    ("GET", "/v1/providers"),    # samma router (require_admin på router-nivå)
    ("POST", "/v1/sync"),
    ("POST", "/v1/offers/sweep"),
    ("POST", "/v1/admin/partials/upgrade"),
]
# Öppna (måste vara nåbara utan auth).
_OPEN = [("GET", "/healthz"), ("GET", "/"), ("GET", "/admin")]


def test_gated_endpoints_reject_anon():
    bad = []
    for method, path in _GATED:
        r = client.request(method, path)
        if r.status_code not in (401, 403):
            bad.append(f"{method} {path} -> {r.status_code} (väntat 401/403)")
    assert not bad, "Gatade endpoints släppte igenom anonymt:\n" + "\n".join(bad)
    return len(_GATED)


def test_open_endpoints_reachable():
    bad = []
    for method, path in _OPEN:
        r = client.request(method, path)
        if r.status_code != 200:
            bad.append(f"{method} {path} -> {r.status_code} (väntat 200)")
    assert not bad, "Öppna endpoints svarade inte:\n" + "\n".join(bad)
    return len(_OPEN)


def test_bad_api_key_rejected():
    """Felaktig X-API-Key ger inte åtkomst (gatat som anonymt)."""
    r = client.get("/v1/products/catalog/browse?q=test", headers={"X-API-Key": "fel-nyckel-123"})
    assert r.status_code in (401, 403), f"Ogiltig API-nyckel gav {r.status_code}"
    return True


if __name__ == "__main__":
    print(f"OK: {test_gated_endpoints_reject_anon()} gatade endpoints avvisar anonymt")
    print(f"OK: {test_open_endpoints_reachable()} öppna endpoints nåbara")
    test_bad_api_key_rejected()
    print("OK: ogiltig X-API-Key avvisas")
