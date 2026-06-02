"""Lättviktiga static-embeddings (model2vec, CPU/numpy - ingen torch) för semantisk
produktnamn-likhet i märkesvaru-paringsförslagen.

Lazy-laddad singleton: modellen hämtas vid första anropet (HF-cache därefter). Degraderar
TYST till None om den inte kan laddas (offline första gången, saknad modell, ...) så
anroparen kan falla tillbaka på lexikal matchning (`brands.score`). Modell via `EMBED_MODEL`.
"""

import logging

from . import config

log = logging.getLogger("matbutiker")

_model = None
_failed = False


def _get_model():
    global _model, _failed
    if _model is not None or _failed:
        return _model
    try:
        from model2vec import StaticModel

        _model = StaticModel.from_pretrained(config.EMBED_MODEL)
        log.info("embeddings: laddade %s (dim %s)", config.EMBED_MODEL, getattr(_model, "dim", "?"))
    except Exception as e:  # noqa: BLE001
        log.warning("embeddings: kunde inte ladda %s: %s - faller tillbaka på lexikal matchning",
                    config.EMBED_MODEL, e)
        _failed = True
    return _model


def available():
    return _get_model() is not None


def name_cosines(query, candidates):
    """Cosine-likhet [-1,1] mellan `query`-namnet och varje kandidatnamn (lista, samma
    ordning). None om embeddings ej tillgängliga -> anroparen faller tillbaka på lexikalt.
    Tomma kandidatnamn ger 0."""
    candidates = list(candidates)
    if not candidates:
        return []
    m = _get_model()
    if m is None:
        return None
    import numpy as np

    vecs = m.encode([query or ""] + [c or "" for c in candidates])
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vecs = vecs / norms
    sims = vecs[1:] @ vecs[0]
    return [float(s) if (candidates[i] or "").strip() else 0.0 for i, s in enumerate(sims)]


def group_cosines(members, candidates):
    """Cosine mellan en GRUPPS centroid (medel av medlemmarnas embeddings, normaliserat) och
    varje kandidat. None om embeddings ej tillgängliga; tomma kandidatnamn -> 0."""
    members = [x for x in members if (x or "").strip()]
    candidates = list(candidates)
    if not candidates or not members:
        return None if _get_model() is None else [0.0] * len(candidates)
    m = _get_model()
    if m is None:
        return None
    import numpy as np

    vecs = m.encode(members + [c or "" for c in candidates])
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vecs = vecs / norms
    centroid = vecs[: len(members)].mean(axis=0)
    centroid = centroid / (np.linalg.norm(centroid) or 1.0)
    sims = vecs[len(members):] @ centroid
    return [float(s) if (candidates[i] or "").strip() else 0.0 for i, s in enumerate(sims)]
