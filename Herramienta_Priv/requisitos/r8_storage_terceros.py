"""
R8 - Integridad del dispositivo / Accesos no autorizados a memoria
(RGPD Art. 5.1.f / Considerando 49)

Detecta si scripts de terceros escriben o leen datos de los mecanismos de
almacenamiento del navegador (localStorage, sessionStorage, indexedDB) sin
base legal, lo que permite el seguimiento persistente del usuario entre sesiones.

Fuente: base de datos SQLite de OpenWPM (tabla javascript).

Clasificación (solo scripts de terceros):
  ── FAILED ────────────────────────────────────────────────────────────────
  · Storage.setItem (call/set)  → escritura en localStorage/sessionStorage

  ── WARNING ───────────────────────────────────────────────────────────────
  · Storage.getItem             → lectura de storage (posible rastreo)
  · Storage.removeItem          → borrado de storage por tercero
  · Storage.key                 → acceso por índice a storage
  · window.localStorage (get)   → acceso al objeto localStorage
  · window.sessionStorage (get) → acceso al objeto sessionStorage
  · window.navigator.storage    → acceso a StorageManager API
  · window.indexedDB            → acceso a IndexedDB

  ── Veredicto global ──────────────────────────────────────────────────────
  FAILED  si existe al menos una escritura de tercero (setItem)
  WARNING si solo lecturas/accesos de tercero (sin escrituras)
  PASSED  si no se detectan accesos de tercero a storage

Uso:
  python3 r8_storage_terceros.py                              # BD por defecto
  python3 r8_storage_terceros.py /ruta/crawl-data.sqlite      # BD explícita
  python3 r8_storage_terceros.py --no-detalle                 # solo resumen
"""

import sqlite3
import sys
import json
from pathlib import Path
from urllib.parse import urlparse
from collections import defaultdict

# ── Rutas ─────────────────────────────────────────────────────────────────────
RESULTADO_PATH = Path(__file__).resolve().parents[1] / "analysis_data/r8_resultado.json"

# ── Clasificación de símbolos ─────────────────────────────────────────────────
SIMBOLOS_FAILED = {
    "Storage.setItem",        # escritura en localStorage / sessionStorage
}

SIMBOLOS_WARNING = {
    "Storage.getItem",        # lectura de storage
    "Storage.removeItem",     # borrado de entrada
    "Storage.key",            # acceso por índice
    "Storage.length",         # tamaño del storage
    "window.localStorage",    # acceso al objeto localStorage
    "window.sessionStorage",  # acceso al objeto sessionStorage
    "window.navigator.storage",  # StorageManager API
    "window.indexedDB",       # acceso a IndexedDB
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


def parsear_args(args_raw) -> list:
    """Devuelve la lista de argumentos de la llamada, o [] si no hay."""
    if not args_raw:
        return []
    try:
        parsed = json.loads(args_raw)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


# ── Carga de datos ────────────────────────────────────────────────────────────
def cargar_datos(db_path: Path) -> list[dict]:
    """
    Lee la tabla javascript filtrando símbolos de storage.
    Devuelve una lista de registros individuales (sin agrupar) para
    poder analizar qué claves concretas escribe/lee cada script.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT
            script_url,
            symbol,
            operation,
            arguments,
            value,
            top_level_url
        FROM javascript
        WHERE (
            symbol LIKE '%Storage%'     OR
            symbol LIKE '%localStorage%' OR
            symbol LIKE '%sessionStorage%' OR
            symbol LIKE '%indexedDB%'
        )
        AND script_url IS NOT NULL
        AND script_url != ''
    """)
    filas = cur.fetchall()
    conn.close()
    return [dict(f) for f in filas]


# ── Análisis ──────────────────────────────────────────────────────────────────
def analizar(filas: list[dict]) -> dict:
    """
    Agrupa las operaciones de storage por script de tercero y
    clasifica cada una en FAILED o WARNING.
    """
    # Inferir sitio desde top_level_url más frecuente
    site_url = ""
    if filas:
        from collections import Counter
        conteo   = Counter(f["top_level_url"] or "" for f in filas)
        site_url = conteo.most_common(1)[0][0]

    sitio = dominio_base(site_url)

    # Agrupar por script_url → {symbol → set(claves_args)}
    por_script: dict[str, dict] = defaultdict(lambda: {
        "operaciones": defaultdict(set),   # symbol → {clave1, clave2…}
        "top_level_url": "",
        "nivel": None,                     # FAILED | WARNING | None
    })

    for fila in filas:
        script_url = fila["script_url"]
        if not es_tercero(script_url, site_url):
            continue

        symbol    = fila["symbol"]
        args      = parsear_args(fila["arguments"])
        clave     = args[0] if args else ""

        info = por_script[script_url]
        info["top_level_url"] = fila["top_level_url"] or ""
        info["operaciones"][symbol].add(clave)

        # Actualizar nivel: FAILED prevalece sobre WARNING
        if symbol in SIMBOLOS_FAILED:
            info["nivel"] = "FAILED"
        elif symbol in SIMBOLOS_WARNING and info["nivel"] != "FAILED":
            info["nivel"] = "WARNING"

    # Construir listas de resultados
    fallos  = []
    avisos  = []

    for script_url, info in por_script.items():
        if info["nivel"] is None:
            continue   # símbolo no clasificado, ignorar

        entrada = {
            "script":    script_url,
            "dominio":   dominio_base(script_url),
            "nivel":     info["nivel"],
            "operaciones": _serializar_ops(info["operaciones"]),
        }

        if info["nivel"] == "FAILED":
            fallos.append(entrada)
        else:
            avisos.append(entrada)

    # Veredicto global
    if fallos:
        veredicto = "FAILED"
    elif avisos:
        veredicto = "WARNING"
    else:
        veredicto = "PASSED"

    return {
        "sitio":     sitio,
        "veredicto": veredicto,
        "resumen": {
            "FAILED":  len(fallos),
            "WARNING": len(avisos),
        },
        "fallos": fallos,
        "avisos": avisos,
    }


def _serializar_ops(operaciones: dict) -> list[dict]:
    """Convierte el dict de operaciones en lista serializable JSON."""
    resultado = []
    for symbol, claves in sorted(operaciones.items()):
        claves_limpias = sorted(str(c) for c in claves if c != "")
        resultado.append({
            "symbol": symbol,
            "claves": claves_limpias,
        })
    return resultado


# ── Salida por consola ────────────────────────────────────────────────────────
def imprimir(resultado: dict, detalle: bool) -> None:
    sitio     = resultado["sitio"]
    veredicto = resultado["veredicto"]
    resumen   = resultado["resumen"]
    fallos    = resultado["fallos"]
    avisos    = resultado["avisos"]

    print(f"\n{'='*64}")
    print(f"  R8 — Integridad del dispositivo / Storage de terceros")
    print(f"  Sitio   : {sitio}")
    print(f"  Veredicto: {ICONO[veredicto]} {veredicto}")
    print(f"{'='*64}")
    print(f"\nScripts de 3ª parte con acceso a storage:")
    print(f"  ❌ FAILED  (escrituras) : {resumen['FAILED']}")
    print(f"  ⚠️  WARNING (lecturas)  : {resumen['WARNING']}")

    if not detalle:
        print()
        return

    if fallos:
        print(f"\n{'─'*64}")
        print("FAILED — Scripts de tercero con escritura en storage:\n")
        for f in fallos:
            print(f"  ❌ {f['dominio']}")
            print(f"     Script: {f['script'][:80]}")
            for op in f["operaciones"]:
                claves = ", ".join(op["claves"]) if op["claves"] else "—"
                print(f"     {op['symbol']:30s}  claves: {claves[:60]}")
            print()

    if avisos:
        print(f"{'─'*64}")
        print("WARNING — Scripts de tercero con lectura/acceso a storage:\n")
        for a in avisos:
            print(f"  ⚠️  {a['dominio']}")
            print(f"     Script: {a['script'][:80]}")
            for op in a["operaciones"]:
                claves = ", ".join(op["claves"]) if op["claves"] else "—"
                print(f"     {op['symbol']:30s}  claves: {claves[:60]}")
            print()

    print()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    args    = sys.argv[1:]
    detalle = "--no-detalle" not in args
    args    = [a for a in args if a != "--no-detalle"]

    if not args:
        print("[ERROR] Uso: r8_storage_terceros.py <crawl-data.sqlite> [--no-detalle]", file=sys.stderr)
        sys.exit(1)
    db_path = Path(args[0])

    if not db_path.exists():
        print(f"[ERROR] No se encontró la BD: {db_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[*] Leyendo BD: {db_path.name}")
    filas = cargar_datos(db_path)
    print(f"[*] Registros de storage en BD: {len(filas)}")

    resultado = analizar(filas)

    RESULTADO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f"[✓] Resultado guardado en {RESULTADO_PATH}")

    imprimir(resultado, detalle)


if __name__ == "__main__":
    main()
