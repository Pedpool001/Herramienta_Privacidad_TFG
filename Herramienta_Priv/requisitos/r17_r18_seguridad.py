"""
R17 - Redirección Forzosa HTTPS (LSSI Art. 9 / RGPD Art. 32)
R18 - Implementación de Content Security Policy (RGPD Art. 32)

Fuente principal: requests.har generado por WEC.

R17 — Lógica:
  1. Petición en vivo a http://{sitio} con allow_redirects=False.
     - 301 → PASSED  (redirección permanente a HTTPS)
     - 302/307/308 → WARNING  (redirección temporal, debería ser permanente)
     - 200 → FAILED  (sirve HTTP sin redirigir)
     - Puerto 80 rechazado → PASSED  (bloquea HTTP directamente)
     - Timeout / error → UNKNOWN
  2. Complemento HSTS (desde cabeceras del documento principal en el HAR):
     - max-age >= 31536000 (1 año) con includeSubDomains → PASSED
     - max-age presente pero < 1 año, o sin includeSubDomains → WARNING
     - Ausente → WARNING  (ventana de vulnerabilidad en la primera visita)

R18 — Lógica:
  1. Sin CSP ni CSP-Report-Only → FAILED
  2. Solo CSP-Report-Only → WARNING  (modo observación, no se aplica)
  3. CSP presente → analizar contenido:
     - 'unsafe-inline' o 'unsafe-eval' en script-src/default-src → WARNING
     - Wildcard '*' en script-src/default-src → WARNING
     - Sin script-src ni default-src → WARNING  (CSP incompleta)
     - Sin frame-ancestors → WARNING  (clickjacking posible)
     - Sin ninguna advertencia → PASSED

Uso:
  python3 r17_r18_seguridad.py                        # HAR por defecto
  python3 r17_r18_seguridad.py /ruta/wec/output/      # directorio WEC explícito
  python3 r17_r18_seguridad.py /ruta/requests.har     # fichero HAR directo
  python3 r17_r18_seguridad.py --no-detalle           # solo resumen
"""

import json
import sys
import warnings
warnings.filterwarnings("ignore")  # suprimir advertencia de versión urllib3 antes del import

import requests
from pathlib import Path
from urllib.parse import urlparse

# ── Rutas ─────────────────────────────────────────────────────────────────────
_TFG_DIR = Path(__file__).resolve().parents[2]
WEC_OUTPUT_DEFAULT = _TFG_DIR / "WEC/website-evidence-collector/output"
RESULTADO_PATH = Path(__file__).resolve().parents[1] / "analysis_data/r17_r18_resultado.json"

ICONO = {"PASSED": "✅", "WARNING": "⚠️ ", "FAILED": "❌", "UNKNOWN": "❓"}

TIMEOUT_HTTP = 10  # segundos para la petición HTTP en vivo

# Umbral HSTS: 1 año en segundos
HSTS_MIN_MAX_AGE = 31_536_000


# ── Helpers ───────────────────────────────────────────────────────────────────
def dominio_base(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        parts = host.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host
    except Exception:
        return ""


def cabeceras_dict(entry: dict) -> dict:
    return {h["name"].lower(): h["value"] for h in entry["response"]["headers"]}


def entrada_principal(entries: list) -> dict | None:
    """Devuelve la primera entrada de tipo 'document' del HAR."""
    for e in entries:
        if e.get("_resourceType") == "document":
            return e
    return None


# ── R17 ───────────────────────────────────────────────────────────────────────
def verificar_redirect_http(sitio: str) -> dict:
    """Petición en vivo a http://{sitio}/ para comprobar la redirección."""
    url_http = f"http://{sitio}/"
    try:
        resp = requests.get(url_http, allow_redirects=False, timeout=TIMEOUT_HTTP)
        status = resp.status_code
        location = resp.headers.get("location", "")

        if status == 301 and location.lower().startswith("https"):
            return {
                "estado": "PASSED",
                "status_code": status,
                "location": location,
                "motivo": "Redirección 301 permanente a HTTPS",
            }
        if status in (302, 307, 308) and location.lower().startswith("https"):
            return {
                "estado": "WARNING",
                "status_code": status,
                "location": location,
                "motivo": f"Redirección {status} temporal a HTTPS — debería ser 301 permanente",
            }
        if status in (301, 302, 307, 308) and not location.lower().startswith("https"):
            return {
                "estado": "FAILED",
                "status_code": status,
                "location": location,
                "motivo": f"Redirección {status} pero el destino NO es HTTPS: {location}",
            }
        if status == 403:
            return {
                "estado": "WARNING",
                "status_code": status,
                "location": location,
                "motivo": "HTTP responde 403 Forbidden — posible bloqueo por WAF/CDN; "
                          "verificar manualmente si un navegador real recibe redirect",
            }
        return {
            "estado": "FAILED",
            "status_code": status,
            "location": location,
            "motivo": f"Respuesta HTTP {status} sin redirigir a HTTPS",
        }

    except requests.exceptions.ConnectionError:
        return {
            "estado": "PASSED",
            "status_code": None,
            "location": None,
            "motivo": "Puerto 80 rechaza conexiones — HTTP no accesible (protección activa)",
        }
    except requests.exceptions.Timeout:
        return {
            "estado": "UNKNOWN",
            "status_code": None,
            "location": None,
            "motivo": f"Timeout tras {TIMEOUT_HTTP}s — no se pudo verificar",
        }
    except Exception as e:
        return {
            "estado": "UNKNOWN",
            "status_code": None,
            "location": None,
            "motivo": f"Error al conectar: {e}",
        }


def analizar_hsts(hdrs: dict) -> dict:
    """Analiza la cabecera Strict-Transport-Security del documento principal."""
    hsts_raw = hdrs.get("strict-transport-security")
    if not hsts_raw:
        return {
            "presente": False,
            "max_age": None,
            "include_subdomains": False,
            "preload": False,
            "estado": "WARNING",
            "motivo": "Cabecera HSTS ausente — primera visita vulnerable a MITM",
        }

    partes = [p.strip().lower() for p in hsts_raw.split(";")]
    max_age = None
    for p in partes:
        if p.startswith("max-age="):
            try:
                max_age = int(p.split("=")[1])
            except ValueError:
                pass

    include_sub = "includesubdomains" in partes
    preload = "preload" in partes

    if max_age is None:
        return {
            "presente": True,
            "max_age": None,
            "include_subdomains": include_sub,
            "preload": preload,
            "estado": "WARNING",
            "motivo": "HSTS presente pero sin max-age válido",
        }
    if max_age < HSTS_MIN_MAX_AGE:
        return {
            "presente": True,
            "max_age": max_age,
            "include_subdomains": include_sub,
            "preload": preload,
            "estado": "WARNING",
            "motivo": f"HSTS max-age insuficiente ({max_age}s < {HSTS_MIN_MAX_AGE}s recomendados)",
        }
    if not include_sub:
        return {
            "presente": True,
            "max_age": max_age,
            "include_subdomains": False,
            "preload": preload,
            "estado": "WARNING",
            "motivo": "HSTS correcto pero sin includeSubDomains — subdominios HTTP vulnerables",
        }
    return {
        "presente": True,
        "max_age": max_age,
        "include_subdomains": include_sub,
        "preload": preload,
        "estado": "PASSED",
        "motivo": "HSTS correctamente configurado"
        + (" con preload" if preload else ""),
    }


def evaluar_r17(sitio: str, hdrs_doc: dict) -> dict:
    redirect = verificar_redirect_http(sitio)
    hsts     = analizar_hsts(hdrs_doc)

    # Veredicto global R17: el peor de los dos
    orden = ["FAILED", "UNKNOWN", "WARNING", "PASSED"]
    veredicto = min(
        [redirect["estado"], hsts["estado"]],
        key=lambda x: orden.index(x) if x in orden else 0,
    )

    return {
        "veredicto": veredicto,
        "redirect_http": redirect,
        "hsts": hsts,
    }


# ── R18 ───────────────────────────────────────────────────────────────────────
def analizar_csp(hdrs: dict) -> dict:
    csp_raw    = hdrs.get("content-security-policy")
    csp_ro_raw = hdrs.get("content-security-policy-report-only")

    # Sin CSP de ningún tipo
    if not csp_raw and not csp_ro_raw:
        return {
            "veredicto": "FAILED",
            "modo": None,
            "valor": None,
            "directivas_problematicas": [],
            "directivas_faltantes": [],
            "motivo": "Cabecera Content-Security-Policy ausente",
        }

    # Solo en modo Report-Only (observa pero no bloquea)
    if not csp_raw and csp_ro_raw:
        return {
            "veredicto": "WARNING",
            "modo": "report-only",
            "valor": csp_ro_raw,
            "directivas_problematicas": [],
            "directivas_faltantes": [],
            "motivo": "CSP solo en modo Report-Only — no se aplica, no bloquea nada",
        }

    # CSP presente — analizar directivas
    valor = csp_raw
    directivas = {}
    for parte in valor.split(";"):
        tokens = parte.strip().split()
        if tokens:
            directivas[tokens[0].lower()] = " ".join(tokens[1:])

    problematicas = []
    faltantes     = []

    # Comprobar directivas críticas en script-src o default-src
    src_efectiva = directivas.get("script-src") or directivas.get("default-src") or ""

    if "'unsafe-inline'" in src_efectiva:
        problematicas.append({
            "directiva": "script-src / default-src",
            "valor": "'unsafe-inline'",
            "riesgo": "Permite scripts inline — anula protección XSS",
        })
    if "'unsafe-eval'" in src_efectiva:
        problematicas.append({
            "directiva": "script-src / default-src",
            "valor": "'unsafe-eval'",
            "riesgo": "Permite eval() — vía de ejecución de código arbitrario",
        })
    if " * " in f" {src_efectiva} " or src_efectiva.strip() == "*":
        problematicas.append({
            "directiva": "script-src / default-src",
            "valor": "*",
            "riesgo": "Wildcard — permite scripts de cualquier origen",
        })

    # Comprobar directivas importantes que faltan
    if "script-src" not in directivas and "default-src" not in directivas:
        faltantes.append({
            "directiva": "script-src / default-src",
            "impacto": "Sin restricción de origen para scripts",
        })
    if "frame-ancestors" not in directivas:
        faltantes.append({
            "directiva": "frame-ancestors",
            "impacto": "Sin protección contra clickjacking mediante iframes",
        })

    # Veredicto
    if problematicas or faltantes:
        motivos = (
            [f"{p['valor']} en {p['directiva']}" for p in problematicas]
            + [f"falta {f['directiva']}" for f in faltantes]
        )
        veredicto = "WARNING"
        motivo    = "CSP presente pero débil: " + "; ".join(motivos)
    else:
        veredicto = "PASSED"
        motivo    = "CSP correctamente configurada"

    return {
        "veredicto": veredicto,
        "modo": "enforcing",
        "valor": valor,
        "directivas_problematicas": problematicas,
        "directivas_faltantes": faltantes,
        "motivo": motivo,
    }


# ── Salida por consola ────────────────────────────────────────────────────────
def imprimir(resultado: dict, detalle: bool) -> None:
    sitio = resultado.get("sitio", "desconocido")
    r17   = resultado["r17"]
    r18   = resultado["r18"]

    print(f"\n{'='*62}")
    print(f"  R17 + R18 — Seguridad en comunicaciones")
    print(f"  Sitio: {sitio}")
    print(f"{'='*62}")

    # R17
    v17 = r17["veredicto"]
    print(f"\nR17 — Redirección HTTPS: {ICONO[v17]} {v17}")
    if detalle:
        red = r17["redirect_http"]
        hsts = r17["hsts"]
        sc   = f"[{red['status_code']}] " if red["status_code"] else ""
        print(f"  Redirect HTTP : {ICONO[red['estado']]} {sc}{red['motivo']}")
        if red.get("location"):
            print(f"                  → {red['location']}")
        print(f"  HSTS          : {ICONO[hsts['estado']]} {hsts['motivo']}")
        if hsts["presente"]:
            sub  = "✓" if hsts["include_subdomains"] else "✗"
            pre  = "✓" if hsts["preload"] else "✗"
            print(f"                  max-age={hsts['max_age']}s  "
                  f"includeSubDomains={sub}  preload={pre}")

    # R18
    v18 = r18["veredicto"]
    print(f"\nR18 — Content Security Policy: {ICONO[v18]} {v18}")
    if detalle:
        print(f"  {r18['motivo']}")
        if r18.get("modo"):
            print(f"  Modo: {r18['modo']}")
        for p in r18.get("directivas_problematicas", []):
            print(f"  ⚠️   {p['valor']} en {p['directiva']}: {p['riesgo']}")
        for f in r18.get("directivas_faltantes", []):
            print(f"  ⚠️   Falta '{f['directiva']}': {f['impacto']}")
        if r18.get("valor") and v18 != "FAILED":
            print(f"\n  Valor CSP:\n  {r18['valor'][:200]}"
                  + ("..." if len(r18.get("valor", "")) > 200 else ""))

    print()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    args    = sys.argv[1:]
    detalle = "--no-detalle" not in args
    args    = [a for a in args if a != "--no-detalle"]

    # Resolver ruta al HAR
    if args:
        ruta = Path(args[0])
        har_path = ruta if ruta.suffix == ".har" else ruta / "requests.har"
    else:
        har_path = WEC_OUTPUT_DEFAULT / "requests.har"

    if not har_path.exists():
        print(f"[ERROR] No se encontró {har_path}", file=sys.stderr)
        sys.exit(1)

    with open(har_path, encoding="utf-8") as f:
        har = json.load(f)

    entries = har["log"]["entries"]
    doc     = entrada_principal(entries)

    if not doc:
        print("[ERROR] No se encontró ninguna entrada de tipo 'document' en el HAR.",
              file=sys.stderr)
        sys.exit(1)

    sitio    = dominio_base(doc["request"]["url"])
    hdrs_doc = cabeceras_dict(doc)

    print(f"[*] Analizando {sitio} ...")
    print(f"[*] Verificando redirección HTTP en vivo ...")

    resultado = {
        "sitio":   sitio,
        "r17":     evaluar_r17(sitio, hdrs_doc),
        "r18":     analizar_csp(hdrs_doc),
    }

    # Guardar JSON
    RESULTADO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f"[✓] Resultado guardado en {RESULTADO_PATH}")

    imprimir(resultado, detalle)


if __name__ == "__main__":
    main()
