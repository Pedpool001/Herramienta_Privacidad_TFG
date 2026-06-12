"""
Generador del informe HTML batch (múltiples sitios).

Produce un fichero HTML autónomo con:
  - Tarjetas de resumen global
  - Gráfico de barras apiladas por requisito (R1-R19)
  - Gráfico de barras por sitio (% de cumplimiento)
  - Gráfico de donut con distribución global de veredictos
  - Matriz sitio × requisito con color por veredicto
  - Enlace al informe individual de cada sitio
"""

from __future__ import annotations
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

REQUISITOS = [f"R{i}" for i in range(1, 20)]

NOMBRES_REQ: dict[str, str] = {
    "R1":  "Información por capas",
    "R2":  "Bloqueo cookies no exentas",
    "R3":  "Web Beacons",
    "R4":  "Granularidad en la elección",
    "R5":  "Revocabilidad sencilla",
    "R6":  "Anti-Keylogging",
    "R7":  "Fingerprinting",
    "R8":  "Storage de terceros",
    "R9":  "Minimización de datos",
    "R10": "Limitación del plazo",
    "R11": "Desvinculación y aislamiento",
    "R12": "Software de terceros",
    "R13": "Dark Patterns",
    "R14": "Lenguaje claro y sencillo",
    "R15": "Identificación de responsables",
    "R16": "Correspondencia aviso-ejecución",
    "R17": "Redirección HTTPS",
    "R18": "Content Security Policy",
    "R19": "Designación del DPO",
}

_COLOR = {
    "PASSED":       "#1a7f37",
    "WARNING":      "#9a6700",
    "FAILED":       "#cf222e",
    "NO_EVALUABLE": "#57606a",
    "ERROR":        "#888888",
}
_BG = {
    "PASSED":       "#dafbe1",
    "WARNING":      "#fff8c5",
    "FAILED":       "#ffebe9",
    "NO_EVALUABLE": "#f6f8fa",
    "ERROR":        "#f0f0f0",
}
_LABEL = {
    "PASSED":       "Cumple",
    "WARNING":      "Aviso",
    "FAILED":       "Falla",
    "NO_EVALUABLE": "Sin dato",
    "ERROR":        "Error",
}
_ORDEN_VEREDICTOS = ["PASSED", "WARNING", "FAILED", "NO_EVALUABLE", "ERROR"]


# ─────────────────────────────────────────────────────────────────────────────
# Funciones de cálculo
# ─────────────────────────────────────────────────────────────────────────────

def _veredicto(resultados_sitio: dict, req: str) -> str:
    return resultados_sitio.get(req, {}).get("veredicto", "ERROR")


def _stats_por_req(resultados_batch: dict) -> dict[str, dict[str, int]]:
    """Para cada requisito, cuenta cuántos sitios tienen cada veredicto."""
    stats: dict[str, dict[str, int]] = {}
    for req in REQUISITOS:
        c = {v: 0 for v in _ORDEN_VEREDICTOS}
        for res in resultados_batch.values():
            v = _veredicto(res, req)
            key = v if v in c else "ERROR"
            c[key] += 1
        stats[req] = c
    return stats


def _stats_por_sitio(resultados_batch: dict) -> dict[str, dict[str, int]]:
    """Para cada sitio, cuenta cuántos requisitos tienen cada veredicto."""
    stats: dict[str, dict[str, int]] = {}
    for url, res in resultados_batch.items():
        c = {v: 0 for v in _ORDEN_VEREDICTOS}
        for req in REQUISITOS:
            v = _veredicto(res, req)
            key = v if v in c else "ERROR"
            c[key] += 1
        stats[url] = c
    return stats


def _global_counts(resultados_batch: dict) -> dict[str, int]:
    """Conteo global de veredictos (todos los sitios × todos los requisitos)."""
    c = {v: 0 for v in _ORDEN_VEREDICTOS}
    for res in resultados_batch.values():
        for req in REQUISITOS:
            v = _veredicto(res, req)
            key = v if v in c else "ERROR"
            c[key] += 1
    return c


def _dominio(url: str) -> str:
    return urlparse(url).netloc.lstrip("www.")


# ─────────────────────────────────────────────────────────────────────────────
# Generadores de fragmentos HTML
# ─────────────────────────────────────────────────────────────────────────────

def _tarjetas_resumen(resultados_batch: dict) -> str:
    n_sitios = len(resultados_batch)
    gc = _global_counts(resultados_batch)
    total_eval = sum(gc.values())

    def pct(v):
        return f"{gc[v]/total_eval*100:.1f}%" if total_eval else "–"

    tarjetas = [
        ("Sitios auditados", str(n_sitios), "#0969da", "#ddf4ff"),
        ("✅ Cumple",   f"{gc['PASSED']} ({pct('PASSED')})",   _COLOR["PASSED"],       _BG["PASSED"]),
        ("⚠️ Aviso",    f"{gc['WARNING']} ({pct('WARNING')})",  _COLOR["WARNING"],      _BG["WARNING"]),
        ("❌ Falla",    f"{gc['FAILED']} ({pct('FAILED')})",    _COLOR["FAILED"],       _BG["FAILED"]),
        ("— Sin dato", f"{gc['NO_EVALUABLE'] + gc['ERROR']}",  _COLOR["NO_EVALUABLE"], _BG["NO_EVALUABLE"]),
    ]

    items = "".join(
        f'<div class="card" style="color:{c};background:{bg}">'
        f'<div class="card-val">{val}</div>'
        f'<div class="card-lbl">{lbl}</div></div>'
        for lbl, val, c, bg in tarjetas
    )
    return f'<div class="cards">{items}</div>'


def _grafico_donut(resultados_batch: dict) -> str:
    """Donut SVG con la distribución global de veredictos."""
    gc = _global_counts(resultados_batch)
    total = sum(gc.values())
    if not total:
        return ""

    R, r = 80, 50   # radio exterior e interior
    cx, cy = 100, 100
    stroke_w = R - r

    segments = []
    offset = 0.0
    circum = 2 * 3.14159265 * ((R + r) / 2)

    for v in _ORDEN_VEREDICTOS:
        if gc[v] == 0:
            continue
        frac = gc[v] / total
        dash = frac * circum
        segments.append((dash, offset, _COLOR[v], _LABEL[v], gc[v]))
        offset += dash

    mid_r = (R + r) / 2
    paths = "".join(
        f'<circle cx="{cx}" cy="{cy}" r="{mid_r:.1f}" fill="none" '
        f'stroke="{color}" stroke-width="{stroke_w}" '
        f'stroke-dasharray="{dash:.2f} {circum:.2f}" '
        f'stroke-dashoffset="{-off:.2f}" '
        f'transform="rotate(-90 {cx} {cy})">'
        f'<title>{lbl}: {count} ({count/total*100:.1f}%)</title></circle>'
        for dash, off, color, lbl, count in segments
    )

    leyenda = "".join(
        f'<span class="leg-item"><span class="leg-dot" style="background:{_COLOR[v]}"></span>'
        f'{_LABEL[v]}: {gc[v]} ({gc[v]/total*100:.1f}%)</span>'
        for v in _ORDEN_VEREDICTOS if gc[v] > 0
    )

    return f"""
<div class="chart-wrap">
  <h3>Distribución global de veredictos</h3>
  <div class="donut-row">
    <svg viewBox="0 0 200 200" width="200" height="200">
      {paths}
      <text x="{cx}" y="{cy-8}" text-anchor="middle" font-size="14" font-weight="bold" fill="#24292f">{total}</text>
      <text x="{cx}" y="{cy+10}" text-anchor="middle" font-size="10" fill="#57606a">evaluaciones</text>
    </svg>
    <div class="leyenda">{leyenda}</div>
  </div>
</div>"""


def _barras_por_requisito(resultados_batch: dict) -> str:
    """Barras apiladas horizontales: una por requisito, apiladas por veredicto."""
    stats = _stats_por_req(resultados_batch)
    n = len(resultados_batch)
    if not n:
        return ""

    filas = ""
    for req in REQUISITOS:
        c = stats[req]
        nombre = NOMBRES_REQ.get(req, req)
        segmentos = "".join(
            f'<div style="flex:{c[v]};background:{_COLOR[v]}" '
            f'title="{_LABEL[v]}: {c[v]}">'
            f'{"" if c[v] < 1 else ""}</div>'
            for v in _ORDEN_VEREDICTOS if c[v] > 0
        )
        pct_pass = f"{c['PASSED']/n*100:.0f}%"
        filas += f"""
<div class="bar-row">
  <div class="bar-label"><span class="req-tag">{req}</span>{nombre}</div>
  <div class="bar-track">{segmentos}</div>
  <div class="bar-pct">{pct_pass}</div>
</div>"""

    leyenda = "".join(
        f'<span class="leg-item"><span class="leg-dot" style="background:{_COLOR[v]}"></span>{_LABEL[v]}</span>'
        for v in _ORDEN_VEREDICTOS
    )

    return f"""
<div class="chart-wrap">
  <h3>Cumplimiento por requisito</h3>
  <div class="leyenda mb">{leyenda}</div>
  <div class="bar-chart">{filas}</div>
  <p class="nota">El porcentaje indica los sitios que <strong>Cumplen</strong> el requisito.</p>
</div>"""


def _barras_por_sitio(resultados_batch: dict) -> str:
    """Barras apiladas horizontales: una por sitio."""
    stats = _stats_por_sitio(resultados_batch)
    n_req = len(REQUISITOS)
    if not stats:
        return ""

    filas = ""
    for url, c in stats.items():
        dom = _dominio(url)
        segmentos = "".join(
            f'<div style="flex:{c[v]};background:{_COLOR[v]}" '
            f'title="{_LABEL[v]}: {c[v]}"></div>'
            for v in _ORDEN_VEREDICTOS if c[v] > 0
        )
        pct_pass = f"{c['PASSED']/n_req*100:.0f}%"
        filas += f"""
<div class="bar-row">
  <div class="bar-label site-label" title="{url}">{dom}</div>
  <div class="bar-track">{segmentos}</div>
  <div class="bar-pct">{pct_pass}</div>
</div>"""

    return f"""
<div class="chart-wrap">
  <h3>Cumplimiento por sitio</h3>
  <div class="bar-chart">{filas}</div>
  <p class="nota">El porcentaje indica los requisitos que el sitio <strong>Cumple</strong>.</p>
</div>"""


def _matriz(resultados_batch: dict) -> str:
    """Tabla sitio × requisito con celdas coloreadas por veredicto."""
    _ICO = {"PASSED": "✓", "WARNING": "!", "FAILED": "✗",
            "NO_EVALUABLE": "–", "ERROR": "E"}

    cab_reqs = "".join(
        f'<th title="{NOMBRES_REQ.get(r, r)}">{r}</th>'
        for r in REQUISITOS
    )
    cabecera = f"<tr><th>Sitio</th>{cab_reqs}</tr>"

    filas = ""
    for url, res in resultados_batch.items():
        dom = _dominio(url)
        celdas = ""
        for req in REQUISITOS:
            v = _veredicto(res, req)
            ico = _ICO.get(v, "?")
            celdas += (
                f'<td style="background:{_BG.get(v,"#f6f8fa")};'
                f'color:{_COLOR.get(v,"#57606a")};font-weight:600" '
                f'title="{v}">{ico}</td>'
            )
        filas += f"<tr><td class='site-cell' title='{url}'>{dom}</td>{celdas}</tr>"

    return f"""
<div class="chart-wrap">
  <h3>Matriz sitio × requisito</h3>
  <div class="matrix-wrap">
    <table class="matrix">
      <thead>{cabecera}</thead>
      <tbody>{filas}</tbody>
    </table>
  </div>
  <div class="matrix-legend">
    {"".join(f'<span class="leg-item"><span class="leg-dot" style="background:{_COLOR[v]}"></span>{_ICO[v]} {_LABEL[v]}</span>' for v in _ORDEN_VEREDICTOS)}
  </div>
</div>"""


# ─────────────────────────────────────────────────────────────────────────────
# Función principal
# ─────────────────────────────────────────────────────────────────────────────

def generar_informe_batch(resultados_batch: dict, ruta_salida: str) -> None:
    """
    Genera el informe HTML combinado de la auditoría batch.

    Args:
        resultados_batch: {url: {R1: {veredicto, detalle}, ...}, ...}
        ruta_salida:      Ruta del fichero HTML a generar.
    """
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
    n_sitios = len(resultados_batch)
    sitios_str = ", ".join(_dominio(u) for u in resultados_batch)

    tarjetas  = _tarjetas_resumen(resultados_batch)
    donut     = _grafico_donut(resultados_batch)
    barras_req  = _barras_por_requisito(resultados_batch)
    barras_sit  = _barras_por_sitio(resultados_batch)
    matriz    = _matriz(resultados_batch)

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Informe Batch — Auditoría de Privacidad Web</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
       background:#f6f8fa;color:#24292f;font-size:14px;line-height:1.5}}
  header{{background:#0969da;color:#fff;padding:28px 32px}}
  header h1{{font-size:22px;font-weight:700;margin-bottom:4px}}
  header p{{opacity:.85;font-size:13px}}
  .container{{max-width:1100px;margin:0 auto;padding:24px 20px}}
  h2{{font-size:18px;font-weight:700;margin:32px 0 16px;color:#0969da;
      border-bottom:2px solid #0969da;padding-bottom:6px}}
  h3{{font-size:15px;font-weight:600;margin-bottom:14px;color:#24292f}}
  .nota{{font-size:12px;color:#57606a;margin-top:10px}}

  /* Tarjetas */
  .cards{{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:8px}}
  .card{{flex:1;min-width:140px;border-radius:8px;padding:14px 18px;
         border:1px solid rgba(0,0,0,.08)}}
  .card-val{{font-size:22px;font-weight:700}}
  .card-lbl{{font-size:12px;margin-top:2px;opacity:.85}}

  /* Donut */
  .chart-wrap{{background:#fff;border:1px solid #d0d7de;border-radius:8px;
               padding:22px 24px;margin-bottom:20px}}
  .donut-row{{display:flex;align-items:center;gap:24px;flex-wrap:wrap}}

  /* Leyenda */
  .leyenda{{display:flex;flex-wrap:wrap;gap:10px}}
  .leyenda.mb{{margin-bottom:14px}}
  .leg-item{{display:flex;align-items:center;gap:5px;font-size:12px}}
  .leg-dot{{width:12px;height:12px;border-radius:50%;flex-shrink:0}}

  /* Barras */
  .bar-chart{{display:flex;flex-direction:column;gap:7px}}
  .bar-row{{display:flex;align-items:center;gap:10px}}
  .bar-label{{width:220px;font-size:12px;white-space:nowrap;overflow:hidden;
              text-overflow:ellipsis;flex-shrink:0}}
  .site-label{{width:180px}}
  .req-tag{{display:inline-block;background:#0969da;color:#fff;
            border-radius:4px;padding:0 5px;font-size:11px;
            font-weight:700;margin-right:6px}}
  .bar-track{{flex:1;height:20px;display:flex;border-radius:4px;
              overflow:hidden;background:#f6f8fa;min-width:0}}
  .bar-track div{{min-width:0;transition:flex .3s}}
  .bar-pct{{width:38px;text-align:right;font-size:12px;
            font-weight:600;color:#57606a;flex-shrink:0}}

  /* Matriz */
  .matrix-wrap{{overflow-x:auto}}
  .matrix{{border-collapse:collapse;font-size:12px;width:100%}}
  .matrix th,.matrix td{{border:1px solid #d0d7de;padding:5px 6px;text-align:center}}
  .matrix th{{background:#f6f8fa;font-weight:600;position:sticky;top:0}}
  .matrix td:first-child,.matrix th:first-child{{text-align:left;position:sticky;
    left:0;background:#fff;z-index:1;white-space:nowrap}}
  .site-cell{{max-width:160px;overflow:hidden;text-overflow:ellipsis}}
  .matrix-legend{{display:flex;flex-wrap:wrap;gap:10px;margin-top:12px}}
</style>
</head>
<body>
<header>
  <h1>Informe Batch — Auditoría de Privacidad Web</h1>
  <p>{n_sitios} sitios auditados · {fecha} · {sitios_str}</p>
</header>
<div class="container">

  <h2>Resumen general</h2>
  {tarjetas}
  {donut}

  <h2>Análisis por requisito</h2>
  {barras_req}

  <h2>Análisis por sitio</h2>
  {barras_sit}

  <h2>Matriz completa</h2>
  {matriz}

</div>
</body>
</html>"""

    Path(ruta_salida).parent.mkdir(parents=True, exist_ok=True)
    Path(ruta_salida).write_text(html, encoding="utf-8")
    print(f"[✓] Informe batch guardado en {ruta_salida}")
