"""
R2 + R3 - Bloqueo de cookies no exentas y web beacons de terceros
(RGPD Art. 5.1.a, 6 y 7 / LSSI Art. 22.2 / Directiva ePrivacy)

R2: Verifica que ningún rastreador de marketing o analítica se activa antes de
que el usuario interaccione con el banner de consentimiento (fase PRE).

R3: Verifica que no se realizan peticiones a dominios de rastreo (web beacons)
en la carga inicial. Queda cubierto por R2: el tipo «trackingPixel» en fase PRE
de Privacy Pioneer corresponde exactamente a los web beacons de terceros. La
separación PRE/POST es metodológicamente superior a la carga inicial de WEC
para medir bloqueo antes del consentimiento, por lo que no se necesita script
independiente para R3.

Las cookies/rastreadores «no exentos» son aquellos que requieren consentimiento
expreso según la Directiva ePrivacy: publicidad comportamental, analítica de
comportamiento, redes sociales, píxeles de seguimiento y fingerprinting.
Las cookies estrictamente necesarias (funcionales, de sesión, de seguridad)
están exentas de consentimiento.

Fuente de datos:
  MySQL tabla «entries» de Privacy Pioneer (usuario: pioneer, pass: abc, BD: analysis).
  Campo «consent_phase»: 'PRE' (antes del clic en el banner) / 'POST' (después).

Tipos no exentos evaluados:
  advertising    — Publicidad comportamental
  analytics      — Analítica de comportamiento
  social         — Redes sociales (botones Like, píxeles de FB...)
  trackingPixel  — Píxel de seguimiento
  fingerprinting — Huella digital del dispositivo

Tipos exentos (no se evalúan en R2):
  ipAddress, region, city — información de red, no cookies de marketing

Veredicto por sitio:
  PASSED  — Ningún rastreador no exento activo en fase PRE
  FAILED  — Al menos un rastreador no exento detectado antes del consentimiento

Uso:
  python3 r2_r3_cookies_beacons.py              # todos los sitios en la BD
  python3 r2_r3_cookies_beacons.py abc.es       # filtrar por sitio (substring)
  python3 r2_r3_cookies_beacons.py --no-detalle # solo resumen
"""

import os
import sys
import json
import re
import mysql.connector
from pathlib import Path
from urllib.parse import urlparse
from collections import defaultdict

# ── Configuración ─────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("MYSQL_HOST", "localhost"),
    "user":     os.getenv("MYSQL_USER", "pioneer"),
    "password": os.getenv("MYSQL_PASSWORD", "abc"),
    "database": os.getenv("MYSQL_DATABASE", "analysis"),
}

RESULTADO_PATH = Path(__file__).resolve().parents[1] / "analysis_data/r2_resultado.json"

# Tipos que REQUIEREN consentimiento (no exentos)
TIPOS_NO_EXENTOS = {"advertising", "analytics", "social", "trackingPixel", "fingerprinting"}

ETIQUETA_TIPO = {
    "advertising":    "Publicidad comportamental",
    "analytics":      "Analítica de comportamiento",
    "social":         "Redes sociales",
    "trackingPixel":  "Píxel de seguimiento",
    "fingerprinting": "Fingerprinting de dispositivo",
}

ICONO = {"PASSED": "✅", "WARNING": "⚠️ ", "FAILED": "❌"}


# ── Utilidades ────────────────────────────────────────────────────────────────

def dominio_base(url: str) -> str:
    """Extrae el dominio registrable de una URL (sin subdominio www)."""
    try:
        host = urlparse(url).netloc or url
        host = re.sub(r"^www\.", "", host)
        return host.split(":")[0]
    except Exception:
        return url


# ── Consulta MySQL ─────────────────────────────────────────────────────────────

def obtener_violaciones(filtro_sitio: str | None) -> dict:
    """
    Devuelve un dict {rootUrl: [lista de violaciones]} con los rastreadores
    no exentos detectados en fase PRE.
    """
    conn = mysql.connector.connect(**DB_CONFIG)
    cur  = conn.cursor(dictionary=True)

    tipos_placeholder = ", ".join(["%s"] * len(TIPOS_NO_EXENTOS))
    sql = f"""
        SELECT rootUrl, requestUrl, typ, parentCompany, consent_phase
        FROM entries
        WHERE consent_phase = 'PRE'
          AND typ IN ({tipos_placeholder})
    """
    params = list(TIPOS_NO_EXENTOS)

    if filtro_sitio:
        sql += " AND rootUrl LIKE %s"
        params.append(f"%{filtro_sitio}%")

    cur.execute(sql, params)
    filas = cur.fetchall()

    cur.close()
    conn.close()

    # Agrupar por sitio y deduplicar por (dominio_rastreador, tipo)
    por_sitio: dict[str, dict] = defaultdict(dict)
    for fila in filas:
        sitio   = fila["rootUrl"]
        dominio = dominio_base(fila["requestUrl"])
        clave   = (dominio, fila["typ"])

        if clave not in por_sitio[sitio]:
            por_sitio[sitio][clave] = {
                "dominio_rastreador": dominio,
                "url_completa":       fila["requestUrl"],
                "tipo":               fila["typ"],
                "tipo_legible":       ETIQUETA_TIPO.get(fila["typ"], fila["typ"]),
                "empresa":            fila["parentCompany"] or "Desconocida",
            }

    # Convertir a listas
    return {sitio: list(v.values()) for sitio, v in por_sitio.items()}


def obtener_sitios_auditados(filtro_sitio: str | None) -> list[str]:
    """Devuelve todos los rootUrl distintos en la tabla entries."""
    conn = mysql.connector.connect(**DB_CONFIG)
    cur  = conn.cursor()
    if filtro_sitio:
        cur.execute("SELECT DISTINCT rootUrl FROM entries WHERE rootUrl LIKE %s", (f"%{filtro_sitio}%",))
    else:
        cur.execute("SELECT DISTINCT rootUrl FROM entries")
    sitios = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return sitios


# ── Evaluación ────────────────────────────────────────────────────────────────

def evaluar_sitio(sitio: str, violaciones: list[dict]) -> dict:
    if not violaciones:
        return {
            "estado": "PASSED",
            "detalle": (
                "No se detectó ningún rastreador no exento activo antes del "
                "consentimiento. Cumple RGPD Art. 7 / LSSI Art. 22.2."
            ),
        }

    tipos_encontrados = sorted({v["tipo_legible"] for v in violaciones})
    empresas          = sorted({v["empresa"] for v in violaciones if v["empresa"] != "Desconocida"})

    return {
        "estado": "FAILED",
        "detalle": (
            f"Se detectaron {len(violaciones)} rastreadores no exentos activos "
            f"ANTES del consentimiento: {', '.join(tipos_encontrados)}. "
            f"Empresas implicadas: {', '.join(empresas) if empresas else 'no identificadas'}. "
            f"Incumple RGPD Art. 7 / LSSI Art. 22.2 (bloqueo previo al consentimiento)."
        ),
    }


# ── Salida por consola ────────────────────────────────────────────────────────

def imprimir_sitio(sitio: str, evaluacion: dict, violaciones: list[dict], detalle: bool) -> None:
    veredicto = evaluacion["estado"]
    print(f"\n{'='*64}")
    print(f"  R2 — Cookies no exentas")
    print(f"  Sitio    : {sitio}")
    print(f"  Veredicto: {ICONO[veredicto]} {veredicto}")
    print(f"{'='*64}")
    print(f"\n{evaluacion['detalle']}")

    if not detalle or not violaciones:
        print()
        return

    print(f"\n{'─'*64}")
    print(f"Rastreadores no exentos en fase PRE ({len(violaciones)}):\n")

    por_tipo: dict[str, list] = defaultdict(list)
    for v in sorted(violaciones, key=lambda x: x["tipo"]):
        por_tipo[v["tipo_legible"]].append(v)

    for tipo_label, entradas in por_tipo.items():
        print(f"  [{tipo_label}]")
        for e in entradas:
            empresa = f" ({e['empresa']})" if e["empresa"] != "Desconocida" else ""
            print(f"    • {e['dominio_rastreador']}{empresa}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args    = sys.argv[1:]
    detalle = "--no-detalle" not in args
    args    = [a for a in args if a != "--no-detalle"]

    filtro_sitio = args[0] if args else None

    try:
        violaciones_por_sitio = obtener_violaciones(filtro_sitio)
        todos_los_sitios      = obtener_sitios_auditados(filtro_sitio)
    except mysql.connector.Error as e:
        print(f"[ERROR] No se pudo conectar a MySQL: {e}", file=sys.stderr)
        sys.exit(1)

    if not todos_los_sitios:
        print("[!] No hay datos en la tabla 'entries'. Ejecuta primero el crawler.")
        salida = {
            "requisito":   "R2",
            "descripcion": "Bloqueo de cookies no exentas antes del consentimiento",
            "sitios":      {},
            "no_evaluable": True,
        }
        RESULTADO_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RESULTADO_PATH, "w", encoding="utf-8") as f:
            json.dump(salida, f, ensure_ascii=False, indent=2)
        sys.exit(0)

    resultados = {}
    for sitio in sorted(todos_los_sitios):
        violaciones = violaciones_por_sitio.get(sitio, [])
        evaluacion  = evaluar_sitio(sitio, violaciones)
        resultados[sitio] = {
            "veredicto":  evaluacion["estado"],
            "evaluacion": evaluacion,
            "violaciones": violaciones,
        }
        imprimir_sitio(sitio, evaluacion, violaciones, detalle)

    # Guardar resultado
    salida = {
        "requisito": "R2",
        "descripcion": "Bloqueo de cookies no exentas antes del consentimiento",
        "sitios": resultados,
    }
    RESULTADO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)
    print(f"[✓] Resultado guardado en {RESULTADO_PATH}")


if __name__ == "__main__":
    main()
