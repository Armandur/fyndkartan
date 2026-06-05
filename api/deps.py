"""Delade FastAPI-dependencies. Importeras härifrån (av main.py och route-moduler i
`api/routes/`), aldrig kopierade lokalt - en sanningskälla för auth-grindarna.

- `require_consumer`: gatar konsument-/data-endpoints (inloggad app-användare, X-API-Key
  eller betrodd konsol-admin).
- `require_admin`: bor i `auth.py`; re-exporteras här så route-moduler kan importera båda
  grindarna från `deps`.
"""
from fastapi import Depends, HTTPException, Request

from . import auth

require_admin = auth.require_admin


def require_consumer(request: Request, user=Depends(auth.current_user)):
    """Gatar /v1-dataendpoints: kräver inloggad app-användare (session/bearer), giltig
    API-nyckel (X-API-Key) ELLER inloggad konsol-admin (betrodd, t.ex. API-testaren).
    Inget är öppet anonymt externt."""
    if user or getattr(request.state, "api_key", None) or auth.current_admin(request):
        return user
    raise HTTPException(status_code=401, detail="Autentisering krävs: logga in eller skicka en API-nyckel.")
