"""
Generador del informe HTML de auditoría de privacidad.

Uso (desde main.py):
    from salida import generar_informe_unico
    generar_informe_unico(url_sitio, resultados, ruta_salida)
"""

import json
from datetime import datetime
from pathlib import Path

# ── Metadatos de cada requisito ───────────────────────────────────────────────

REQUISITOS = {
    "R1":  ("Información por capas",                "RGPD Art. 12 / AEPD"),
    "R2":  ("Bloqueo de cookies no exentas",        "RGPD Art. 7 / LSSI Art. 22.2"),
    "R3":  ("Ausencia de web beacons en PRE",       "RGPD Art. 5.1.a / Directiva ePrivacy"),
    "R4":  ("Granularidad en la elección",          "RGPD Art. 7 / Considerando 43"),
    "R5":  ("Revocabilidad sencilla",               "RGPD Art. 7.3 / AEPD"),
    "R6":  ("Anti-keylogging",                      "RGPD Art. 5.1.f / LSSI Art. 12"),
    "R7":  ("Protección contra fingerprinting",     "RGPD Art. 5 / AEPD"),
    "R8":  ("Storage de terceros",                  "RGPD Art. 5.1.f / Considerando 49"),
    "R9":  ("Minimización de datos",                "RGPD Art. 5.1.c"),
    "R10": ("Limitación del plazo de persistencia", "RGPD Art. 5.1.e"),
    "R11": ("Desvinculación y aislamiento",         "RGPD Art. 5.1.f / Considerando 26"),
    "R12": ("Software de terceros desactivado",     "RGPD / Privacy by Design"),
    "R13": ("Ausencia de dark patterns",            "RGPD Art. 7 / EDPB 3/2022"),
    "R14": ("Lenguaje claro y sencillo",            "RGPD Art. 12 / AEPD"),
    "R15": ("Identificación de responsables",       "RGPD Art. 13/14"),
    "R16": ("Correspondencia aviso-ejecución",      "RGPD Art. 13/14 / Art. 5.1.a"),
    "R17": ("Redirección HTTPS",                    "RGPD Art. 32"),
    "R18": ("Content Security Policy",              "RGPD Art. 32"),
    "R19": ("Designación y contacto del DPO",       "RGPD Art. 37-39"),
}

# ── Colores por veredicto ─────────────────────────────────────────────────────

COLORES = {
    "PASSED":       ("#1a7f37", "#dafbe1", "✅"),
    "WARNING":      ("#9a6700", "#fff8c5", "⚠️"),
    "FAILED":       ("#cf222e", "#ffebe9", "❌"),
    "ERROR":        ("#57606a", "#f6f8fa", "⚙️"),
    "NO_EVALUABLE": ("#57606a", "#f6f8fa", "—"),
}


def _color(veredicto: str) -> tuple:
    return COLORES.get(veredicto, COLORES["ERROR"])


def _resumen(resultados: dict) -> dict:
    conteo = {k: 0 for k in COLORES}
    for v in resultados.values():
        veredicto = v.get("veredicto", "ERROR")
        conteo[veredicto] = conteo.get(veredicto, 0) + 1
    return conteo


def _detalle_html(detalle) -> str:
    """Renderiza el campo detalle como JSON coloreado dentro de un <details>."""
    if not detalle:
        return ""
    texto = json.dumps(detalle, ensure_ascii=False, indent=2)
    # Escapar HTML básico
    texto = texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""
        <details>
            <summary>Ver detalle</summary>
            <pre class="detalle-json">{texto}</pre>
        </details>"""


def _fila(req: str, datos: dict) -> str:
    nombre, normativa = REQUISITOS.get(req, (req, ""))
    veredicto = datos.get("veredicto", "ERROR")
    color_texto, color_fondo, icono = _color(veredicto)
    detalle = datos.get("detalle", "")

    return f"""
    <tr>
        <td class="req-id">{req}</td>
        <td>
            <span class="req-nombre">{nombre}</span>
            <span class="req-norm">{normativa}</span>
        </td>
        <td>
            <span class="badge"
                  style="color:{color_texto};background:{color_fondo};border-color:{color_texto}">
                {icono}&nbsp;{veredicto}
            </span>
        </td>
        <td>{_detalle_html(detalle)}</td>
    </tr>"""


def generar_informe_unico(url_sitio: str, resultados: dict, ruta_salida: str) -> None:
    """
    Genera un informe HTML completo con los resultados de la auditoría.

    Args:
        url_sitio:   URL del sitio auditado.
        resultados:  Dict {R1: {veredicto, detalle}, ...}
        ruta_salida: Ruta del fichero HTML a generar.
    """
    fecha    = datetime.now().strftime("%d/%m/%Y %H:%M")
    resumen  = _resumen(resultados)
    filas    = "".join(_fila(req, resultados.get(req, {"veredicto": "ERROR"}))
                       for req in sorted(REQUISITOS))
    total    = len(REQUISITOS)

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Auditoría de Privacidad — {url_sitio}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    font-size: 14px;
    color: #1f2328;
    background: #f6f8fa;
    padding: 32px 24px;
  }}

  /* ── Cabecera ── */
  .header {{
    background: #fff;
    border: 1px solid #d0d7de;
    border-radius: 8px;
    padding: 24px 28px;
    margin-bottom: 24px;
  }}
  .header h1 {{
    font-size: 20px;
    font-weight: 600;
    margin-bottom: 6px;
  }}
  .header .meta {{
    color: #57606a;
    font-size: 13px;
  }}
  .header .url {{
    font-family: monospace;
    font-size: 14px;
    color: #0969da;
    word-break: break-all;
  }}

  /* ── Tarjetas de resumen ── */
  .resumen {{
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 24px;
  }}
  .card {{
    background: #fff;
    border: 1px solid #d0d7de;
    border-radius: 8px;
    padding: 16px 20px;
    min-width: 110px;
    text-align: center;
  }}
  .card .num {{
    font-size: 28px;
    font-weight: 700;
    line-height: 1.1;
  }}
  .card .label {{
    font-size: 12px;
    color: #57606a;
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}
  .card.passed  .num {{ color: #1a7f37; }}
  .card.warning .num {{ color: #9a6700; }}
  .card.failed  .num {{ color: #cf222e; }}
  .card.other   .num {{ color: #57606a; }}

  /* ── Tabla de resultados ── */
  .tabla-wrap {{
    background: #fff;
    border: 1px solid #d0d7de;
    border-radius: 8px;
    overflow: hidden;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
  }}
  thead tr {{
    background: #f6f8fa;
  }}
  th {{
    padding: 10px 16px;
    text-align: left;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: #57606a;
    border-bottom: 1px solid #d0d7de;
  }}
  td {{
    padding: 12px 16px;
    border-bottom: 1px solid #eaeef2;
    vertical-align: top;
  }}
  tr:last-child td {{
    border-bottom: none;
  }}
  tr:hover td {{
    background: #f6f8fa;
  }}

  .req-id {{
    font-family: monospace;
    font-weight: 700;
    font-size: 13px;
    color: #57606a;
    white-space: nowrap;
    width: 48px;
  }}
  .req-nombre {{
    display: block;
    font-weight: 500;
  }}
  .req-norm {{
    display: block;
    font-size: 12px;
    color: #57606a;
    margin-top: 2px;
  }}

  .badge {{
    display: inline-block;
    padding: 3px 10px;
    border-radius: 20px;
    border: 1px solid;
    font-size: 12px;
    font-weight: 600;
    white-space: nowrap;
  }}

  /* ── Detalle colapsable ── */
  details summary {{
    cursor: pointer;
    font-size: 12px;
    color: #0969da;
    user-select: none;
    padding: 2px 0;
  }}
  details summary:hover {{
    text-decoration: underline;
  }}
  .detalle-json {{
    margin-top: 8px;
    padding: 10px;
    background: #f6f8fa;
    border: 1px solid #d0d7de;
    border-radius: 6px;
    font-size: 11px;
    font-family: monospace;
    white-space: pre-wrap;
    word-break: break-all;
    max-height: 300px;
    overflow-y: auto;
    color: #1f2328;
  }}
</style>
</head>
<body>

<div class="header">
  <h1>Auditoría de Privacidad Web</h1>
  <p class="meta">Generado el {fecha}</p>
  <p class="url" style="margin-top:8px">{url_sitio}</p>
</div>

<div class="resumen">
  <div class="card">
    <div class="num">{total}</div>
    <div class="label">Total</div>
  </div>
  <div class="card passed">
    <div class="num">{resumen.get("PASSED", 0)}</div>
    <div class="label">✅ Cumple</div>
  </div>
  <div class="card warning">
    <div class="num">{resumen.get("WARNING", 0)}</div>
    <div class="label">⚠️ Aviso</div>
  </div>
  <div class="card failed">
    <div class="num">{resumen.get("FAILED", 0)}</div>
    <div class="label">❌ Falla</div>
  </div>
  <div class="card other">
    <div class="num">{resumen.get("ERROR", 0) + resumen.get("NO_EVALUABLE", 0)}</div>
    <div class="label">— Sin dato</div>
  </div>
</div>

<div class="tabla-wrap">
  <table>
    <thead>
      <tr>
        <th>Req.</th>
        <th>Descripción</th>
        <th>Veredicto</th>
        <th>Detalle</th>
      </tr>
    </thead>
    <tbody>
      {filas}
    </tbody>
  </table>
</div>

</body>
</html>"""

    ruta = Path(ruta_salida)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(html, encoding="utf-8")
    print(f"[✓] Informe guardado en {ruta}")
