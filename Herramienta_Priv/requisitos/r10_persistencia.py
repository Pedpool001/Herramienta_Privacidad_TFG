"""
R10 - Limitación del plazo de persistencia (RGPD Art. 5.1.e)
Las cookies persistentes deben tener una caducidad vinculada a su finalidad,
evitando plazos de varios años sin justificación.

Fuentes:
  - cookies.yml       (WEC) — cookies con expiresDays y firstPartyStorage
  - local-storage.yml (WEC) — almacenamiento sin expiración por diseño
  - Open Cookie Database    — categoría declarada de cada cookie

Jerarquía de evaluación por cookie:
  1. session o sin fecha  → PASSED  (sin caducidad fija, no aplica)
  2. expiresDays < 0      → PASSED  (ya expirada al momento del análisis)
  3. Cookie CMP/consent   → PASSED si ≤ 395 días / WARNING si > 395
  4. 3ª parte:
       - publicidad/tracking: > 90 días  → FAILED
       - analítica:           > 365 días → FAILED
       - resto/sin OCD:       > 365 días → FAILED
  5. 1ª parte:
       - publicidad:          > 180 días → WARNING
       - cualquier categoría: > 395 días → WARNING
       - resto:               → PASSED

  LocalStorage de terceros → FAILED (sin expiración por diseño del API)

Uso:
  python3 r10_persistencia.py                          # directorio WEC por defecto
  python3 r10_persistencia.py /ruta/wec/output/        # directorio WEC explícito
  python3 r10_persistencia.py /ruta/cookies.yml        # fichero directo
  python3 r10_persistencia.py --no-detalle             # solo resumen
"""

import json
import sys
import fnmatch
import yaml
from pathlib import Path
from urllib.parse import urlparse
from collections import Counter

# ── Rutas ─────────────────────────────────────────────────────────────────────
_TFG_DIR = Path(__file__).resolve().parents[2]
WEC_OUTPUT_DEFAULT = _TFG_DIR / "WEC/website-evidence-collector/output"
OCD_PATH = _TFG_DIR / "Herramienta_Priv/assets/open-cookie-database.json"
RESULTADO_PATH = Path(__file__).resolve().parents[1] / "analysis_data/r10_resultado.json"

# ── Patrones de detección de consent/CMP cookies ──────────────────────────────
CMP_PATTERNS = [
    "didomi", "optanon", "cookieconsent", "cookiebot", "trustarc",
    "euconsent", "gdpr", "rgpd", "consent", "notice_gdpr", "cmapi",
    "quantcast", "usercentrics", "tcf",
]
# Nombres exactos conocidos que no contienen los patrones anteriores
CMP_EXACT = {"FCCDCF", "eupubconsent", "eupubconsent-v2"}

# ── Categorías OCD ────────────────────────────────────────────────────────────
CAT_PUBLICIDAD = {"advertising", "marketing", "tracking", "social media"}
CAT_ANALITICA  = {"analytics", "performance", "statistics"}

# ── Umbrales (días) ───────────────────────────────────────────────────────────
UMBRAL_CONSENT      = 395   # consent/CMP: 13 meses
UMBRAL_3P_PUBLI     =  90   # 3ª parte publicidad/tracking
UMBRAL_3P_ANALITICA = 365   # 3ª parte analítica
UMBRAL_3P_GENERICO  = 365   # 3ª parte sin categoría OCD
UMBRAL_1P_PUBLI     = 180   # 1ª parte publicidad (6 meses)
UMBRAL_1P_GENERICO  = 395   # 1ª parte cualquier categoría: 13 meses

ICONO = {"PASSED": "✅", "WARNING": "⚠️ ", "FAILED": "❌"}


# ── Open Cookie Database ──────────────────────────────────────────────────────
def cargar_ocd() -> dict:
    if not OCD_PATH.exists():
        return {}
    with open(OCD_PATH, encoding="utf-8") as f:
        return json.load(f)


def buscar_en_ocd(nombre: str, ocd: dict) -> dict | None:
    if nombre in ocd:
        return ocd[nombre][0]
    for patron, entradas in ocd.items():
        if entradas[0].get("wildcard") == "1" and fnmatch.fnmatch(nombre, patron):
            return entradas[0]
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────
def dominio_base(url: str) -> str:
    """Extrae dominio registrable: 'www.elpais.com' → 'elpais.com'."""
    try:
        host = urlparse(url).hostname or ""
        parts = host.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host
    except Exception:
        return ""


def es_consent_cookie(nombre: str) -> bool:
    if nombre in CMP_EXACT:
        return True
    nl = nombre.lower()
    return any(p in nl for p in CMP_PATTERNS)


# ── Evaluación de cookies ─────────────────────────────────────────────────────
def evaluar_cookie(cookie: dict, ocd: dict) -> dict:
    nombre    = cookie.get("name", "")
    dias      = cookie.get("expiresDays")
    es_1p     = cookie.get("firstPartyStorage", True)
    es_sesion = cookie.get("session", False)
    dominio   = cookie.get("domain", "")

    ocd_entry = buscar_en_ocd(nombre, ocd)
    categoria = (ocd_entry.get("category", "").lower().strip() if ocd_entry else None) or None
    empresa   = ocd_entry.get("dataController", "") if ocd_entry else ""

    base = {
        "nombre": nombre,
        "dominio": dominio,
        "dias": dias,
        "primera_parte": es_1p,
        "sesion": es_sesion,
        "categoria_ocd": categoria or "desconocida",
        "empresa_ocd": empresa,
    }

    # 1. Sesión o sin fecha de expiración
    if es_sesion or dias is None:
        return {**base, "estado": "PASSED",
                "motivo": "Cookie de sesión — sin caducidad fija"}

    dias = float(dias)

    # 2. Ya expirada en el momento del análisis
    if dias < 0:
        return {**base, "estado": "PASSED",
                "motivo": "Cookie ya expirada al momento del análisis"}

    # 3. Consent / CMP
    if es_consent_cookie(nombre):
        if dias <= UMBRAL_CONSENT:
            return {**base, "estado": "PASSED",
                    "motivo": f"Cookie CMP dentro de 13 meses ({dias:.0f} días)"}
        return {**base, "estado": "WARNING",
                "motivo": f"Cookie CMP excede 13 meses ({dias:.0f} días > {UMBRAL_CONSENT})"}

    # 4. Terceras partes
    if not es_1p:
        if categoria in CAT_PUBLICIDAD:
            if dias > UMBRAL_3P_PUBLI:
                return {**base, "estado": "FAILED",
                        "motivo": f"3ª parte publicitaria excede {UMBRAL_3P_PUBLI} días ({dias:.0f})"}
        elif categoria in CAT_ANALITICA:
            if dias > UMBRAL_3P_ANALITICA:
                return {**base, "estado": "FAILED",
                        "motivo": f"3ª parte analítica excede {UMBRAL_3P_ANALITICA} días ({dias:.0f})"}
        else:
            if dias > UMBRAL_3P_GENERICO:
                return {**base, "estado": "FAILED",
                        "motivo": f"3ª parte (categoría: {categoria or 'desconocida'}) excede {UMBRAL_3P_GENERICO} días ({dias:.0f})"}
        return {**base, "estado": "PASSED",
                "motivo": f"3ª parte dentro del umbral ({dias:.0f} días)"}

    # 5. Primera parte
    if categoria in CAT_PUBLICIDAD and dias > UMBRAL_1P_PUBLI:
        return {**base, "estado": "WARNING",
                "motivo": f"1ª parte publicitaria excede {UMBRAL_1P_PUBLI} días ({dias:.0f})"}
    if dias > UMBRAL_1P_GENERICO:
        return {**base, "estado": "WARNING",
                "motivo": f"1ª parte excede 13 meses ({dias:.0f} días > {UMBRAL_1P_GENERICO})"}

    return {**base, "estado": "PASSED",
            "motivo": f"1ª parte dentro del umbral ({dias:.0f} días)"}


# ── Análisis de LocalStorage ──────────────────────────────────────────────────
def analizar_local_storage(ls_data: dict, mapa_cookies: dict) -> list:
    """
    Detecta items de LocalStorage escritos por scripts de terceros.
    LocalStorage no tiene expiración por diseño → FAILED si es de tercero.
    Si el nombre del item coincide con una cookie ya evaluada, anota la
    relación en `duplica_cookie` para facilitar la interpretación.
    Estructura ls_data: { site_url: { item_name: { value, firstPartyStorage, log } } }
    mapa_cookies: { nombre_cookie: resultado_evaluado }
    """
    resultados = []
    for site_url, items in ls_data.items():
        site_dom = dominio_base(site_url)
        if not isinstance(items, dict):
            continue
        for nombre_item, datos in items.items():
            if not isinstance(datos, dict):
                continue
            stack = datos.get("log", {}).get("stack", [])
            scripts_terceros = [
                f.get("fileName", "")
                for f in stack
                if isinstance(f, dict)
                and f.get("fileName", "").startswith("http")
                and dominio_base(f["fileName"]) != site_dom
            ]
            if not scripts_terceros:
                continue

            # Cruzar con cookies evaluadas
            cookie_homologa = mapa_cookies.get(nombre_item)
            duplica = None
            if cookie_homologa:
                duplica = {
                    "estado_cookie": cookie_homologa["estado"],
                    "dias_cookie": cookie_homologa["dias"],
                    "motivo_cookie": cookie_homologa["motivo"],
                }

            resultados.append({
                "nombre": nombre_item,
                "sitio": site_url,
                "script_tercero": scripts_terceros[0],
                "estado": "FAILED",
                "motivo": "LocalStorage escrito por tercero — sin expiración (persistencia indefinida)",
                "duplica_cookie": duplica,
            })
    return resultados


# ── Veredicto global ──────────────────────────────────────────────────────────
def veredicto_global(cookies: list, ls_items: list) -> str:
    estados = [r["estado"] for r in cookies + ls_items]
    if "FAILED" in estados:
        return "FAILED"
    if "WARNING" in estados:
        return "WARNING"
    return "PASSED"


def resumen_estados(resultados: list) -> dict:
    c = Counter(r["estado"] for r in resultados)
    return {"PASSED": c.get("PASSED", 0), "WARNING": c.get("WARNING", 0), "FAILED": c.get("FAILED", 0)}


# ── Salida por consola ────────────────────────────────────────────────────────
def imprimir(resultado: dict, detalle: bool) -> None:
    sitio     = resultado.get("sitio", "desconocido")
    veredicto = resultado["veredicto"]
    r_cookies = resultado.get("cookies", [])
    r_ls      = resultado.get("local_storage", [])
    res       = resultado.get("resumen_cookies", {})

    print(f"\n{'='*62}")
    print(f"  R10 — Limitación del plazo de persistencia")
    print(f"  Sitio: {sitio}")
    print(f"  Veredicto: {ICONO[veredicto]} {veredicto}")
    print(f"{'='*62}")

    print(f"\nCookies analizadas : {len(r_cookies)}")
    print(f"  ✅ PASSED  : {res.get('PASSED', 0)}")
    print(f"  ⚠️  WARNING : {res.get('WARNING', 0)}")
    print(f"  ❌ FAILED  : {res.get('FAILED', 0)}")

    if r_ls:
        print(f"\nLocalStorage de 3ª parte : {len(r_ls)} item(s) — todos ❌ FAILED")

    if detalle:
        incidencias = [r for r in r_cookies if r["estado"] != "PASSED"]
        if incidencias:
            print(f"\n{'─'*62}")
            print("Incidencias en cookies:")
            for r in incidencias:
                icono = ICONO[r["estado"]]
                fp = "1ª parte" if r["primera_parte"] else "3ª parte"
                cat = r["categoria_ocd"]
                emp = f" ({r['empresa_ocd']})" if r["empresa_ocd"] else ""
                print(f"\n  {icono} {r['nombre']}")
                print(f"     Dominio : {r['dominio']}  [{fp}]")
                print(f"     Días    : {r['dias']}")
                print(f"     OCD     : {cat}{emp}")
                print(f"     Motivo  : {r['motivo']}")

        if r_ls:
            print(f"\n{'─'*62}")
            print("Items de LocalStorage de terceros:")
            for item in r_ls:
                print(f"\n  ❌ {item['nombre']}")
                print(f"     Script : {item['script_tercero']}")
                print(f"     Motivo : {item['motivo']}")
                dup = item.get("duplica_cookie")
                if dup:
                    icono_dup = ICONO[dup["estado_cookie"]]
                    print(f"     ↳ Cookie homónima : {icono_dup} {dup['estado_cookie']} "
                          f"({dup['dias_cookie']:.0f} días — {dup['motivo_cookie']})")

    print()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    args    = sys.argv[1:]
    detalle = "--no-detalle" not in args
    args    = [a for a in args if a != "--no-detalle"]

    # Resolver ruta al directorio de salida de WEC
    if args:
        ruta = Path(args[0])
        wec_dir = ruta.parent if ruta.is_file() else ruta
    else:
        wec_dir = WEC_OUTPUT_DEFAULT

    cookies_path = wec_dir / "cookies.yml"
    ls_path      = wec_dir / "local-storage.yml"

    if not cookies_path.exists():
        print(f"[ERROR] No se encontró {cookies_path}", file=sys.stderr)
        sys.exit(1)

    # Cargar ficheros WEC
    with open(cookies_path, encoding="utf-8") as f:
        cookies_raw = yaml.safe_load(f) or []

    ls_data = {}
    if ls_path.exists():
        with open(ls_path, encoding="utf-8") as f:
            ls_data = yaml.safe_load(f) or {}
    else:
        print("[AVISO] local-storage.yml no encontrado — análisis de LocalStorage omitido")

    # Cargar OCD
    ocd = cargar_ocd()
    if not ocd:
        print("[AVISO] Open Cookie Database no disponible — umbrales por categoría desactivados")

    # Inferir sitio desde el campo location del primer cookie
    sitio = "desconocido"
    for c in cookies_raw:
        loc = c.get("log", {}).get("location", "")
        if loc:
            sitio = dominio_base(loc)
            break

    # Evaluar cookies y construir mapa {nombre → resultado} para cruce con LS
    resultados_cookies = [evaluar_cookie(c, ocd) for c in cookies_raw]
    mapa_cookies       = {r["nombre"]: r for r in resultados_cookies}
    resultados_ls      = analizar_local_storage(ls_data, mapa_cookies)
    veredicto          = veredicto_global(resultados_cookies, resultados_ls)

    resultado_final = {
        "sitio": sitio,
        "veredicto": veredicto,
        "resumen_cookies": resumen_estados(resultados_cookies),
        "total_ls_terceros": len(resultados_ls),
        "cookies": resultados_cookies,
        "local_storage": resultados_ls,
    }

    # Guardar JSON
    RESULTADO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(resultado_final, f, ensure_ascii=False, indent=2)
    print(f"[✓] Resultado guardado en {RESULTADO_PATH}")

    imprimir(resultado_final, detalle)


if __name__ == "__main__":
    main()
