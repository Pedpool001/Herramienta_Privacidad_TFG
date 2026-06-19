"""
R19 - Designación y datos de contacto del DPO
(RGPD Art. 37-39 / Art. 13.1.b)

Verifica que la política de privacidad del sitio:
  1. Mencione explícitamente la figura del Delegado de Protección de Datos (DPD/DPO).
  2. Proporcione al menos un método de contacto directo con el DPO
     (dirección de email o dirección postal específica para el DPO).
  3. Opcionalmente: identifique por nombre a la persona que ejerce el rol.

Distinción con R15:
  R15 verificó si los propietarios de cookies de TERCEROS son identificables,
  cruzando datos de rastreadores con bases de datos externas (OCD, Privacy Pioneer).
  R19 verifica si el PROPIO SITIO designa un DPO y publica sus datos de contacto
  en el texto de su política de privacidad, conforme a RGPD Art. 37.7.

Fuente de datos:
  · readability.json  — texto limpio de la política de privacidad (fuente principal)
  · graph-original.full.yml — fragmentos de texto por cláusula (verificación cruzada)

Veredicto:
  PASSED  — DPO mencionado + al menos un contacto (email o dirección postal)
  WARNING — DPO mencionado pero sin datos de contacto específicos
  FAILED  — No se detecta ninguna mención al DPO en la política

Uso:
  python3 r19_dpo.py /ruta/poligraph/output/   # directorio con readability.json
  python3 r19_dpo.py /ruta/readability.json     # fichero directo
  python3 r19_dpo.py /ruta/readability.json --no-detalle
"""

import sys
import json
import re
from pathlib import Path

try:
    import yaml
    from bs4 import BeautifulSoup
except ImportError as e:
    print(f"[ERROR] Dependencia no instalada: {e}")
    print("       pip install pyyaml beautifulsoup4")
    sys.exit(1)

# ── Rutas por defecto ─────────────────────────────────────────────────────────
_TFG_DIR = Path(__file__).resolve().parents[2]
POLIGRAPH_DEFAULT = _TFG_DIR / "PoliGraph/example/elpais_audit"
RESULTADO_PATH = Path(__file__).resolve().parents[1] / "analysis_data/r19_resultado.json"

ICONO = {"PASSED": "✅", "WARNING": "⚠️ ", "FAILED": "❌"}

# ── Patrones de detección ─────────────────────────────────────────────────────
#
# MENCIÓN AL DPO: buscamos las formas habituales en español e inglés.
# Se usa re.IGNORECASE para cubrir variantes de mayúsculas.
PATRON_MENCION = re.compile(
    r"data\s+protection\s+officer"      # inglés completo
    r"|delegado\s+de\s+protecci[oó]n\s+de\s+datos"  # español completo
    r"|\bDPO\b"                          # sigla en inglés
    r"|\bDPD\b"                          # sigla en español
    r"|responsable\s+de\s+protecci[oó]n",  # forma abreviada española
    re.IGNORECASE,
)

# EMAIL DE CONTACTO DEL DPO: detectamos cualquier dirección de email que aparezca
# en el mismo párrafo o frase que una mención al DPO, o cuyo prefijo sugiera
# que pertenece al DPO (dpo@, dpd@, privacidad@, privacy@, dataprotection@…).
# Primero extraemos todos los emails del texto y luego filtramos por contexto.
PATRON_EMAIL = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

# Prefijos de email que sin más contexto ya indican contacto de DPO
PREFIJOS_DPO = re.compile(
    r"^(dpo|dpd|privacy|privacidad|dataprotection|protecciondedatos|"
    r"datos|delegado|gdpr|rgpd|lopd)@",
    re.IGNORECASE,
)

# DIRECCIÓN POSTAL ASOCIADA AL DPO: buscamos frases que mencionen DPO junto
# a palabras clave de dirección postal en un radio de 200 caracteres.
PATRON_POSTAL = re.compile(
    r"(data\s+protection\s+officer|delegado\s+de\s+protecci[oó]n|DPO|DPD)"
    r".{0,200}"
    r"(calle|street|avenue|avenida|plaza|paseo|road|c\.|av\.|"
    r"cod\.?\s*postal|zip\s*code|c\.p\.|cp\s+\d{5}|\d{5}\s+\w+)",
    re.IGNORECASE | re.DOTALL,
)

# NOMBRE DE PERSONA ASOCIADO AL DPO: búsqueda en dos pasos.
# Paso 1: localizar el trigger DPO (con IGNORECASE).
# Paso 2: buscar "is/es/: [Nombre Apellido]" justo después, SIN IGNORECASE
#         para que solo casen letras verdaderamente mayúsculas, no palabras
#         genéricas como "through" o "whom" que se activarían con IGNORECASE.
PATRON_DPO_TRIGGER_NOMBRE = re.compile(
    r"(?:DPO|DPD|data\s+protection\s+officer|delegado\s+de\s+protecci[oó]n)",
    re.IGNORECASE,
)
# Sin re.IGNORECASE: [A-ZÁÉÍÓÚÑ] solo casa letras realmente mayúsculas
PATRON_NOMBRE_TRAS_TRIGGER = re.compile(
    r"(?:is|es|:)\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}"
    r"(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}){1,3})"
)


# ── Carga de datos ────────────────────────────────────────────────────────────
def resolver_ruta(ruta_arg: str) -> tuple[Path, Path | None]:
    """
    Dado un argumento de ruta (directorio o fichero), devuelve las rutas
    a readability.json y opcionalmente a graph-original.full.yml.
    """
    p = Path(ruta_arg)
    if p.is_dir():
        r_json = p / "readability.json"
        g_yml  = p / "graph-original.full.yml"
        return r_json, g_yml if g_yml.exists() else None
    # Si se pasa el fichero directamente, buscamos el YML en el mismo dir
    g_yml = p.parent / "graph-original.full.yml"
    return p, g_yml if g_yml.exists() else None


def extraer_texto(readability_path: Path) -> str:
    """
    Lee readability.json y devuelve el texto limpio de la política.
    PoliGraph guarda el HTML procesado en el campo 'content'; usamos
    BeautifulSoup para quedarnos solo con el texto visible.
    """
    with open(readability_path, encoding="utf-8") as f:
        data = json.load(f)
    html = data.get("content", "") or data.get("textContent", "")
    return BeautifulSoup(html, "html.parser").get_text(separator="\n")


def extraer_textos_grafo(graph_path: Path) -> str:
    """
    Lee graph-original.full.yml y concatena todos los fragmentos de texto
    de los enlaces del grafo. Sirve como verificación cruzada: algunos
    datos de contacto del DPO pueden estar en fragmentos que readability
    no captura completamente.
    """
    with open(graph_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    partes = []
    for link in data.get("links", []):
        textos = link.get("text", [])
        if isinstance(textos, list):
            partes.extend(textos)
        elif isinstance(textos, str):
            partes.append(textos)
    return " ".join(partes)


# ── Análisis ──────────────────────────────────────────────────────────────────
def analizar_texto(texto: str) -> dict:
    """
    Aplica los patrones de detección sobre el texto completo de la política
    y devuelve un diccionario con todos los hallazgos.
    """
    hallazgos = {
        "mencion_dpo":   False,
        "emails_dpo":    [],    # emails con prefijo DPO o en contexto DPO
        "emails_otros":  [],    # otros emails encontrados (para referencia)
        "direccion_postal": False,
        "nombre_dpo":    None,
        "fragmentos":    [],    # fragmentos de texto donde se detectó el DPO
    }

    # ── Mención al DPO ────────────────────────────────────────────────────────
    menciones = list(PATRON_MENCION.finditer(texto))
    if not menciones:
        return hallazgos

    hallazgos["mencion_dpo"] = True

    # Guardar hasta 3 fragmentos de contexto (±150 chars alrededor de la mención)
    for m in menciones[:3]:
        inicio = max(0, m.start() - 150)
        fin    = min(len(texto), m.end() + 150)
        fragmento = texto[inicio:fin].replace("\n", " ").strip()
        hallazgos["fragmentos"].append(fragmento)

    # ── Emails ────────────────────────────────────────────────────────────────
    todos_emails = PATRON_EMAIL.findall(texto)
    for email in todos_emails:
        if PREFIJOS_DPO.match(email):
            # El prefijo del email ya indica que es del DPO
            hallazgos["emails_dpo"].append(email)
        else:
            hallazgos["emails_otros"].append(email)

    # Emails genéricos que aparezcan en el mismo párrafo que una mención al DPO
    parrafos = texto.split("\n\n")
    for parrafo in parrafos:
        if PATRON_MENCION.search(parrafo):
            emails_parrafo = PATRON_EMAIL.findall(parrafo)
            for e in emails_parrafo:
                if e not in hallazgos["emails_dpo"] and e not in hallazgos["emails_otros"]:
                    hallazgos["emails_dpo"].append(e)
                elif e in hallazgos["emails_otros"] and e not in hallazgos["emails_dpo"]:
                    # Promover de "otro" a "dpo" por contexto de párrafo
                    hallazgos["emails_dpo"].append(e)

    # Deduplicar
    hallazgos["emails_dpo"]   = list(dict.fromkeys(hallazgos["emails_dpo"]))
    hallazgos["emails_otros"] = [
        e for e in dict.fromkeys(hallazgos["emails_otros"])
        if e not in hallazgos["emails_dpo"]
    ]

    # ── Dirección postal ──────────────────────────────────────────────────────
    hallazgos["direccion_postal"] = bool(PATRON_POSTAL.search(texto))

    # ── Nombre del DPO ────────────────────────────────────────────────────────
    # Para cada posición donde aparece un trigger DPO, buscamos si justo
    # después hay "is/es/: [NombrePropio]" con letras realmente mayúsculas.
    for m_trigger in PATRON_DPO_TRIGGER_NOMBRE.finditer(texto):
        contexto = texto[m_trigger.end(): m_trigger.end() + 120]
        m_nombre = PATRON_NOMBRE_TRAS_TRIGGER.search(contexto)
        if m_nombre:
            candidato = m_nombre.group(1).strip()
            # Descartar si alguna palabra es un término genérico de política
            palabras_genericas = {"the", "this", "their", "los", "las", "our"}
            palabras = candidato.lower().split()
            if not any(p in palabras_genericas for p in palabras):
                hallazgos["nombre_dpo"] = candidato
                break

    return hallazgos


def evaluar(hallazgos: dict) -> dict:
    """
    Traduce los hallazgos en un veredicto RGPD:

    PASSED  → DPO mencionado + contacto disponible (email o postal)
    WARNING → DPO mencionado pero sin método de contacto específico
    FAILED  → No se detecta ninguna mención al DPO
    """
    if not hallazgos["mencion_dpo"]:
        return {
            "estado":  "FAILED",
            "detalle": (
                "No se detectó ninguna mención al Delegado de Protección de Datos "
                "(DPO/DPD) en la política de privacidad. RGPD Art. 37.7 exige "
                "publicar los datos de contacto del DPO."
            ),
        }

    tiene_contacto = bool(hallazgos["emails_dpo"]) or hallazgos["direccion_postal"]

    if tiene_contacto:
        partes = []
        if hallazgos["emails_dpo"]:
            partes.append(f"email: {', '.join(hallazgos['emails_dpo'])}")
        if hallazgos["direccion_postal"]:
            partes.append("dirección postal")
        return {
            "estado":  "PASSED",
            "detalle": (
                f"DPO designado y datos de contacto publicados "
                f"({'; '.join(partes)}). Cumple RGPD Art. 37.7."
            ),
        }

    return {
        "estado":  "WARNING",
        "detalle": (
            "DPO mencionado en la política pero sin datos de contacto "
            "específicos (email ni dirección postal). RGPD Art. 37.7 "
            "exige publicar el método de contacto."
        ),
    }


# ── Salida por consola ────────────────────────────────────────────────────────
def imprimir(resultado: dict, detalle: bool) -> None:
    sitio     = resultado.get("sitio", "—")
    veredicto = resultado["veredicto"]
    ev        = resultado["evaluacion"]
    hallazgos = resultado["hallazgos"]

    print(f"\n{'='*64}")
    print(f"  R19 — Designación y contacto del DPO")
    print(f"  Sitio   : {sitio}")
    print(f"  Veredicto: {ICONO[veredicto]} {veredicto}")
    print(f"{'='*64}")
    print(f"\n{ev['detalle']}")

    if not detalle or not hallazgos["mencion_dpo"]:
        print()
        return

    print(f"\n{'─'*64}")
    print("Hallazgos:\n")

    if hallazgos["nombre_dpo"]:
        print(f"  Nombre DPO    : {hallazgos['nombre_dpo']}")
    if hallazgos["emails_dpo"]:
        print(f"  Email(s) DPO  : {', '.join(hallazgos['emails_dpo'])}")
    if hallazgos["emails_otros"]:
        print(f"  Otros emails  : {', '.join(hallazgos['emails_otros'][:3])}")
    print(f"  Direc. postal : {'Sí' if hallazgos['direccion_postal'] else 'No detectada'}")

    if hallazgos["fragmentos"]:
        print(f"\n{'─'*64}")
        print("Fragmentos donde se menciona el DPO:\n")
        for i, frag in enumerate(hallazgos["fragmentos"], 1):
            print(f"  [{i}] …{frag[:200]}…")
            print()

    print()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    args    = sys.argv[1:]
    detalle = "--no-detalle" not in args
    args    = [a for a in args if a != "--no-detalle"]

    ruta_arg = args[0] if args else str(POLIGRAPH_DEFAULT)
    r_path, g_path = resolver_ruta(ruta_arg)

    if not r_path.exists():
        print(f"[ERROR] No se encontró: {r_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[*] Leyendo política: {r_path}")
    texto = extraer_texto(r_path)

    # Verificación cruzada con el grafo si está disponible
    if g_path:
        print(f"[*] Verificación cruzada con: {g_path.name}")
        texto_grafo = extraer_textos_grafo(g_path)
        # Concatenamos ambas fuentes; los patrones son idempotentes
        texto = texto + "\n\n" + texto_grafo

    # Intentar inferir el sitio desde el título
    try:
        raw = json.load(open(r_path, encoding="utf-8"))
        sitio = raw.get("siteName") or raw.get("title") or r_path.parent.name
    except Exception:
        sitio = r_path.parent.name

    hallazgos = analizar_texto(texto)
    evaluacion = evaluar(hallazgos)
    veredicto  = evaluacion["estado"]

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
