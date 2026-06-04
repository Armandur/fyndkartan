"""Kost-klassificering (HÄRLEDD ur ingredienser): kött/fisk -> varken; mejeri/ägg/honung/gelatin
-> vegetarisk; annars vegansk. Fristående (inga interna beroenden) så både details (read-time-
derivering) och database (bläddra-filtrets `get_product_diets`) kan dela den utan cirkulär import.

`\b` = ordstart så "kokosmjölk"/"havremjölk" (växt) inte träffar "mjölk". PLANT_OK nollar växt-
kompositer som börjar med en djur-delsträng (äggplanta osv)."""
import re

_MEAT = re.compile(
    r"\b(nötkött|fläsk|griskött|kyckling|kalkon|anka|bacon|skinka|prosciutto|korv|salami|chorizo|"
    r"lamm|vilt|älgkött|renkött|köttfärs|fläskfärs|blandfärs|kött|charkuteri|fisk|lax|sill|makrill|"
    r"tonfisk|torsk|sej|räk|krabba|hummer|mussl|ostron|ansjovis|sardin|skaldjur|kräft|blodpudding|"
    r"leverpastej|fiskolja|fiskbuljong|hönsbuljong|köttbuljong|hönskött)", re.I)
_ANIMAL = re.compile(
    r"\b(mjölk|grädde|gräddfil|filmjölk|smör|ost|yoghurt|kvarg|kesella|vassle|mjölkprotein|"
    r"mjölkpulver|kasein|laktos|ägg|äggula|äggvita|honung|gelatin|bivax|lanolin|löpe|smörfett|"
    r"vasslepulver|skummjölk)", re.I)
_PLANT_OK = ("kokosmjölk", "havremjölk", "sojamjölk", "mandelmjölk", "risdryck", "havredryck",
             "sojadryck", "äggplanta", "jordnötssmör", "mandelsmör", "kakaosmör", "sheasmör",
             "jordnötter", "frukost")


def classify_diet(ingredients):
    """'none' (kött/fisk) | 'vegetarian' (mejeri/ägg/honung/gelatin) | 'vegan' | None (ingen
    ingredienslista). Heuristik (markeras 'härledd' i UI:t); icke-livsmedel kan bli falskt 'vegan'."""
    if not ingredients:
        return None
    s = ingredients.lower()
    for ok in _PLANT_OK:
        s = s.replace(ok, " ")
    if _MEAT.search(s):
        return "none"
    if _ANIMAL.search(s):
        return "vegetarian"
    return "vegan"
