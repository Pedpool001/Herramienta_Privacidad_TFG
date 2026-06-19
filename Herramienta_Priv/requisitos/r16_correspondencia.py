"""
R16 - Correspondencia entre el aviso y la ejecución
(RGPD Art. 13/14 / Art. 5.1.a)

Verifica que los terceros a quienes el sitio envía datos en la realidad técnica
estén declarados (o sean identificables como filiales de entidades declaradas)
en la política de privacidad.

Fuentes de datos:
  - PoliGraph graph-original.full.yml: actores ACTOR declarados en la política
  - webXray 3p_domains.csv: dominios de terceros con propietario y linaje corporativo
  - DuckDuckGo Tracker Radar (domains/): resolución de propietario cuando
    webXray no tiene el dato (dominio desconocido)

Lógica:
  1. Extrae los actores declarados en la política de privacidad (PoliGraph).
  2. Carga los dominios de terceros detectados por webXray con sus propietarios.
  3. Para dominios sin propietario en webXray, consulta DuckDuckGo Tracker Radar.
  4. Aplica coincidencia de tokens significativos entre cada propietario y los
     actores declarados (empresa "declarada" si alguna palabra ≥4 chars aparece
     en el nombre de algún actor de la política).
  5. Los dominios cuyo propietario no tiene coincidencia con ningún actor
     declarado = discrepancia (el sitio envía datos a entidades no comunicadas).

Veredicto:
  PASSED  — Todos los dominios de terceros tienen propietario declarado
            (o no identificable = sin owner en DDG, podría ser infraestructura)
  WARNING — Solo dominios sin propietario identificable quedan sin declarar
            (no se puede confirmar que sean data processors)
  FAILED  — Al menos un dominio tiene propietario identificado (en webXray o DDG)
            que NO aparece en la política de privacidad

Uso:
  python3 r16_correspondencia.py                                # rutas por defecto
  python3 r16_correspondencia.py /ruta/poligraph/ /ruta/webxray/reports/sitio/
  python3 r16_correspondencia.py /ruta/graph.full.yml /ruta/3p_domains.csv
  python3 r16_correspondencia.py /ruta/poligraph/ /ruta/webxray/ --no-detalle
"""

import os
import sys
import csv
import json
import re
import yaml
from pathlib import Path

# ── Rutas por defecto ─────────────────────────────────────────────────────────
# r16_correspondencia.py → analysis_scripts/ → privacy-pioneer-web-crawler/ → TFG root
BASE = Path(__file__).resolve().parents[2]

POLIGRAPH_DEFAULT = BASE / "PoliGraph/example/elpais_audit"
WEBXRAY_DEFAULT   = BASE / "webXray/reports/auditoria_ejemplo"
# tracker-radar es un directorio hermano del TFG; también configurable via env var
DDG_BASE = Path(os.getenv("TRACKER_RADAR_DIR", str(BASE.parent / "tracker-radar")))

RESULTADO_PATH = Path(__file__).resolve().parents[1] / "analysis_data/r16_resultado.json"

ICONO = {"PASSED": "✅", "WARNING": "⚠️ ", "FAILED": "❌"}

# Directorios de DDG en orden de preferencia
DDG_COUNTRIES = ["US", "GB", "DE", "FR", "AU", "CA", "NL", "NO", "CH"]

# Palabras genéricas que no deben usarse como tokens de matching
_STOPWORDS = {
    "inc", "llc", "ltd", "corp", "gmbh", "s.a", "the", "and", "for",
    "com", "net", "org", "group", "media", "tech", "data", "digital",
    "interactive", "online", "global", "systems", "services", "solutions",
    "international", "holding", "holdings", "platform", "platforms",
}


# ── Normalización y matching de entidades ─────────────────────────────────────

def tokenizar(texto: str) -> set[str]:
    """Extrae tokens significativos de un nombre de entidad."""
    texto = texto.lower()
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    tokens = {t for t in texto.split() if len(t) >= 4 and t not in _STOPWORDS}
    return tokens


def construir_tokens_actores(actores: list[str]) -> list[tuple[str, set[str]]]:
    """Devuelve lista de (actor_original, tokens) para todos los actores."""
    resultado = []
    for a in actores:
        t = tokenizar(a)
        if t:
            resultado.append((a, t))
    return resultado


def esta_declarado(nombres_entidad: list[str], tokens_actores: list[tuple]) -> tuple[bool, str]:
    """
    Comprueba si algún nombre de la entidad (nombre + displayName + linaje)
    tiene al menos un token significativo en común con algún actor de la política.

    Devuelve (found: bool, actor_que_coincide: str).
    """
    entity_tokens = set()
    for nombre in nombres_entidad:
        entity_tokens |= tokenizar(nombre)

    if not entity_tokens:
        return False, ""

    for actor_orig, actor_tokens in tokens_actores:
        if entity_tokens & actor_tokens:
            return True, actor_orig

    return False, ""


# ── Carga de datos ─────────────────────────────────────────────────────────────

def cargar_actores_poligraph(ruta_yml: Path) -> list[str]:
    """Extrae los actores ACTOR del grafo YAML de PoliGraph."""
    with open(ruta_yml, encoding="utf-8") as f:
        grafo = yaml.safe_load(f)
    actores = [
        n["id"]
        for n in grafo.get("nodes", [])
        if n.get("type") == "ACTOR" and n.get("id") not in ("UNSPECIFIED_ACTOR", "we")
    ]
    return actores


def cargar_3p_webxray(ruta_csv: Path) -> list[dict]:
    """
    Lee 3p_domains.csv de webXray.
    Columnas: percent_total, domain, owner, owner_country, owner_lineage
    """
    dominios = []
    if not Path(ruta_csv).is_file():
        return dominios
    with open(ruta_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            dominios.append({
                "domain":   row.get("domain", "").strip(),
                "owner":    row.get("owner", "").strip(),
                "lineage":  row.get("owner_lineage", "").strip(),
                "country":  row.get("owner_country", "").strip(),
            })
    return dominios


def resolver_propietario_ddg(dominio: str) -> tuple[str, str]:
    """
    Busca el dominio en los ficheros de DuckDuckGo Tracker Radar.
    Devuelve (name, displayName) o ('', '') si no se encuentra.
    """
    if not DDG_BASE.exists():
        return "", ""
    for pais in DDG_COUNTRIES:
        ruta = DDG_BASE / "domains" / pais / f"{dominio}.json"
        if ruta.exists():
            try:
                with open(ruta, encoding="utf-8") as f:
                    data = json.load(f)
                owner = data.get("owner", {})
                return owner.get("name", ""), owner.get("displayName", "")
            except (json.JSONDecodeError, KeyError):
                pass
    return "", ""


# ── Análisis principal ────────────────────────────────────────────────────────

def analizar(actores: list[str], dominios_3p: list[dict]) -> dict:
    tokens_actores = construir_tokens_actores(actores)

    declarados   = []
    no_declarados_identificados   = []   # tienen owner → FAILED
    no_declarados_sin_owner       = []   # no owner en ninguna fuente → WARNING

    for entrada in dominios_3p:
        dominio  = entrada["domain"]
        owner    = entrada["owner"]
        lineage  = entrada["lineage"]

        # Construir lista de nombres para matching
        nombres = []
        if owner:
            nombres.append(owner)
        # Linaje: "Adobe Audience Manager > Adobe Experience Cloud > Adobe Systems"
        if lineage:
            for parte in lineage.split(">"):
                parte = parte.strip()
                if parte and parte not in nombres:
                    nombres.append(parte)

        # Si webXray no tiene owner, consultar DDG
        ddg_name = ddg_display = ""
        if not nombres:
            ddg_name, ddg_display = resolver_propietario_ddg(dominio)
            if ddg_name:
                nombres.append(ddg_name)
            if ddg_display and ddg_display != ddg_name:
                nombres.append(ddg_display)

        if not nombres:
            # Sin owner en ninguna fuente: podría ser infraestructura (CDN, etc.)
            no_declarados_sin_owner.append({
                "dominio":  dominio,
                "fuente":   "ninguna",
                "nombres":  [],
            })
            continue

        encontrado, actor_coincidente = esta_declarado(nombres, tokens_actores)

        if encontrado:
            declarados.append({
                "dominio":          dominio,
                "nombres_entidad":  nombres,
                "actor_declarado":  actor_coincidente,
                "fuente_owner":     "ddg" if (not owner and ddg_name) else "webxray",
            })
        else:
            no_declarados_identificados.append({
                "dominio":         dominio,
                "nombres_entidad": nombres,
                "fuente_owner":    "ddg" if (not owner and ddg_name) else "webxray",
            })

    return {
        "actores_declarados":           actores,
        "dominios_analizados":          len(dominios_3p),
        "declarados":                   declarados,
        "no_declarados_identificados":  no_declarados_identificados,
        "no_declarados_sin_owner":      no_declarados_sin_owner,
    }


def evaluar(hallazgos: dict) -> dict:
    nd_id  = hallazgos["no_declarados_identificados"]
    nd_no  = hallazgos["no_declarados_sin_owner"]
    total  = hallazgos["dominios_analizados"]
    decl   = len(hallazgos["declarados"])

    if nd_id:
        propietarios = list({
            (n["nombres_entidad"][0] if n["nombres_entidad"] else "?")
            for n in nd_id
        })
        propietarios_str = ", ".join(propietarios[:4])
        if len(propietarios) > 4:
            propietarios_str += f" (+{len(propietarios)-4} más)"
        return {
            "estado": "FAILED",
            "detalle": (
                f"De {total} dominios de terceros, {len(nd_id)} tienen propietario "
                f"identificado que NO está declarado en la política de privacidad: "
                f"{propietarios_str}. "
                f"RGPD Art. 13/14 exige informar de todos los destinatarios."
            ),
        }

    if nd_no:
        return {
            "estado": "WARNING",
            "detalle": (
                f"De {total} dominios de terceros, {decl} tienen propietario declarado "
                f"en la política. Otros {len(nd_no)} dominios no tienen propietario "
                f"identificable en webXray ni en DuckDuckGo Tracker Radar "
                f"({', '.join(e['dominio'] for e in nd_no[:3])}{'...' if len(nd_no)>3 else ''}); "
                f"podrían ser infraestructura/CDN sin rol de responsable de datos."
            ),
        }

    return {
        "estado": "PASSED",
        "detalle": (
            f"Los {total} dominios de terceros tienen propietario declarado en "
            f"la política de privacidad. Correspondencia técnica verificada."
        ),
    }


# ── Salida por consola ────────────────────────────────────────────────────────

def imprimir(resultado: dict, detalle: bool) -> None:
    sitio     = resultado.get("sitio", "—")
    veredicto = resultado["veredicto"]
    ev        = resultado["evaluacion"]
    hallazgos = resultado["hallazgos"]

    print(f"\n{'='*64}")
    print(f"  R16 — Correspondencia entre aviso y ejecución")
    print(f"  Sitio    : {sitio}")
    print(f"  Veredicto: {ICONO[veredicto]} {veredicto}")
    print(f"{'='*64}")
    print(f"\n{ev['detalle']}")

    if not detalle:
        print()
        return

    print(f"\n{'─'*64}")
    actores = hallazgos.get("actores_declarados", [])
    print(f"Actores declarados en la política ({len(actores)}):\n")
    for a in actores[:12]:
        print(f"    {a}")
    if len(actores) > 12:
        print(f"    … ({len(actores)-12} más)")

    nd_id = hallazgos.get("no_declarados_identificados", [])
    if nd_id:
        print(f"\n{'─'*64}")
        print(f"❌ Dominios con propietario NO declarado ({len(nd_id)}):\n")
        for e in nd_id:
            propietario = e["nombres_entidad"][0] if e["nombres_entidad"] else "?"
            fuente      = f"[{e['fuente_owner']}]"
            print(f"    {e['dominio']:<30} → {propietario}  {fuente}")

    declarados = hallazgos.get("declarados", [])
    if declarados and detalle:
        print(f"\n{'─'*64}")
        print(f"✅ Dominios con propietario declarado ({len(declarados)}):\n")
        for e in declarados:
            propietario = e["nombres_entidad"][0] if e["nombres_entidad"] else "?"
            actor       = e.get("actor_declarado", "")
            fuente      = f"[{e['fuente_owner']}]"
            print(f"    {e['dominio']:<30} → {propietario}  {fuente}")
            if actor:
                print(f"    {'':30}   coincide con: «{actor[:50]}»")

    nd_no = hallazgos.get("no_declarados_sin_owner", [])
    if nd_no:
        print(f"\n{'─'*64}")
        print(f"⚪ Dominios sin propietario identificable ({len(nd_no)}):\n")
        for e in nd_no:
            print(f"    {e['dominio']}")

    print()


# ── Resolución de rutas CLI ───────────────────────────────────────────────────

def resolver_ruta_poligraph(ruta_arg: str) -> Path:
    p = Path(ruta_arg)
    if p.is_dir():
        candidatos = [
            p / "graph-original.full.yml",
            p / "graph-original.yml",
        ]
        for c in candidatos:
            if c.exists():
                return c
        raise FileNotFoundError(f"No se encontró graph-original.full.yml en {p}")
    return p


def resolver_ruta_webxray(ruta_arg: str) -> Path:
    p = Path(ruta_arg)
    if p.is_dir():
        candidato = p / "3p_domains.csv"
        if candidato.exists():
            return candidato
        raise FileNotFoundError(f"No se encontró 3p_domains.csv en {p}")
    return p


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args    = sys.argv[1:]
    detalle = "--no-detalle" not in args
    args    = [a for a in args if a != "--no-detalle"]

    if len(args) >= 2:
        ruta_poligraph_arg = args[0]
        ruta_webxray_arg   = args[1]
    elif len(args) == 1:
        ruta_poligraph_arg = args[0]
        ruta_webxray_arg   = str(WEBXRAY_DEFAULT)
    else:
        ruta_poligraph_arg = str(POLIGRAPH_DEFAULT)
        ruta_webxray_arg   = str(WEBXRAY_DEFAULT)

    try:
        yml_path = resolver_ruta_poligraph(ruta_poligraph_arg)
        csv_path = resolver_ruta_webxray(ruta_webxray_arg)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[*] PoliGraph: {yml_path}")
    print(f"[*] webXray  : {csv_path}")
    print(f"[*] DDG base : {DDG_BASE}  ({'encontrado' if DDG_BASE.exists() else 'no encontrado'})")

    actores    = cargar_actores_poligraph(yml_path)
    dominios   = cargar_3p_webxray(csv_path)

    print(f"[*] Actores declarados en la política: {len(actores)}")
    print(f"[*] Dominios de terceros detectados  : {len(dominios)}")

    hallazgos  = analizar(actores, dominios)
    evaluacion = evaluar(hallazgos)
    veredicto  = evaluacion["estado"]

    sitio = csv_path.parent.name

    resultado = {
        "sitio":      sitio,
        "veredicto":  veredicto,
        "evaluacion": evaluacion,
        "hallazgos":  hallazgos,
    }

    RESULTADO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f"[✓] Resultado guardado en {RESULTADO_PATH}")

    imprimir(resultado, detalle)


if __name__ == "__main__":
    main()
