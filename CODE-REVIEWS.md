# Kodgranskningar - matbutiker

Logg över genomförda kodgranskningar (nyast först). Varje granskning gjordes oberoende av Claude
(Opus 4.8) + Gemini CLI och vägdes sedan samman + verifierades mot koden. HTML-rapporter genereras
för läsning men tas bort efter hantering; den beständiga dokumentationen är här.

## 2026-06-13 - Schemaläggning av ICA/Coop per-butik-priscrawl + omkörning

**Scope:** `api/store_crawl.py` (omkörnings-logik + AIMD-loop), `api/main.py` (`scheduled_crawl`),
`web/admin.js` (etiketter). **Åtgärdad i commit `ad9c06a`.**

**Utfall:** Inga kritiska eller medel-buggar. Den asynkrona AIMD-loopen och omkörnings-logiken
verifierades korrekt: inga race conditions (single-threaded asyncio, inga await mellan
räknar-muteringar), ingen dubbelräkning (`stores_ok + errors == total` utan breaker-abort),
omkörningen körs exakt en gång, circuit-breakern respekteras, gating på `len(tasks)` (ej
`cs["active"]`) som avsett.

Tre låg-allvarliga fynd, alla åtgärdade i samma commit:

| # | Fynd | Källa | Åtgärd |
|---|------|-------|--------|
| 1 | `web/admin.js` översiktskort visade kvar "Sortiment-crawl" medan inställningsfliken ändrats | Gemini (Claude missade) | Båda etiketterna nu "Pris-crawl (sortiment + ICA/Coop-butikspris)" |
| 2 | `retrying`-flaggan sattes i state/API men renderades inte -> omkörningsfas osynlig (`done`=100%, kunde se hängt ut) | Gemini implicerade, Claude bekräftade | Per-butik-crawlkortet visar badge "omkörning av felade butiker" |
| 3 | `last_error` nollställdes inte vid lyckad omkörning -> "ok"-körning kunde bära gammalt fel | Claude (Gemini missade) | `cs["last_error"]=None` när `failed` töms |

**Avfärdat (bekräftat avsiktligt/säkert):** `waf_streak` kan nå breakern snabbt vid samtidig
transient störning (avsett circuit-breaker-beteende); `done`=100% under omkörning är tekniskt
korrekt (löst av #2 via synlig retry-status).

**Samarbete:** Gemini var träffsäkert och konservativt här (0 falska positiva), till skillnad från
matstatistik-granskningen samma dag där Gemini gav 3 falska KRITISKT om "heltalsdivision" som
krävde schema-verifiering (alla pris-kolumner är `double precision`) för att avfärdas.
