#!/usr/bin/env python3
"""
Re-ranking semántico de enlaces candidatos a política de privacidad.

Corre en el entorno conda `poligraph` (el único con `sentence_transformers`).
Lee por stdin un JSON `{"candidatos": [{"href","texto"}, ...]}` y escribe por
stdout un JSON `[{"href","score"}, ...]` ordenado por similitud de coseno (0-1)
de cada enlace frente al *concepto* "política de privacidad" expresado en varios
idiomas. `buscador_politica.py` lo invoca con:

    conda run -n poligraph python _semantic_policy_ranker.py

Motivación: el matching léxico (regex/palabras clave) solo acierta si el texto
contiene exactamente una palabra conocida. Los embeddings comparan el
*significado*, así que generalizan a redacciones e idiomas no previstos
("Tus datos", "Cómo usamos la información", "Centro de privacidad"…).
"""

import os
import sys
import json
from urllib.parse import urlparse

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# Modelo multilingüe pequeño y bueno; se puede sustituir por env var.
_MODELO = os.getenv("POLICY_RANKER_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")

# Frases de referencia del concepto, en varios idiomas. La similitud de cada
# enlace se toma como el MÁXIMO coseno frente a cualquiera de estas frases.
_CONCEPTOS = [
    "política de privacidad",
    "privacy policy",
    "protección de datos personales",
    "cómo tratamos tus datos personales",
    "aviso de privacidad",
    "privacy notice",
    "Datenschutzerklärung",
    "politique de confidentialité",
]


def _texto_enlace(cand: dict) -> str:
    """Texto a incrustar: el texto visible del enlace + el último segmento del
    href (para enlaces sin texto o con texto genérico, p. ej. /tus-datos)."""
    href = cand.get("href", "") or ""
    seg = ""
    try:
        seg = (urlparse(href).path.rstrip("/").split("/")[-1]
               .replace("-", " ").replace("_", " "))
    except Exception:
        seg = ""
    texto = (cand.get("texto") or "").strip()
    return (texto + " " + seg).strip()


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        print("[]")
        return
    cands = data.get("candidatos", [])
    if not cands:
        print("[]")
        return

    from sentence_transformers import SentenceTransformer, util

    modelo = SentenceTransformer(_MODELO)
    emb_concepto = modelo.encode(_CONCEPTOS, convert_to_tensor=True,
                                 normalize_embeddings=True)
    textos = [_texto_enlace(c) for c in cands]
    emb_cand = modelo.encode(textos, convert_to_tensor=True,
                             normalize_embeddings=True)

    sims = util.cos_sim(emb_cand, emb_concepto)      # (n_cand, n_concepto)
    scores = sims.max(dim=1).values.tolist()         # mejor coseno por enlace

    salida = [{"href": c.get("href", ""), "score": round(float(s), 4)}
              for c, s in zip(cands, scores)]
    salida.sort(key=lambda x: -x["score"])
    print(json.dumps(salida))


if __name__ == "__main__":
    main()
