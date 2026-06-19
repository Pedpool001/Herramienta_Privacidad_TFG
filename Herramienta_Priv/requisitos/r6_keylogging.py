"""
R6 - Confidencialidad de las comunicaciones / Anti-Keylogging
(RGPD Art. 5.1.f / LSSI Art. 12)

Verifica que el software de terceros del sitio no monitorice pulsaciones de
teclado ni movimientos de ratón del usuario sin base legal.

Fuente de datos: inspection.json generado por Blacklight.

Lógica de clasificación (solo scripts de terceros):
  ── Nivel FAILED ────────────────────────────────────────────────────────────
  · key_logging no vacío       → keylogger explícito detectado por Blacklight
  · session_recorders no vacío → grabador de sesión (FullStory, Hotjar…)
  · Script de 3ª parte con listener KEYBOARD (keydown/keyup/keypress)

  ── Nivel WARNING ────────────────────────────────────────────────────────────
  · Script de 3ª parte con listener MOUSE de movimiento
    (mousemove, mousedown, mouseup)
  · Script de 3ª parte con listener TOUCH de movimiento
    (touchmove, touchstart, touchend)

  ── Nivel INFO (anotado, no cambia veredicto) ─────────────────────────────
  · Script de 3ª parte con solo scroll o click (analítica básica)

  ── Veredicto global ──────────────────────────────────────────────────────
  FAILED  si existe al menos una condición de nivel FAILED
  WARNING si existe al menos una condición WARNING (y ninguna FAILED)
  PASSED  si no se detecta monitorización de terceros relevante

Uso:
  python3 r6_keylogging.py                          # JSON por defecto
  python3 r6_keylogging.py /ruta/inspection.json    # fichero directo
  python3 r6_keylogging.py /ruta/directorio/        # directorio con el JSON
  python3 r6_keylogging.py --no-detalle             # solo resumen
"""

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

# ── Rutas por defecto ─────────────────────────────────────────────────────────
_TFG_DIR = Path(__file__).resolve().parents[2]
INSPECTION_DEFAULT = _TFG_DIR / "BL/blacklight-collector/demo-dir/inspection.json"
RESULTADO_PATH = Path(__file__).resolve().parents[1] / "analysis_data/r6_resultado.json"

# ── Clasificación de eventos ──────────────────────────────────────────────────
EVENTOS_TECLADO  = {"keydown", "keyup", "keypress"}
EVENTOS_RATON_MV = {"mousemove", "mousedown", "mouseup"}
EVENTOS_RATON_OK = {"click", "scroll"}
EVENTOS_TOUCH    = {"touchmove", "touchstart", "touchend"}

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


def es_tercero(script_url: str, site_domain: str) -> bool:
    return dominio_base(script_url) != site_domain


def resolver_json(ruta_arg: str) -> Path:
    p = Path(ruta_arg)
    if p.is_dir():
        candidate = p / "inspection.json"
        if not candidate.exists():
            print(f"[ERROR] No se encontró inspection.json en {p}", file=sys.stderr)
            sys.exit(1)
        return candidate
    return p


# ── Carga y análisis ──────────────────────────────────────────────────────────
def analizar(data: dict) -> dict:
    """
    Analiza el inspection.json y devuelve el resultado estructurado de R6.
    """
    host     = data.get("host", "")
    site_dom = dominio_base(data.get("uri_ins", "") or f"http://{host}")
    if not site_dom:
        site_dom = host

    reports = data.get("reports", {})

    fallos   = []   # lista de dicts con nivel FAILED
    avisos   = []   # lista de dicts con nivel WARNING
    notas    = []   # nivel INFO (scroll/click de terceros)

    # ── key_logging ───────────────────────────────────────────────────────────
    key_logging = reports.get("key_logging", {})
    for dominio_kl, evidencias in key_logging.items():
        fallos.append({
            "tipo":      "KEY_LOGGING_EXPLÍCITO",
            "script":    dominio_kl,
            "eventos":   [],
            "evidencia": evidencias,
            "motivo":    "Keylogger detectado directamente por Blacklight",
        })

    # ── session_recorders ─────────────────────────────────────────────────────
    session_rec = reports.get("session_recorders", {})
    for script_sr, urls_rel in session_rec.items():
        fallos.append({
            "tipo":      "SESSION_RECORDER",
            "script":    script_sr,
            "eventos":   [],
            "evidencia": urls_rel,
            "motivo":    "Grabador de sesión activo (captura pulsaciones y movimientos)",
        })

    # ── behaviour_event_listeners ─────────────────────────────────────────────
    bel = reports.get("behaviour_event_listeners", {})

    for categoria, scripts in bel.items():
        for script_url, eventos in scripts.items():
            if not es_tercero(script_url, site_dom):
                continue  # primer nivel: ignorar scripts del propio sitio

            eventos_set = set(e.lower() for e in eventos)
            dom_script  = dominio_base(script_url)

            # Teclado → FAILED
            eventos_teclado = eventos_set & EVENTOS_TECLADO
            if eventos_teclado:
                fallos.append({
                    "tipo":    "KEYBOARD_LISTENER",
                    "script":  script_url,
                    "dominio": dom_script,
                    "eventos": sorted(eventos_teclado),
                    "motivo":  "Monitorización de pulsaciones de teclado por tercero",
                })

            # Movimiento ratón/touch → WARNING
            eventos_mv = eventos_set & (EVENTOS_RATON_MV | EVENTOS_TOUCH)
            if eventos_mv:
                avisos.append({
                    "tipo":    "MOUSE_TRACKING",
                    "script":  script_url,
                    "dominio": dom_script,
                    "eventos": sorted(eventos_mv),
                    "motivo":  "Monitorización de movimientos de ratón/toque por tercero",
                })

            # Solo scroll/click → INFO
            eventos_ok = eventos_set & EVENTOS_RATON_OK
            solo_ok    = eventos_ok and not eventos_teclado and not eventos_mv
            if solo_ok:
                notas.append({
                    "tipo":    "MOUSE_BASICO",
                    "script":  script_url,
                    "dominio": dom_script,
                    "eventos": sorted(eventos_ok),
                    "motivo":  "Eventos básicos de ratón (scroll/click) por tercero",
                })

    # ── Deduplicar por script ─────────────────────────────────────────────────
    fallos = _dedup(fallos)
    avisos = _dedup(avisos)
    notas  = _dedup(notas)

    # ── Veredicto global ──────────────────────────────────────────────────────
    if fallos:
        veredicto = "FAILED"
    elif avisos:
        veredicto = "WARNING"
    else:
        veredicto = "PASSED"

    return {
        "sitio":     host or site_dom,
        "veredicto": veredicto,
        "resumen": {
            "FAILED":  len(fallos),
            "WARNING": len(avisos),
            "INFO":    len(notas),
        },
        "fallos":  fallos,
        "avisos":  avisos,
        "notas":   notas,
    }


def _dedup(lista: list) -> list:
    """Elimina entradas duplicadas por script."""
    vistos = set()
    result = []
    for item in lista:
        key = (item.get("tipo", ""), item.get("script", ""))
        if key not in vistos:
            vistos.add(key)
            result.append(item)
    return result


# ── Salida por consola ────────────────────────────────────────────────────────
def imprimir(resultado: dict, detalle: bool) -> None:
    sitio     = resultado["sitio"]
    veredicto = resultado["veredicto"]
    resumen   = resultado["resumen"]
    fallos    = resultado["fallos"]
    avisos    = resultado["avisos"]
    notas     = resultado["notas"]

    print(f"\n{'='*64}")
    print(f"  R6 — Confidencialidad / Anti-Keylogging")
    print(f"  Sitio   : {sitio}")
    print(f"  Veredicto: {ICONO[veredicto]} {veredicto}")
    print(f"{'='*64}")
    print(f"\nDetecciones de terceros:")
    print(f"  ❌ FAILED  : {resumen['FAILED']}")
    print(f"  ⚠️  WARNING : {resumen['WARNING']}")
    print(f"  ℹ️  INFO    : {resumen['INFO']}")

    if not detalle:
        print()
        return

    if fallos:
        print(f"\n{'─'*64}")
        print("FAILED — Monitorización de teclado / grabación de sesión:\n")
        for f in fallos:
            print(f"  ❌ {f['tipo']}")
            print(f"     Script : {f['script'][:80]}")
            if f.get("eventos"):
                print(f"     Eventos: {', '.join(f['eventos'])}")
            print(f"     Motivo : {f['motivo']}")
            print()

    if avisos:
        print(f"{'─'*64}")
        print("WARNING — Seguimiento de movimientos de ratón:\n")
        for a in avisos:
            print(f"  ⚠️  {a['tipo']}")
            print(f"     Script : {a['script'][:80]}")
            print(f"     Eventos: {', '.join(a['eventos'])}")
            print(f"     Motivo : {a['motivo']}")
            print()

    if notas:
        print(f"{'─'*64}")
        print("INFO — Eventos básicos de ratón (scroll/click, sin riesgo directo):\n")
        for n in notas:
            print(f"  ℹ️  {n['dominio']}")
            print(f"     Eventos: {', '.join(n['eventos'])}")
            print()

    print()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    args    = sys.argv[1:]
    detalle = "--no-detalle" not in args
    args    = [a for a in args if a != "--no-detalle"]

    if args:
        json_path = resolver_json(args[0])
    else:
        json_path = INSPECTION_DEFAULT

    if not json_path.exists():
        print(f"[ERROR] No se encontró: {json_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[*] Leyendo: {json_path}")
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    resultado = analizar(data)

    RESULTADO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f"[✓] Resultado guardado en {RESULTADO_PATH}")

    imprimir(resultado, detalle)


if __name__ == "__main__":
    main()
