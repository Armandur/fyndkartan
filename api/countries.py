"""Landnamn <-> ISO-3166-kod via babel (CLDR). Används för att normalisera ursprung till
svenska och härleda flagg-emoji. Ursprung kommer på svenska (Coop/ICA) eller engelska
(Axfood); båda matchas mot ISO-koden, och koden ger kanoniskt svenskt namn + flagga."""
import re

from babel import Locale

_SV = Locale("sv").territories
_SV_TO_CODE = {n.lower(): c for c, n in _SV.items() if len(c) == 2}
_EN_TO_CODE = {n.lower(): c for c, n in Locale("en").territories.items() if len(c) == 2}
# Vardagliga/historiska/förkortade varianter som inte är egna CLDR-namn.
_EXTRA = {"holland": "NL", "england": "GB", "eu": "EU", "storbritannien": "GB",
          "makedonien": "MK", "czech republic": "CZ"}


def country_code(name):
    """ISO-3166 alfa-2-kod för ett landnamn (svenskt eller engelskt), annars None."""
    k = (name or "").strip().lower()
    if not k:
        return None
    return _SV_TO_CODE.get(k) or _EN_TO_CODE.get(k) or _EXTRA.get(k)


def country_sv(name):
    """Kanoniskt svenskt landnamn för ett (sv/en) namn, annars None."""
    code = country_code(name)
    return _SV.get(code) if code else None


def sv_name(code):
    """Kanoniskt svenskt landnamn för en ISO-kod, annars koden själv."""
    return _SV.get(code, code) if code else None


def flag_emoji(code):
    """Flagg-emoji ur en alfa-2-kod (regional indicator-par). EU -> EU-flaggan."""
    if not code or len(code) != 2 or not code.isalpha():
        return None
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code.upper())


def codes_for(names):
    """Lista (redan uppdelade) landnamn -> lista ISO-koder (dedup, ordningsbevarande). För kort
    som redan har normaliserade origin-namn (offers/bläddra/katalog) och bara behöver flagg-koder."""
    out = []
    for n in names or []:
        code = country_code(n)
        if code and code not in out:
            out.append(code)
    return out


def split_origins(text):
    """Ursprungssträng -> (normaliserat svenskt namn, [ISO-koder]). Hanterar komma/snedstreck-
    separerade fleruländer ('Sverige, Norge', 'EU/Marocko'); igenkända delar översätts till
    svenska och ger en kod, okända delar ('Icke-EU', fiskeområden) behålls som text utan kod."""
    if not text:
        return text, []
    # Sanera skrapskräp: kapa vid HTML-tagg/radbrytning, och släng absurt långa strängar
    # (origin-fältet har ibland förorenats med produkttext/SVG-markup) -> inget ursprung.
    s = re.sub(r"\s+", " ", re.split(r"[<\n]", str(text))[0]).strip()
    if not s or len(s) > 60:
        return None, []
    parts = [p.strip() for p in re.split(r"[,/]", s) if p.strip()]
    if not parts:
        return None, []
    names, codes = [], []
    for p in parts:
        code = country_code(p)
        if code:
            names.append(_SV.get(code, p))
            if code not in codes:
                codes.append(code)
        else:
            names.append(p)
    return ", ".join(names), codes
