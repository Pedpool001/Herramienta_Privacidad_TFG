"""
R15 - Identificación de Responsables (RGPD Art. 13/14)
Asocia cada cookie capturada con su propietario usando tres fuentes:
  1. Open Cookie Database (por nombre exacto y wildcard)
  2. Privacy Pioneer DB (por dominio de la cookie)
  3. "No identificado" si ninguna fuente lo resuelve

Opcionalmente verifica, mediante la salida de PoliGraph, si el propio
sitio web identifica claramente a su responsable del tratamiento en la
política de privacidad (nombre legal, dirección, NIF/CIF).
"""

import os
import re
import json
import sys
import fnmatch
import mysql.connector
import yaml
from pathlib import Path
from collections import defaultdict

# ── Configuración ─────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("MYSQL_HOST",     "localhost"),
    "user":     os.getenv("MYSQL_USER",     "pioneer"),
    "password": os.getenv("MYSQL_PASSWORD", "abc"),
    "database": os.getenv("MYSQL_DATABASE", "analysis"),
}

_TFG_DIR = Path(__file__).resolve().parents[2]
OCD_PATH = _TFG_DIR / "Herramienta_Priv/assets/open-cookie-database.json"

REPORTE_PATH = _TFG_DIR / "privacy-pioneer-web-crawler/analysis_data/reporte_auditoria.json"

# ── Carga de fuentes ──────────────────────────────────────────────────────────

def cargar_ocd() -> dict:
    """Carga Open Cookie Database. Devuelve {nombre: [{dataController, category, platform, wildcard}]}"""
    with open(OCD_PATH, encoding="utf-8") as f:
        return json.load(f)


def cargar_dominio_empresa_pp() -> dict:
    """Construye {dominio: empresa} a partir de los requestUrl de Privacy Pioneer."""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT DISTINCT requestUrl, parentCompany FROM entries "
        "WHERE parentCompany IS NOT NULL AND parentCompany NOT IN ('Unknown', '')"
    )
    filas = cursor.fetchall()
    cursor.close()
    conn.close()

    mapping = {}
    for fila in filas:
        dominio = extraer_dominio(fila["requestUrl"])
        if dominio:
            mapping[dominio] = fila["parentCompany"]
    return mapping


def extraer_responsable_poligraph(ruta_yml: str) -> dict:
    """
    Extrae el responsable del tratamiento identificado en la política de privacidad.
    Sigue la cadena SUBSUM desde 'we', 'joint controller' o 'vgms' para encontrar
    la entidad legal concreta (nombre, dirección, NIF/CIF).
    """
    with open(ruta_yml, encoding="utf-8") as f:
        texto_raw = f.read()

    datos = yaml.safe_load(texto_raw)

    # Construir mapa SUBSUM: {source: [{entidad, texto_cita}]}
    subsum = {}
    for link in datos.get("links", []):
        if link["key"] == "SUBSUM":
            src = link["source"]
            tgt = link["target"]
            cita = (link.get("text") or [""])[0]
            subsum.setdefault(src, []).append({"entidad": tgt, "texto_cita": cita})

    # Nodos desde los que PoliGraph representa al responsable del sitio
    NODOS_RESPONSABLE = ("we", "joint controller", "vgms")
    entidades = []
    vistas = set()
    for nodo in NODOS_RESPONSABLE:
        for item in subsum.get(nodo, []):
            entidad = item["entidad"]
            if entidad in ("UNSPECIFIED_ACTOR", "UNSPECIFIED_DATA") or entidad in vistas:
                continue
            vistas.add(entidad)
            entidades.append({
                "entidad": entidad,
                "via": nodo,
                "texto_cita": item["texto_cita"][:300] if item["texto_cita"] else "",
            })

    # Patrones de identificación suficiente según RGPD Art. 13
    pat_nif = re.compile(r'\b[A-Z]\d{8}\b|\b\d{8}[A-Z]\b')
    palabras_direccion = ("address", "calle", "c/", "dirección", "madrid",
                          "barcelona", "bis,", "street", "avenue", "avda")

    for e in entidades:
        t = e["texto_cita"]
        e["tiene_nif"]       = bool(pat_nif.search(t))
        e["tiene_direccion"] = any(p in t.lower() for p in palabras_direccion)

    return {"entidades": entidades}


# ── Helpers ───────────────────────────────────────────────────────────────────

def extraer_dominio(url: str) -> str:
    """Extrae dominio base (ej: '.doubleclick.net' → 'doubleclick.net')."""
    url = url.lstrip(".")
    match = re.search(r'(?:https?://)?(?:www\.)?([^/\s?#]+)', url)
    return match.group(1).lower() if match else ""


def normalizar_dominio_cookie(dominio: str) -> str:
    return dominio.lstrip(".").lower()


def buscar_en_ocd(nombre: str, ocd: dict) -> dict | None:
    """
    Busca el nombre exacto primero; si no, prueba entradas wildcard.
    Devuelve el primer match o None.
    """
    # Exacto
    if nombre in ocd:
        return ocd[nombre][0]

    # Wildcard (ej: AMCVS_* coincide con AMCVS_9854...)
    for patron, entradas in ocd.items():
        if "*" in patron and fnmatch.fnmatch(nombre, patron):
            return entradas[0]

    return None


def buscar_en_pp(dominio_cookie: str, mapa_pp: dict) -> str | None:
    """Busca la empresa por dominio usando el mapa de Privacy Pioneer."""
    dom = normalizar_dominio_cookie(dominio_cookie)
    # Coincidencia exacta
    if dom in mapa_pp:
        return mapa_pp[dom]
    # Coincidencia por sufijo (ej: 'x.doubleclick.net' en mapa que tiene 'doubleclick.net')
    for mapa_dom, empresa in mapa_pp.items():
        if dom.endswith(mapa_dom) or mapa_dom.endswith(dom):
            return empresa
    return None



# ── Identificación de cookies ─────────────────────────────────────────────────

def es_dominio_propio(dominio_cookie: str, dominio_sitio: str) -> bool:
    """Devuelve True si la cookie pertenece al propio sitio o a un subdominio suyo."""
    dom = normalizar_dominio_cookie(dominio_cookie)
    return dom == dominio_sitio or dom.endswith("." + dominio_sitio)


def identificar_cookies(cookies: list, ocd: dict, mapa_pp: dict,
                        dominio_sitio: str = "") -> list[dict]:
    resultados = []
    for c in cookies:
        nombre = c.get("name", "")
        dominio = c.get("domain", "")

        # 1. Open Cookie Database
        match_ocd = buscar_en_ocd(nombre, ocd)
        if match_ocd:
            propietario = match_ocd.get("dataController", "No identificado")
            plataforma  = match_ocd.get("platform", "")
            categoria   = match_ocd.get("category", "")
            fuente      = "Open Cookie Database"
        else:
            # 2. Privacy Pioneer por dominio
            empresa_pp = buscar_en_pp(dominio, mapa_pp)
            if empresa_pp:
                propietario = empresa_pp
                plataforma  = ""
                categoria   = ""
                fuente      = "Privacy Pioneer (dominio)"
            # 3. Dominio propio del sitio
            elif dominio_sitio and es_dominio_propio(dominio, dominio_sitio):
                propietario = "Sitio propio"
                plataforma  = ""
                categoria   = ""
                fuente      = "Dominio propio"
            else:
                propietario = "No identificado"
                plataforma  = ""
                categoria   = ""
                fuente      = "—"

        resultados.append({
            "nombre":      nombre,
            "dominio":     dominio,
            "propietario": propietario,
            "plataforma":  plataforma,
            "categoria":   categoria,
            "fuente":      fuente,
            "expiry":      c.get("expiry"),
        })

    return resultados


# ── Auditoría R15 ─────────────────────────────────────────────────────────────

def evaluar_responsable_poligraph(info: dict) -> dict:
    """
    Decide si el responsable del tratamiento está suficientemente identificado.
      PASS        → entidad concreta con NIF o dirección
      ADVERTENCIA → entidad concreta pero sin datos de contacto completos
      FAIL        → no se encontró ninguna entidad identificable
    """
    entidades = info["entidades"]
    if not entidades:
        return {
            "estado": "FAIL",
            "motivo": "La política no identifica ningún responsable del tratamiento.",
            "entidades": [],
        }

    completas = [e for e in entidades if e["tiene_nif"] or e["tiene_direccion"]]
    if completas:
        nombres = [e["entidad"] for e in completas]
        return {
            "estado": "PASS",
            "motivo": f"Responsable identificado con datos suficientes: {', '.join(nombres)}",
            "entidades": entidades,
        }

    nombres = [e["entidad"] for e in entidades]
    return {
        "estado": "ADVERTENCIA",
        "motivo": f"Responsable mencionado pero sin NIF ni dirección clara: {', '.join(nombres)}",
        "entidades": entidades,
    }


def auditar_r15(ruta_poligraph: str = None) -> dict:
    reporte = json.loads(REPORTE_PATH.read_text(encoding="utf-8"))
    ocd = cargar_ocd()
    mapa_pp = cargar_dominio_empresa_pp()

    resultado_global = {}

    for sitio, fases in reporte.items():
        # Derivar el dominio del sitio desde la clave (abc_es → abc.es)
        dominio_sitio = sitio.replace("_", ".")

        sitio_result = {}
        for fase, cookies in fases.items():
            cookies_identificadas = identificar_cookies(cookies, ocd, mapa_pp, dominio_sitio)

            # "Sitio propio" cuenta como identificada; solo falla lo verdaderamente desconocido
            no_identificadas = [c for c in cookies_identificadas if c["propietario"] == "No identificado"]
            identificadas    = [c for c in cookies_identificadas if c["propietario"] != "No identificado"]

            sitio_result[fase] = {
                "estado": "PASS" if not no_identificadas else "FAIL",
                "total": len(cookies_identificadas),
                "identificadas": len(identificadas),
                "no_identificadas": len(no_identificadas),
                "cookies": cookies_identificadas,
            }

        # Verificación PoliGraph a nivel de sitio (única por sitio, no por fase)
        if ruta_poligraph:
            info = extraer_responsable_poligraph(ruta_poligraph)
            sitio_result["responsable_en_politica"] = evaluar_responsable_poligraph(info)

        resultado_global[sitio] = sitio_result

    return resultado_global


# ── Salida por consola ────────────────────────────────────────────────────────

def imprimir_reporte(resultado: dict, mostrar_detalle: bool = True):
    print("\n" + "=" * 65)
    print("       AUDITORÍA R15 — IDENTIFICACIÓN DE RESPONSABLES")
    print("=" * 65)

    for sitio, fases in resultado.items():
        print(f"\n{'─'*65}")
        print(f"  Sitio: {sitio}")

        # Verificación del responsable (PoliGraph) — a nivel de sitio
        if "responsable_en_politica" in fases:
            rp = fases["responsable_en_politica"]
            iconos_rp = {"PASS": "✅", "ADVERTENCIA": "⚠️ ", "FAIL": "❌"}
            print(f"\n  [RESPONSABLE EN POLÍTICA] {iconos_rp.get(rp['estado'], '?')} {rp['estado']}")
            print(f"    {rp['motivo']}")
            if mostrar_detalle and rp["entidades"]:
                for e in rp["entidades"]:
                    nif = "✓ NIF" if e["tiene_nif"] else "✗ NIF"
                    dir_ = "✓ Dirección" if e["tiene_direccion"] else "✗ Dirección"
                    print(f"    → {e['entidad']}  [{nif} | {dir_}]")
                    if e["texto_cita"]:
                        print(f"      \"{e['texto_cita'][:120]}...\"")

        for fase, datos in fases.items():
            if fase == "responsable_en_politica":
                continue
            icono = "✅" if datos["estado"] == "PASS" else "❌"
            print(f"\n  [{fase}] {icono} {datos['estado']}  "
                  f"({datos['identificadas']}/{datos['total']} identificadas)")

            if mostrar_detalle:
                for c in datos["cookies"]:
                    if c["propietario"] == "No identificado":
                        icono_c = "✗"
                    elif c["propietario"] == "Sitio propio":
                        icono_c = "🏠"
                    else:
                        icono_c = "✓"
                    linea = f"    {icono_c} {c['nombre'][:35]:<35} → {c['propietario']}"
                    if c.get("plataforma"):
                        linea += f"  [{c['plataforma']}]"
                    print(linea)

    print(f"\n{'='*65}")
    total_sitios = sum(len(fases) for fases in resultado.values())
    pass_count = sum(
        1 for fases in resultado.values()
        for datos in fases.values()
        if datos["estado"] == "PASS"
    )
    print(f"RESUMEN: {total_sitios} auditorías — ✅ {pass_count} PASS | ❌ {total_sitios - pass_count} FAIL")
    print("=" * 65)


def guardar_json(resultado: dict, ruta: str = str(Path(__file__).resolve().parents[1] / "analysis_data/r15_resultado.json")):
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f"\n💾 Resultado guardado en: {ruta}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ruta_pg = None
    detalle = True

    for arg in sys.argv[1:]:
        if arg.endswith(".yml") or arg.endswith(".yaml"):
            ruta_pg = arg
        elif arg == "--no-detalle":
            detalle = False

    if ruta_pg:
        print(f"📄 Usando PoliGraph: {ruta_pg}")
    else:
        print("ℹ️  Sin PoliGraph (pasa la ruta del .yml para cruzar con la política)")

    resultado = auditar_r15(ruta_poligraph=ruta_pg)
    imprimir_reporte(resultado, mostrar_detalle=detalle)
    guardar_json(resultado)
