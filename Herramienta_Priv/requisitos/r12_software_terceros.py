"""
R12 - Software de Terceros (RGPD / Privacy by Design)
"El responsable debe asegurarse de que las funciones de software comercial
o de terceros que no tengan base jurídica estén desactivadas por defecto."

Combina dos fuentes:
  - Privacy Pioneer DB  : detecta actividad comercial en fase PRE (sin consentimiento)
  - webXray 3p_domains  : confirma que el dominio es un tercero conocido y da propietario

Un sitio FALLA R12 si tiene software de tipo comercial activo en fase PRE.
La presencia en webXray eleva la evidencia a "confirmada por ambas herramientas".
"""

import os
import re
import csv
import json
import sys
from pathlib import Path
from collections import defaultdict

import mysql.connector

# ── Configuración ─────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("MYSQL_HOST", "localhost"),
    "user":     os.getenv("MYSQL_USER", "pioneer"),
    "password": os.getenv("MYSQL_PASSWORD", "abc"),
    "database": os.getenv("MYSQL_DATABASE", "analysis"),
}

# Tipos de PP que requieren consentimiento explícito (no exentos)
TIPOS_CRITICOS = {
    "advertising":    "Publicidad comportamental",
    "analytics":      "Analítica de comportamiento",
    "social":         "Redes sociales",
    "trackingPixel":  "Píxel de seguimiento",
    "fingerprinting": "Fingerprinting de dispositivo",
}

# ── Carga de fuentes ──────────────────────────────────────────────────────────

def cargar_webxray(ruta_csv: str) -> dict:
    """
    Lee 3p_domains.csv de webXray.
    Devuelve {dominio: {owner, owner_country, owner_lineage}}.
    """
    mapa = {}
    if not Path(ruta_csv).is_file():
        return mapa
    with open(ruta_csv, newline="", encoding="utf-8") as f:
        for fila in csv.DictReader(f):
            dominio = fila.get("domain", "").strip().strip('"')
            if dominio:
                mapa[dominio] = {
                    "owner":          fila.get("owner", "").strip('"'),
                    "owner_country":  fila.get("owner_country", "").strip('"'),
                    "owner_lineage":  fila.get("owner_lineage", "").strip('"'),
                }
    return mapa


def cargar_pp_pre(sitio_filtro: str = None) -> list[dict]:
    """
    Devuelve las entradas de Privacy Pioneer en fase PRE con tipos comerciales.
    """
    conn   = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    tipos       = list(TIPOS_CRITICOS.keys())
    placeholders = ", ".join(["%s"] * len(tipos))
    where  = f"WHERE consent_phase = 'PRE' AND typ IN ({placeholders})"
    params = tipos[:]

    if sitio_filtro:
        where  += " AND rootUrl LIKE %s"
        params.append(f"%{sitio_filtro}%")

    cursor.execute(
        f"SELECT rootUrl, requestUrl, typ, parentCompany FROM entries {where}",
        params,
    )
    filas = cursor.fetchall()
    cursor.close()
    conn.close()
    return filas


# ── Helpers ───────────────────────────────────────────────────────────────────

def extraer_dominio(url: str) -> str:
    """Extrae el dominio base de una URL (sin www ni path)."""
    url = url.lstrip(".")
    m = re.search(r'(?:https?://)?(?:www\.)?([^/\s?#:]+)', url)
    return m.group(1).lower() if m else ""


def buscar_en_webxray(dominio: str, mapa_wx: dict) -> dict | None:
    """
    Busca el dominio en el mapa de webXray.
    Intenta coincidencia exacta y luego por dominio padre.
    """
    if dominio in mapa_wx:
        return mapa_wx[dominio]
    # Buscar si el dominio es subdominio de alguno del mapa
    for wx_dom, info in mapa_wx.items():
        if dominio.endswith("." + wx_dom):
            return info
    return None


# ── Auditoría R12 ─────────────────────────────────────────────────────────────

def auditar_r12(ruta_3p_domains: str, sitio_filtro: str = None) -> dict:
    mapa_wx  = cargar_webxray(ruta_3p_domains)
    entradas = cargar_pp_pre(sitio_filtro)

    # Agrupar violaciones por sitio
    por_sitio = defaultdict(list)

    for e in entradas:
        sitio       = e["rootUrl"] or "desconocido"
        dominio     = extraer_dominio(e["requestUrl"])
        tipo_pp     = e["typ"]
        empresa_pp  = e["parentCompany"] or "Desconocida"
        descripcion = TIPOS_CRITICOS.get(tipo_pp, tipo_pp)

        info_wx = buscar_en_webxray(dominio, mapa_wx)

        violacion = {
            "dominio":       dominio,
            "tipo_pp":       tipo_pp,
            "descripcion":   descripcion,
            "empresa_pp":    empresa_pp,
            "en_webxray":    info_wx is not None,
            "owner_wx":      info_wx["owner"]         if info_wx else "",
            "pais_wx":       info_wx["owner_country"] if info_wx else "",
            "lineage_wx":    info_wx["owner_lineage"]  if info_wx else "",
            "request_url":   e["requestUrl"][:120],
        }
        por_sitio[sitio].append(violacion)

    # Construir resultado final por sitio
    resultado = {}
    for sitio, violaciones in por_sitio.items():
        # Desduplicar por dominio+tipo para no repetir el mismo tracker N veces
        vistas = set()
        unicas = []
        for v in violaciones:
            clave = (v["dominio"], v["tipo_pp"])
            if clave not in vistas:
                vistas.add(clave)
                unicas.append(v)

        confirmadas = [v for v in unicas if v["en_webxray"]]
        solo_pp     = [v for v in unicas if not v["en_webxray"]]

        resultado[sitio] = {
            "estado":       "FALLO" if unicas else "PASS",
            "total_unicas": len(unicas),
            "confirmadas_ambas_herramientas": len(confirmadas),
            "solo_privacy_pioneer":           len(solo_pp),
            "violaciones":  unicas,
        }

    return resultado


# ── Salida por consola ────────────────────────────────────────────────────────

def imprimir_reporte(resultado: dict, mostrar_detalle: bool = True):
    print("\n" + "=" * 68)
    print("      AUDITORÍA R12 — SOFTWARE DE TERCEROS SIN CONSENTIMIENTO")
    print("=" * 68)

    for sitio, datos in resultado.items():
        icono = "❌" if datos["estado"] == "FALLO" else "✅"
        print(f"\n{'─'*68}")
        print(f"  Sitio : {sitio}")
        print(f"  Estado: {icono} {datos['estado']}")
        print(f"  Rastreadores únicos activos en PRE: {datos['total_unicas']}"
              f"  (✓ confirmados por ambas herramientas: {datos['confirmadas_ambas_herramientas']},"
              f"  solo PP: {datos['solo_privacy_pioneer']})")

        if mostrar_detalle and datos["violaciones"]:
            print()
            # Primero las confirmadas por ambas herramientas
            for v in sorted(datos["violaciones"],
                            key=lambda x: (not x["en_webxray"], x["tipo_pp"])):
                fuente = "🔴 PP + webXray" if v["en_webxray"] else "🟡 Solo PP"
                owner  = f" [{v['owner_wx']}]" if v["owner_wx"] else ""
                pais   = f" ({v['pais_wx']})"  if v["pais_wx"]  else ""
                print(f"    {fuente}  {v['empresa_pp']:<22} "
                      f"tipo={v['tipo_pp']:<14} dominio={v['dominio']}{owner}{pais}")

    print(f"\n{'='*68}")
    total  = len(resultado)
    fallos = sum(1 for d in resultado.values() if d["estado"] == "FALLO")
    print(f"RESUMEN: {total} sitios — ❌ {fallos} FALLO | ✅ {total - fallos} PASS")
    print("=" * 68)


def guardar_json(resultado: dict,
                 ruta: str = str(Path(__file__).resolve().parents[1] / "analysis_data/r12_resultado.json")):
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f"\n💾 Resultado guardado en: {ruta}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _TFG_DIR = Path(__file__).resolve().parents[2]
    RUTA_WX_DEFAULT = str(_TFG_DIR / "webXray/reports/auditoria_ejemplo/3p_domains.csv")

    ruta_wx    = RUTA_WX_DEFAULT
    sitio      = None
    detalle    = True

    for arg in sys.argv[1:]:
        if arg == "--no-detalle":
            detalle = False
        elif arg.endswith(".csv"):
            ruta_wx = arg
        else:
            sitio = arg

    print(f"📂 webXray: {ruta_wx}")
    if sitio:
        print(f"🔍 Filtro de sitio: {sitio}")

    resultado = auditar_r12(ruta_wx, sitio_filtro=sitio)

    if not resultado:
        print("No se encontraron entradas PRE con tipos comerciales en la base de datos.")
    else:
        imprimir_reporte(resultado, mostrar_detalle=detalle)
        guardar_json(resultado)
