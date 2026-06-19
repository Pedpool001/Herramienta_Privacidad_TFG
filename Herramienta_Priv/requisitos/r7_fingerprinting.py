"""
R7 - Protección contra Fingerprinting (RGPD Art. 5 / AEPD)
Detecta si scripts de terceros recogen señales del navegador/dispositivo
para construir una huella digital del usuario sin su consentimiento.

Fuente: base de datos SQLite de OpenWPM (tabla javascript).

Sistema de puntuación por script (solo scripts de terceros):
  ── Capa 1: peso por símbolo individual ──────────────────────────────
  PESO 3:
    CanvasRenderingContext2D.measureText  → font fingerprint
    window.navigator.plugins              → enumeración de plugins
    window.navigator.mimeTypes            → enumeración MIME
    window.navigator.oscpu                → OS fingerprint específico
  PESO 2:
    window.navigator.buildID              → build del navegador
    window.navigator.hardwareConcurrency  → núcleos CPU
    window.navigator.platform             → plataforma
    window.screen.colorDepth              → profundidad de color
    window.screen.pixelDepth              → profundidad de pixel
  PESO 1:
    HTMLCanvasElement.getContext          → inicio de Canvas
    window.navigator.vendor/vendorSub/productSub
    window.navigator.appVersion/appName/appCodeName
    window.navigator.maxTouchPoints
    window.navigator.languages
    window.navigator.userAgent

  ── Capa 2: bonus por combinación ────────────────────────────────────
  +3  getContext + measureText en el mismo script (font fingerprint confirmado)
  +2  ≥ 4 símbolos navigator distintos en el mismo script (batería completa)

  ── Umbrales ─────────────────────────────────────────────────────────
  Puntuación ≥ 7  → FAILED   (fingerprinting confirmado)
  Puntuación ≥ 3  → WARNING  (indicios claros)
  Sin scripts ≥ 3 → PASSED

Uso:
  python3 r7_fingerprinting.py                              # BD por defecto
  python3 r7_fingerprinting.py /ruta/crawl-data.sqlite      # BD explícita
  python3 r7_fingerprinting.py --no-detalle                 # solo resumen
"""

import sqlite3
import sys
import json
from pathlib import Path
from urllib.parse import urlparse
from collections import defaultdict

# ── Rutas ─────────────────────────────────────────────────────────────────────
RESULTADO_PATH = Path(__file__).resolve().parents[1] / "analysis_data/r7_resultado.json"

# ── Sistema de pesos ──────────────────────────────────────────────────────────
PESOS = {
    # Peso 3 — técnicas de alto valor diagnóstico
    "CanvasRenderingContext2D.measureText":  3,
    "window.navigator.plugins":              3,
    "window.navigator.mimeTypes":            3,
    "window.navigator.oscpu":                3,
    # Peso 2 — señales hardware/sistema
    "window.navigator.buildID":              2,
    "window.navigator.hardwareConcurrency":  2,
    "window.navigator.platform":             2,
    "window.screen.colorDepth":              2,
    "window.screen.pixelDepth":              2,
    # Peso 1 — señales de apoyo
    "HTMLCanvasElement.getContext":          1,
    "window.navigator.vendor":               1,
    "window.navigator.vendorSub":            1,
    "window.navigator.productSub":           1,
    "window.navigator.appVersion":           1,
    "window.navigator.appName":              1,
    "window.navigator.appCodeName":          1,
    "window.navigator.maxTouchPoints":       1,
    "window.navigator.languages":            1,
    "window.navigator.userAgent":            1,
}

# ── Bonus por combinación ─────────────────────────────────────────────────────
BONUS_CANVAS_FONT = 3    # getContext + measureText en el mismo script
BONUS_BATERIA_NAV = 2    # ≥ 4 símbolos navigator distintos
MIN_NAV_PARA_BONUS = 4

# Prefijo común para contar símbolos navigator
PREFIJO_NAVIGATOR = "window.navigator."

# ── Umbrales ──────────────────────────────────────────────────────────────────
UMBRAL_FAILED  = 7
UMBRAL_WARNING = 3

# ── Clasificación por técnica ─────────────────────────────────────────────────
TECNICAS = {
    "Canvas Font Fingerprint": {
        "CanvasRenderingContext2D.measureText",
        "CanvasRenderingContext2D.font",
        "HTMLCanvasElement.getContext",
    },
    "Navigator Fingerprint": {
        "window.navigator.plugins", "window.navigator.mimeTypes",
        "window.navigator.oscpu", "window.navigator.buildID",
        "window.navigator.hardwareConcurrency", "window.navigator.platform",
        "window.navigator.vendor", "window.navigator.vendorSub",
        "window.navigator.productSub", "window.navigator.appVersion",
        "window.navigator.appName", "window.navigator.appCodeName",
        "window.navigator.maxTouchPoints", "window.navigator.languages",
        "window.navigator.userAgent",
    },
    "Screen Fingerprint": {
        "window.screen.colorDepth",
        "window.screen.pixelDepth",
    },
}

ICONO = {"PASSED": "✅", "WARNING": "⚠️ ", "FAILED": "❌"}


# ── Helpers ───────────────────────────────────────────────────────────────────
def dominio_base(url: str) -> str:
    """Extrae dominio registrable: 'sub.ejemplo.com' → 'ejemplo.com'."""
    try:
        host = urlparse(url).hostname or ""
        parts = host.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host
    except Exception:
        return ""


def es_tercero(script_url: str, site_url: str) -> bool:
    return dominio_base(script_url) != dominio_base(site_url)


def tecnicas_detectadas(simbolos: set) -> list[str]:
    """Devuelve las técnicas de fingerprinting presentes en un conjunto de símbolos."""
    return [
        nombre
        for nombre, grupo in TECNICAS.items()
        if simbolos & grupo
    ]


# ── Carga de datos ────────────────────────────────────────────────────────────
def cargar_datos(db_path: Path) -> list[dict]:
    """
    Lee la tabla javascript agrupando por (script_url, top_level_url).
    Devuelve una lista de dicts con todos los símbolos únicos por script.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT
            script_url,
            top_level_url,
            GROUP_CONCAT(DISTINCT symbol) AS simbolos_raw
        FROM javascript
        WHERE script_url IS NOT NULL AND script_url != ''
        GROUP BY script_url, top_level_url
    """)
    filas = cur.fetchall()
    conn.close()

    resultado = []
    for fila in filas:
        simbolos = set((fila["simbolos_raw"] or "").split(","))
        resultado.append({
            "script_url":    fila["script_url"],
            "top_level_url": fila["top_level_url"],
            "simbolos":      simbolos,
        })
    return resultado


# ── Evaluación de cada script ─────────────────────────────────────────────────
def evaluar_script(script: dict) -> dict:
    simbolos   = script["simbolos"]
    script_url = script["script_url"]
    site_url   = script["top_level_url"] or ""

    # Solo evaluar terceros
    if not es_tercero(script_url, site_url):
        return None

    # ── Capa 1: puntuación base por símbolo ───────────────────────────────────
    puntuacion   = 0
    simbolos_hit = {}   # {symbol: peso}
    for sym in simbolos:
        peso = PESOS.get(sym, 0)
        if peso:
            puntuacion += peso
            simbolos_hit[sym] = peso

    if puntuacion == 0:
        return None     # sin señales, no interesa

    # ── Capa 2: bonus por combinación ────────────────────────────────────────
    bonuses = []

    canvas_font_combo = (
        "HTMLCanvasElement.getContext" in simbolos
        and "CanvasRenderingContext2D.measureText" in simbolos
    )
    if canvas_font_combo:
        puntuacion += BONUS_CANVAS_FONT
        bonuses.append(f"+{BONUS_CANVAS_FONT} Canvas font fingerprint (getContext+measureText)")

    nav_syms = [s for s in simbolos if s.startswith(PREFIJO_NAVIGATOR) and s in PESOS]
    if len(nav_syms) >= MIN_NAV_PARA_BONUS:
        puntuacion += BONUS_BATERIA_NAV
        bonuses.append(f"+{BONUS_BATERIA_NAV} Batería de navigator ({len(nav_syms)} señales distintas)")

    # ── Veredicto por script ──────────────────────────────────────────────────
    if puntuacion >= UMBRAL_FAILED:
        estado = "FAILED"
    elif puntuacion >= UMBRAL_WARNING:
        estado = "WARNING"
    else:
        estado = None   # por debajo del umbral mínimo, descartar

    if estado is None:
        return None

    return {
        "script_url":   script_url,
        "dominio":      dominio_base(script_url),
        "site_url":     site_url,
        "puntuacion":   puntuacion,
        "estado":       estado,
        "simbolos_hit": simbolos_hit,
        "bonuses":      bonuses,
        "tecnicas":     tecnicas_detectadas(simbolos),
    }


# ── Veredicto global del sitio ────────────────────────────────────────────────
def veredicto_global(evaluaciones: list[dict]) -> str:
    estados = [e["estado"] for e in evaluaciones]
    if "FAILED" in estados:
        return "FAILED"
    if "WARNING" in estados:
        return "WARNING"
    return "PASSED"


# ── Salida por consola ────────────────────────────────────────────────────────
def imprimir(resultado: dict, detalle: bool) -> None:
    sitio     = resultado["sitio"]
    veredicto = resultado["veredicto"]
    scripts   = resultado["scripts_detectados"]
    fallidos  = [s for s in scripts if s["estado"] == "FAILED"]
    avisos    = [s for s in scripts if s["estado"] == "WARNING"]

    print(f"\n{'='*64}")
    print(f"  R7 — Protección contra Fingerprinting")
    print(f"  Sitio: {sitio}")
    print(f"  Veredicto: {ICONO[veredicto]} {veredicto}")
    print(f"{'='*64}")
    print(f"\nScripts de 3ª parte analizados : {resultado['total_scripts_terceros']}")
    print(f"  ❌ FAILED  (≥{UMBRAL_FAILED} pts) : {len(fallidos)}")
    print(f"  ⚠️  WARNING (≥{UMBRAL_WARNING} pts) : {len(avisos)}")

    if detalle and scripts:
        print(f"\n{'─'*64}")
        print("Scripts detectados (mayor puntuación primero):\n")
        for s in scripts:
            icono = ICONO[s["estado"]]
            print(f"  {icono} [{s['puntuacion']:2d} pts] {s['dominio']}")
            print(f"       {s['script_url'][:80]}")
            print(f"       Técnicas : {', '.join(s['tecnicas']) or '—'}")

            # Símbolos agrupados por peso
            por_peso = defaultdict(list)
            for sym, peso in s["simbolos_hit"].items():
                por_peso[peso].append(sym)
            for peso in sorted(por_peso, reverse=True):
                syms = ", ".join(por_peso[peso])
                print(f"       Peso {peso}    : {syms}")

            if s["bonuses"]:
                for b in s["bonuses"]:
                    print(f"       Bonus    : {b}")
            print()

    print()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    args    = sys.argv[1:]
    detalle = "--no-detalle" not in args
    args    = [a for a in args if a != "--no-detalle"]

    if not args:
        print("[ERROR] Uso: r7_fingerprinting.py <crawl-data.sqlite> [--no-detalle]", file=sys.stderr)
        sys.exit(1)
    db_path = Path(args[0])

    if not db_path.exists():
        print(f"[ERROR] No se encontró la BD: {db_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[*] Leyendo BD: {db_path.name}")
    scripts_raw = cargar_datos(db_path)
    print(f"[*] Scripts únicos en BD: {len(scripts_raw)}")

    # Filtrar solo terceros para el conteo
    terceros = [s for s in scripts_raw if es_tercero(s["script_url"], s["top_level_url"] or "")]
    print(f"[*] Scripts de 3ª parte: {len(terceros)}")

    # Evaluar y deduplicar por script_url (mismo script puede aparecer
    # en varias páginas del mismo sitio; nos quedamos con la puntuación más alta)
    evaluaciones_raw = [evaluar_script(s) for s in scripts_raw]
    evaluaciones_raw = [e for e in evaluaciones_raw if e is not None]

    dedup: dict[str, dict] = {}
    for e in evaluaciones_raw:
        url = e["script_url"]
        if url not in dedup or e["puntuacion"] > dedup[url]["puntuacion"]:
            dedup[url] = e
    evaluaciones = sorted(dedup.values(), key=lambda e: e["puntuacion"], reverse=True)

    # Inferir sitio desde top_level_url más frecuente
    sitio = "desconocido"
    if scripts_raw:
        from collections import Counter
        conteo = Counter(dominio_base(s["top_level_url"] or "") for s in scripts_raw)
        sitio  = conteo.most_common(1)[0][0]

    veredicto = veredicto_global(evaluaciones) if evaluaciones else "PASSED"

    resultado = {
        "sitio":                  sitio,
        "veredicto":              veredicto,
        "total_scripts_terceros": len(terceros),
        "scripts_detectados":     evaluaciones,
        "resumen": {
            "FAILED":  sum(1 for e in evaluaciones if e["estado"] == "FAILED"),
            "WARNING": sum(1 for e in evaluaciones if e["estado"] == "WARNING"),
        },
    }

    RESULTADO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f"[✓] Resultado guardado en {RESULTADO_PATH}")

    imprimir(resultado, detalle)


if __name__ == "__main__":
    main()
