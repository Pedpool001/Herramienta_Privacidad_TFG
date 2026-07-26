"""
Mapeo requisito → normativa y generación del gráfico de cumplimiento por normativa.

Módulo compartido por `salida.py` (informe individual) y `salida_batch.py`
(informe batch). Ambos construyen una lista de pares (requisito, veredicto) y
llaman a `grafico_normativas()`, que devuelve el fragmento HTML de la sección.

Un mismo requisito puede emanar de varias normativas a la vez: por ejemplo, R2
(bloqueo de cookies no exentas) deriva del RGPD (base jurídica del
consentimiento), de la Directiva ePrivacy (acceso al terminal) y de la LSSI
(su transposición española en el art. 22.2). Por eso el mapeo es de uno a
varios y la suma de requisitos por normativa excede los 19.
"""

from __future__ import annotations

# ── Mapeo normativo ───────────────────────────────────────────────────────────
#
# Justificación del reparto:
#
#   RGPD     — Reglamento (UE) 2016/679. Todos los requisitos derivan en última
#              instancia de sus principios (art. 5) o de obligaciones concretas
#              (arts. 7, 12, 13/14, 25, 32, 37-39).
#   ePrivacy — Directiva 2002/58/CE, art. 5.3: acceso y almacenamiento de
#              información en el equipo terminal del usuario. Cubre cookies,
#              web beacons, almacenamiento local y su plazo de persistencia.
#   LSSI     — Ley 34/2002, art. 22.2: transposición española de la ePrivacy en
#              lo relativo a cookies y tecnologías de seguimiento similares.
#   AEPD     — Guía sobre el uso de las cookies y Guía de privacidad desde el
#              diseño: información por capas, granularidad, revocabilidad,
#              tecnologías de seguimiento y claridad del lenguaje.
#   CEPD     — Directrices 3/2022 (patrones engañosos) y 4/2019 (privacidad
#              desde el diseño y por defecto, art. 25 RGPD).

NORMATIVAS: dict[str, dict] = {
    "RGPD": {
        "nombre": "RGPD",
        "detalle": "Reglamento (UE) 2016/679",
        "requisitos": [f"R{i}" for i in range(1, 20)],
    },
    "ePrivacy": {
        "nombre": "Directiva ePrivacy",
        "detalle": "Directiva 2002/58/CE, art. 5.3",
        "requisitos": ["R2", "R3", "R6", "R8", "R10"],
    },
    "LSSI": {
        "nombre": "LSSI",
        "detalle": "Ley 34/2002, art. 22.2",
        "requisitos": ["R2", "R3", "R6"],
    },
    "AEPD": {
        "nombre": "AEPD",
        "detalle": "Guías de cookies y privacidad desde el diseño",
        "requisitos": ["R1", "R4", "R5", "R7", "R13", "R14"],
    },
    "CEPD": {
        "nombre": "CEPD",
        "detalle": "Directrices 3/2022 y 4/2019",
        "requisitos": ["R12", "R13"],
    },
}

# Paleta coherente con el resto de informes
_COLOR = {
    "PASSED":       "#1a7f37",
    "WARNING":      "#9a6700",
    "FAILED":       "#cf222e",
    "NO_EVALUABLE": "#57606a",
}

_LABEL = {
    "PASSED":       "Cumple",
    "WARNING":      "Aviso",
    "FAILED":       "Falla",
    "NO_EVALUABLE": "Sin dato",
}

_ORDEN = ["PASSED", "WARNING", "FAILED", "NO_EVALUABLE"]

_PI = 3.14159265358979


def _normalizar(veredicto: str) -> str:
    """Agrupa ERROR y NO_EVALUABLE en una única categoría 'sin dato'."""
    if veredicto in ("PASSED", "WARNING", "FAILED"):
        return veredicto
    return "NO_EVALUABLE"


def contar_por_normativa(pares: list[tuple[str, str]]) -> dict[str, dict[str, int]]:
    """
    Agrupa los veredictos por normativa.

    Args:
        pares: lista de tuplas (requisito, veredicto). En el informe individual
               hay un par por requisito; en el batch, uno por cada
               combinación sitio × requisito.

    Returns:
        {sigla_normativa: {PASSED: n, WARNING: n, FAILED: n, NO_EVALUABLE: n}}
    """
    conteos = {sigla: {v: 0 for v in _ORDEN} for sigla in NORMATIVAS}

    for req, veredicto in pares:
        # NO_SOLICITADO se excluye: el usuario decidió no medir ese requisito
        if veredicto == "NO_SOLICITADO":
            continue
        cat = _normalizar(veredicto)
        for sigla, meta in NORMATIVAS.items():
            if req in meta["requisitos"]:
                conteos[sigla][cat] += 1

    return conteos


def indice_cumplimiento(conteo: dict[str, int]) -> float | None:
    """
    Índice de cumplimiento de una normativa: (PASSED + 0,5 × WARNING) / total.

    Se emplea la misma fórmula que en el análisis por herramienta de la
    memoria, de modo que los porcentajes sean comparables entre secciones.
    Los veredictos sin dato cuentan en el denominador, por lo que penalizan
    el índice; el donut los muestra aparte para que el dato sea transparente.
    """
    total = sum(conteo.values())
    if not total:
        return None
    return (conteo["PASSED"] + 0.5 * conteo["WARNING"]) / total * 100


def _donut(sigla: str, conteo: dict[str, int]) -> str:
    """Genera un donut SVG compacto para una normativa concreta."""
    meta  = NORMATIVAS[sigla]
    total = sum(conteo.values())
    if not total:
        return ""

    R, r = 52, 34
    cx = cy = 60
    stroke_w = R - r
    mid_r    = (R + r) / 2
    circum   = 2 * _PI * mid_r

    segmentos = []
    offset = 0.0
    for v in _ORDEN:
        if conteo[v] == 0:
            continue
        dash = conteo[v] / total * circum
        segmentos.append((dash, offset, _COLOR[v], _LABEL[v], conteo[v]))
        offset += dash

    arcos = "".join(
        f'<circle cx="{cx}" cy="{cy}" r="{mid_r:.1f}" fill="none" '
        f'stroke="{color}" stroke-width="{stroke_w}" '
        f'stroke-dasharray="{dash:.2f} {circum:.2f}" '
        f'stroke-dashoffset="{-off:.2f}" '
        f'transform="rotate(-90 {cx} {cy})">'
        f'<title>{lbl}: {n} ({n / total * 100:.1f}%)</title></circle>'
        for dash, off, color, lbl, n in segmentos
    )

    idx = indice_cumplimiento(conteo)
    pct = f"{idx:.0f}%" if idx is not None else "–"

    n_reqs = len(meta["requisitos"])
    reqs   = ", ".join(meta["requisitos"])

    return f"""
  <div class="norm-card">
    <svg viewBox="0 0 120 120" width="120" height="120" role="img"
         aria-label="Cumplimiento de {meta['nombre']}: {pct}">
      {arcos}
      <text x="{cx}" y="{cy + 2}" text-anchor="middle" font-size="20"
            font-weight="700" fill="#24292f">{pct}</text>
      <text x="{cx}" y="{cy + 16}" text-anchor="middle" font-size="8"
            fill="#57606a">cumplimiento</text>
    </svg>
    <div class="norm-name">{meta['nombre']}</div>
    <div class="norm-sub">{meta['detalle']}</div>
    <div class="norm-reqs" title="{reqs}">{n_reqs} requisitos</div>
  </div>"""


def grafico_normativas(pares: list[tuple[str, str]]) -> str:
    """
    Devuelve el fragmento HTML completo de la sección "Cumplimiento por
    normativa": una rejilla de donuts, uno por cada normativa.

    Args:
        pares: lista de tuplas (requisito, veredicto).
    """
    conteos = contar_por_normativa(pares)
    tarjetas = "".join(_donut(sigla, conteos[sigla]) for sigla in NORMATIVAS)
    if not tarjetas.strip():
        return ""

    leyenda = "".join(
        f'<span class="leg-item">'
        f'<span class="leg-dot" style="background:{_COLOR[v]}"></span>{_LABEL[v]}</span>'
        for v in _ORDEN
    )

    return f"""
<div class="norm-wrap">
  <div class="norm-grid">{tarjetas}</div>
  <div class="norm-leyenda">{leyenda}</div>
  <p class="norm-nota">
    El índice de cumplimiento se calcula como PASSED + 0,5 × WARNING sobre el
    total de evaluaciones de los requisitos asociados a cada normativa. Un mismo
    requisito puede computar en varias normativas cuando su obligación deriva de
    más de una fuente.
  </p>
</div>"""


# CSS de la sección, compartido por ambos informes.
CSS_NORMATIVAS = """
  .norm-wrap { margin: 16px 0 8px; }
  .norm-grid {
    display: flex; flex-wrap: wrap; gap: 16px; justify-content: flex-start;
  }
  .norm-card {
    flex: 0 0 auto; width: 150px; padding: 12px 8px; text-align: center;
    border: 1px solid #d0d7de; border-radius: 8px; background: #fff;
  }
  .norm-name { font-weight: 700; font-size: 14px; margin-top: 6px; color: #24292f; }
  .norm-sub  { font-size: 11px; color: #57606a; margin-top: 2px; line-height: 1.3; }
  .norm-reqs { font-size: 11px; color: #57606a; margin-top: 6px;
               border-top: 1px solid #eaeef2; padding-top: 6px; }
  .norm-leyenda { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 14px; }
  .norm-leyenda .leg-item { display: flex; align-items: center; gap: 6px;
                            font-size: 12px; color: #57606a; }
  .norm-leyenda .leg-dot  { width: 10px; height: 10px; border-radius: 50%;
                            display: inline-block; }
  .norm-nota { font-size: 11px; color: #57606a; margin-top: 10px;
               line-height: 1.5; max-width: 760px; }
"""
