"""
R9 - Minimización de Datos (RGPD Art. 5.1.c)
Analiza los snippets capturados por Privacy Pioneer y detecta qué categorías
de datos personales se transmiten, marcando como FALLO los que aparecen en
fase PRE (sin consentimiento del usuario).
"""

import os
import re
import json
from pathlib import Path
import urllib.parse
import mysql.connector
from collections import defaultdict

# ── Conexión ──────────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("MYSQL_HOST", "localhost"),
    "user":     os.getenv("MYSQL_USER", "pioneer"),
    "password": os.getenv("MYSQL_PASSWORD", "abc"),
    "database": os.getenv("MYSQL_DATABASE", "analysis"),
}

# ── Dominios técnicamente necesarios (excluidos de R9) ────────────────────────
# CMPs necesitan llamar a su API de geolocalización ANTES del consentimiento
# para determinar qué ley aplica (RGPD, CCPA…). Excluirlos es correcto.
_CMP_GEO_DOMAINS = {
    "geolocation.onetrust.com",
    "geolocation.cookielaw.org",
    "cdn.cookielaw.org",
    "geo.privacymanager.io",
    "consent.cookiebot.com",
}
# CDNs de vídeo: la IP aparece como parámetro de enrutamiento, no de tracking
_VIDEO_CDN_DOMAINS = {
    "googlevideo.com",
    "ytimg.com",
}


def _es_excluido(request_url: str) -> bool:
    """Devuelve True si la URL pertenece a un servicio técnicamente necesario."""
    try:
        host = urllib.parse.urlparse(request_url).netloc.lower().lstrip("www.")
        for d in _CMP_GEO_DOMAINS | _VIDEO_CDN_DOMAINS:
            if host == d or host.endswith("." + d):
                return True
    except Exception:
        pass
    return False


# ── Patrones de datos personales ──────────────────────────────────────────────
PATRONES = {
    "IP_ADDRESS": re.compile(
        r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b'
    ),
    "EMAIL": re.compile(
        r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
    ),
    "TELEFONO": re.compile(
        r'(?:\+34|0034)?[\s\-]?[6-9]\d{8}\b'
    ),
    "COORDENADAS": re.compile(
        r'"lat(?:itude)?"\s*:\s*-?\d{1,3}\.\d+|"lon(?:gitude|g)?"\s*:\s*-?\d{1,3}\.\d+'
        r'|lat=-?\d{1,3}\.\d+|lon=-?\d{1,3}\.\d+',
        re.IGNORECASE
    ),
    "GEOLOCALIZACION": re.compile(
        r'"(?:city|ciudad|province|region|country|pais|continent)"\s*:\s*"[^"]+"',
        re.IGNORECASE
    ),
    "DISPOSITIVO_HW": re.compile(
        r'"(?:deviceMemory|hardwareConcurrency|devicePixelRatio)"\s*:\s*[\d.]+',
        re.IGNORECASE
    ),
    "USER_AGENT": re.compile(
        r'Mozilla/\d\.\d\s*\([^)]+\)',
        re.IGNORECASE
    ),
    "ISP_INFO": re.compile(
        r'"(?:isp|organization|autonomous_system_organization)"\s*:\s*"[^"]+"',
        re.IGNORECASE
    ),
    "CANVAS_FINGERPRINT": re.compile(
        r'data:image/png;base64,[A-Za-z0-9+/=]{20,}'
    ),
    "USER_ID": re.compile(
        # UUIDs o tokens de sesión largos en cuerpo de respuesta
        r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
        r'|"(?:uuid|uid|userId|user_id|sessionId|session_id|visitorId|visitor_id)"\s*:\s*"[^"]{8,}"',
        re.IGNORECASE
    ),
}

# IP → MEDIO_RIESGO: la IP aparece implícitamente en toda conexión HTTP;
# su presencia en payloads de rastreadores es una infracción, pero menos grave
# que coordenadas precisas, emails o fingerprinting.
# USER_ID → ALTO_RIESGO: un identificador persistente asignado antes del
# consentimiento permite rastreo cross-session aunque se borren las cookies.
ALTO_RIESGO = {"EMAIL", "TELEFONO", "COORDENADAS", "CANVAS_FINGERPRINT", "USER_ID"}
MEDIO_RIESGO = {"IP_ADDRESS", "GEOLOCALIZACION", "ISP_INFO", "USER_AGENT", "DISPOSITIVO_HW"}


def analizar_snippet(snippet: str) -> dict[str, list[str]]:
    """Devuelve un dict {categoria: [matches]} para los patrones encontrados."""
    encontrados = {}
    for nombre, patron in PATRONES.items():
        matches = patron.findall(snippet)
        if matches:
            # Desduplicar y truncar para legibilidad
            unicos = list(dict.fromkeys(str(m) for m in matches))[:5]
            encontrados[nombre] = unicos
    return encontrados


def evaluar_r9(sitio: str, hallazgos_pre: dict, hallazgos_post: dict) -> dict:
    """
    Reglas:
    - FALLO:      datos de alto riesgo en PRE  →  recogida sin consentimiento
    - ADVERTENCIA: datos de riesgo medio en PRE
    - OK:         solo datos en POST  →  recogida tras consentimiento
    - SIN_DATOS:  sin snippets para analizar
    """
    cats_pre = set(hallazgos_pre.keys())

    if not cats_pre and not hallazgos_post:
        estado = "SIN_DATOS"
        motivo = "No se encontraron snippets con datos personales."
    elif cats_pre & ALTO_RIESGO:
        estado = "FALLO"
        cats = cats_pre & ALTO_RIESGO
        motivo = f"Datos de alto riesgo detectados en fase PRE sin consentimiento: {', '.join(cats)}"
    elif cats_pre & MEDIO_RIESGO:
        estado = "ADVERTENCIA"
        cats = cats_pre & MEDIO_RIESGO
        motivo = f"Datos de riesgo medio detectados en fase PRE: {', '.join(cats)}"
    else:
        estado = "OK"
        motivo = "Datos personales solo detectados en fase POST (tras consentimiento)."

    return {
        "sitio": sitio,
        "estado": estado,
        "motivo": motivo,
        "hallazgos_PRE": hallazgos_pre,
        "hallazgos_POST": hallazgos_post,
    }


def auditar_r9(sitio_filtro: str = None) -> list[dict]:
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    where = "WHERE snippet IS NOT NULL AND snippet != ''"
    params = []
    if sitio_filtro:
        where += " AND rootUrl LIKE %s"
        params.append(f"%{sitio_filtro}%")

    cursor.execute(
        f"SELECT rootUrl, requestUrl, snippet, typ, consent_phase FROM entries {where}",
        params,
    )
    filas = cursor.fetchall()
    cursor.close()
    conn.close()

    # Agrupar por sitio y fase
    por_sitio: dict[str, dict[str, dict]] = defaultdict(lambda: {"PRE": {}, "POST": {}})

    for fila in filas:
        sitio = fila["rootUrl"] or "desconocido"
        fase = fila["consent_phase"] or "PRE"
        snippet = fila["snippet"] or ""
        request_url = fila.get("requestUrl") or ""

        # Excluir servicios técnicamente necesarios (CMP geo, CDN de vídeo)
        if _es_excluido(request_url):
            continue

        hallazgos = analizar_snippet(snippet)
        for cat, matches in hallazgos.items():
            if cat not in por_sitio[sitio][fase]:
                por_sitio[sitio][fase][cat] = []
            por_sitio[sitio][fase][cat].extend(matches)

    # Evaluar R9 por sitio
    resultados = []
    for sitio, fases in por_sitio.items():
        resultado = evaluar_r9(sitio, fases["PRE"], fases["POST"])
        resultados.append(resultado)

    return resultados


def imprimir_reporte(resultados: list[dict]):
    iconos = {"FALLO": "❌", "ADVERTENCIA": "⚠️ ", "OK": "✅", "SIN_DATOS": "ℹ️ "}

    print("\n" + "=" * 60)
    print("         AUDITORÍA R9 — MINIMIZACIÓN DE DATOS")
    print("=" * 60)

    for r in resultados:
        icono = iconos.get(r["estado"], "?")
        print(f"\n{icono}  {r['sitio']}")
        print(f"   Estado : {r['estado']}")
        print(f"   Motivo : {r['motivo']}")

        if r["hallazgos_PRE"]:
            print("   [PRE - sin consentimiento]")
            for cat, vals in r["hallazgos_PRE"].items():
                riesgo = "🔴" if cat in ALTO_RIESGO else "🟡"
                print(f"     {riesgo} {cat}: {vals[0][:80]}{'...' if len(vals[0]) > 80 else ''}")

        if r["hallazgos_POST"]:
            print("   [POST - tras consentimiento]")
            for cat, vals in r["hallazgos_POST"].items():
                print(f"     🔵 {cat}: {vals[0][:80]}{'...' if len(vals[0]) > 80 else ''}")

    print("\n" + "=" * 60)
    total = len(resultados)
    fallos = sum(1 for r in resultados if r["estado"] == "FALLO")
    avisos = sum(1 for r in resultados if r["estado"] == "ADVERTENCIA")
    ok = sum(1 for r in resultados if r["estado"] == "OK")
    print(f"RESUMEN: {total} sitios — ❌ {fallos} FALLO | ⚠️  {avisos} ADVERTENCIA | ✅ {ok} OK")
    print("=" * 60)


def guardar_json(resultados: list[dict], ruta: str = str(Path(__file__).resolve().parents[1] / "analysis_data/r9_resultado.json")):
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)
    print(f"\n💾 Resultado guardado en: {ruta}")


if __name__ == "__main__":
    import sys

    sitio = sys.argv[1] if len(sys.argv) > 1 else None

    try:
        resultados = auditar_r9(sitio_filtro=sitio)
    except Exception as e:
        print(f"[ERROR] No se pudo conectar a MySQL: {e}", file=sys.stderr)
        sys.exit(1)

    if not resultados:
        print("No se encontraron snippets en la base de datos.")
        print("Asegúrate de haber ejecutado el crawler y de que la REST API esté corriendo.")
        guardar_json([])  # escribir lista vacía para que _loader.py no falle
    else:
        imprimir_reporte(resultados)
        guardar_json(resultados)
