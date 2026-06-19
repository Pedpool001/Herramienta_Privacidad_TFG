"""
R11 - Desvinculación y Aislamiento (RGPD Art. 5.1.f / Considerando 26)
Detecta si terceros intercambian identificadores de usuario entre sí
(cookie syncing) para correlacionar perfiles procedentes de distintas fuentes,
vulnerando el principio de independencia de contextos de tratamiento.

Fuente: tabla http_requests de la BD SQLite de OpenWPM.

Método de detección:
  1. Una petición es un SYNC si su URL contiene patrones de intercambio
     de identificadores (uid=, dpuuid, /ibs:, /sync, getUID, etc.)
  2. Es tercero→tercero si triggering_origin (quien disparó la petición)
     es un dominio diferente al sitio auditado Y diferente al dominio
     receptor (url). Ambos son terceros que se intercambian datos.
  3. Se construye un grafo de sincronización: quién sincroniza con quién
     y cuántas veces, para visualizar la red de correlación.

Umbrales:
  ≥ 1 evento tercero→tercero confirmado → FAILED
  Solo syncs desde el propio sitio hacia terceros  → WARNING
  Sin syncs detectados                             → PASSED

Uso:
  python3 r11_desvinculacion.py                          # BD por defecto
  python3 r11_desvinculacion.py /ruta/crawl-data.sqlite  # BD explícita
  python3 r11_desvinculacion.py --no-detalle             # solo resumen
"""

import sqlite3
import sys
import json
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from collections import defaultdict

# ── Rutas ─────────────────────────────────────────────────────────────────────
RESULTADO_PATH = Path(__file__).resolve().parents[1] / "analysis_data/r11_resultado.json"

# ── Patrones de URL que identifican un evento de cookie sync ──────────────────
# Cada patrón corresponde a un mecanismo real de intercambio de identificadores:
#   /ibs:       → Adobe Audience Manager ID bridge service
#   getUID      → petición de UID a un DMP externo (Adswizz, etc.)
#   UCookieSetPug → endpoint de PubMatic para recibir un ID externo
#   /sync       → endpoint genérico de sincronización (Criteo, Rubicon, etc.)
#   user_sync   → variante de sync usada por varios SSPs
#   usync       → variante compacta (Index Exchange, Sharethrough)
#   dpuuid      → parámetro Adobe DMP con el UUID del usuario
#   uid=        → parámetro genérico de ID de usuario
#   guid=       → variant de uid
#   /match      → Google cookie matching endpoint
#   cm/pixel    → DoubleClick cookie match pixel
SYNC_URL_PATTERNS = [
    r"/ibs:",
    r"getUID",
    r"UCookieSetPug",
    r"[?&/]sync[?&/\.]",
    r"user[_-]sync",
    r"usync",
    r"[?&]dpuuid=",
    r"[?&]uid=",
    r"[?&]guid=",
    r"/match[?&]",
    r"cm\.g\.doubleclick\.net/pixel",
    r"syncframe",
    r"cookie[_-]sync",
    r"/rd\?",            # redirect de sync (patrón Demdex)
]

SYNC_REGEX = re.compile("|".join(SYNC_URL_PATTERNS), re.IGNORECASE)

ICONO = {"PASSED": "✅", "WARNING": "⚠️ ", "FAILED": "❌"}


# ── Helpers de dominio ────────────────────────────────────────────────────────
# Igual que en otros scripts: extraemos solo las dos últimas partes del host
# para comparar dominios registrables (evitar que sub.a.com ≠ a.com).
def dominio_base(url: str) -> str:
    if not url or url in ("undefined", "null", ""):
        return ""
    try:
        host = urlparse(url).hostname or ""
        parts = host.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host
    except Exception:
        return ""


def es_sync(url: str) -> bool:
    """Devuelve True si la URL contiene algún patrón de cookie sync."""
    return bool(SYNC_REGEX.search(url))


# ── Carga y análisis de peticiones ────────────────────────────────────────────
# La consulta trae todas las peticiones HTTP que tienen triggering_origin
# definido (no vacío ni 'undefined'). Este campo nos dice qué dominio
# disparó la petición — si es un tercero diferente al sitio y al receptor,
# estamos ante una comunicación tercero→tercero.
def cargar_peticiones(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT
            url,
            top_level_url,
            triggering_origin,
            loading_origin,
            referrer,
            resource_type
        FROM http_requests
        WHERE triggering_origin IS NOT NULL
          AND triggering_origin NOT IN ('undefined', '', 'null')
        ORDER BY url
    """)
    filas = [dict(r) for r in cur.fetchall()]
    conn.close()
    return filas


def analizar_peticiones(peticiones: list[dict]) -> dict:
    """
    Clasifica cada petición en tres categorías:
      - sync_3p_to_3p : tercero dispara sync hacia otro tercero  → FAILED
      - sync_1p_to_3p : el propio sitio dispara sync hacia tercero → WARNING
      - ignoradas     : sin patrón de sync o mismos dominios
    """
    sitio = ""
    # Inferir el dominio del sitio auditado desde top_level_url
    for p in peticiones:
        d = dominio_base(p["top_level_url"] or "")
        if d:
            sitio = d
            break

    syncs_3p_3p = []   # tercero → tercero
    syncs_1p_3p = []   # sitio   → tercero

    for p in peticiones:
        url        = p["url"] or ""
        trigger    = p["triggering_origin"] or ""
        top        = p["top_level_url"] or ""

        dom_url     = dominio_base(url)
        dom_trigger = dominio_base(trigger)
        dom_top     = dominio_base(top)

        # Descartar si no tiene patrón de sync
        if not es_sync(url):
            continue

        # Descartar si origen y destino son el mismo dominio
        if dom_trigger == dom_url:
            continue

        evento = {
            "trigger_dominio": dom_trigger,
            "trigger_origin":  trigger,
            "receptor_dominio": dom_url,
            "receptor_url":    url[:120],
            "sitio":           dom_top,
        }

        if dom_trigger == dom_top or dom_trigger == sitio:
            # El propio sitio inicia el sync → menos grave pero relevante
            syncs_1p_3p.append(evento)
        else:
            # Un tercero inicia el sync hacia otro tercero → violación directa
            syncs_3p_3p.append(evento)

    return {
        "sitio":        sitio,
        "syncs_3p_3p":  syncs_3p_3p,
        "syncs_1p_3p":  syncs_1p_3p,
    }


# ── Grafo de sincronización ───────────────────────────────────────────────────
# Construimos un resumen de qué parejas (A→B) se han visto y cuántas veces.
# Esto visualiza la red de correlación: si demdex.net → pubmatic.com aparece
# 5 veces, hay 5 intercambios de identificadores entre ambas plataformas.
def construir_grafo(syncs: list[dict]) -> list[dict]:
    conteo: dict[tuple, int] = defaultdict(int)
    for s in syncs:
        par = (s["trigger_dominio"], s["receptor_dominio"])
        conteo[par] += 1
    return [
        {"origen": k[0], "destino": k[1], "eventos": v}
        for k, v in sorted(conteo.items(), key=lambda x: -x[1])
    ]


# ── Veredicto global ──────────────────────────────────────────────────────────
def veredicto_global(analisis: dict) -> str:
    if analisis["syncs_3p_3p"]:
        return "FAILED"
    if analisis["syncs_1p_3p"]:
        return "WARNING"
    return "PASSED"


# ── Salida por consola ────────────────────────────────────────────────────────
def imprimir(resultado: dict, detalle: bool) -> None:
    sitio     = resultado["sitio"]
    veredicto = resultado["veredicto"]
    s3p       = resultado["syncs_tercero_tercero"]
    s1p       = resultado["syncs_sitio_tercero"]
    grafo     = resultado["grafo_sincronizacion"]

    print(f"\n{'='*64}")
    print(f"  R11 — Desvinculación y Aislamiento")
    print(f"  Sitio: {sitio}")
    print(f"  Veredicto: {ICONO[veredicto]} {veredicto}")
    print(f"{'='*64}")

    print(f"\nEventos de cookie sync detectados:")
    print(f"  ❌ Tercero → Tercero : {len(s3p)}  (correlación directa entre plataformas)")
    print(f"  ⚠️  Sitio  → Tercero : {len(s1p)}  (el sitio inicia el sync)")

    if grafo:
        print(f"\nRed de sincronización (tercero→tercero):")
        for arista in grafo:
            print(f"  {arista['origen']:30s} → {arista['destino']:30s}  [{arista['eventos']}x]")

    if detalle and s3p:
        print(f"\n{'─'*64}")
        print("Detalle eventos tercero→tercero:\n")
        vistos = set()
        for e in s3p:
            clave = (e["trigger_dominio"], e["receptor_dominio"], e["receptor_url"])
            if clave in vistos:
                continue
            vistos.add(clave)
            print(f"  ❌ {e['trigger_dominio']}  →  {e['receptor_dominio']}")
            print(f"     Trigger : {e['trigger_origin'][:70]}")
            print(f"     URL     : {e['receptor_url']}")
            print()

    if detalle and s1p:
        print(f"\n{'─'*64}")
        print("Detalle eventos sitio→tercero:\n")
        vistos = set()
        for e in s1p:
            clave = (e["trigger_dominio"], e["receptor_dominio"])
            if clave in vistos:
                continue
            vistos.add(clave)
            print(f"  ⚠️  {e['trigger_dominio']}  →  {e['receptor_dominio']}")
            print(f"     URL : {e['receptor_url']}")
            print()

    print()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    args    = sys.argv[1:]
    detalle = "--no-detalle" not in args
    args    = [a for a in args if a != "--no-detalle"]

    if not args:
        print("[ERROR] Uso: r11_desvinculacion.py <crawl-data.sqlite> [--no-detalle]", file=sys.stderr)
        sys.exit(1)
    db_path = Path(args[0])

    if not db_path.exists():
        print(f"[ERROR] No se encontró la BD: {db_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[*] Leyendo BD: {db_path.name}")
    peticiones = cargar_peticiones(db_path)
    print(f"[*] Peticiones con triggering_origin definido: {len(peticiones)}")

    analisis  = analizar_peticiones(peticiones)
    veredicto = veredicto_global(analisis)
    grafo     = construir_grafo(analisis["syncs_3p_3p"])

    resultado = {
        "sitio":                   analisis["sitio"],
        "veredicto":               veredicto,
        "syncs_tercero_tercero":   analisis["syncs_3p_3p"],
        "syncs_sitio_tercero":     analisis["syncs_1p_3p"],
        "grafo_sincronizacion":    grafo,
        "resumen": {
            "total_3p_3p": len(analisis["syncs_3p_3p"]),
            "total_1p_3p": len(analisis["syncs_1p_3p"]),
        },
    }

    RESULTADO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f"[✓] Resultado guardado en {RESULTADO_PATH}")

    imprimir(resultado, detalle)


if __name__ == "__main__":
    main()
