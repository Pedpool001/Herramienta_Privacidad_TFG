"""
R14 - Lenguaje claro y sencillo
(RGPD Art. 12 / AEPD)

Verifica que las políticas de privacidad y los banners de cookies están
redactados en un lenguaje comprensible para el ciudadano medio, evitando
tecnicismos y frases excesivamente largas.

Medición en dos partes:
  1. Política de privacidad — índice de legibilidad Szigriszt-Muñoz sobre el
     texto extraído de readability.json (fuente: PoliGraph).
  2. Banner de cookies — longitud media de frase sobre el texto visible del
     banner (fuente: accessibility_tree.json, reutilizado de R1).

El índice Szigriszt-Muñoz es la adaptación española del Flesch Reading Ease:
  206.835 - 62.3 × (sílabas/palabras) - (palabras/oraciones)

Umbrales:
  ≥ 50  Normal/Adecuado o mejor  →  PASSED
  35–49 Algo difícil              →  WARNING
  < 35  Difícil / Muy difícil     →  FAILED

Veredicto global: el peor de (política, banner). Si alguna parte no está
disponible se evalúa solo la que existe.

Fuentes de datos:
  · readability.json          — texto de la política de privacidad
  · accessibility_tree.json   — texto del banner (opcional)

Uso:
  python3 r14_lenguaje.py                                # directorio por defecto
  python3 r14_lenguaje.py /ruta/poligraph/output/        # directorio con ambos ficheros
  python3 r14_lenguaje.py /ruta/readability.json         # solo política
  python3 r14_lenguaje.py /ruta/poligraph/output/ --no-detalle
"""

import sys
import json
import re
from pathlib import Path

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("[ERROR] Dependencia no instalada: beautifulsoup4")
    print("       pip install beautifulsoup4")
    sys.exit(1)

# ── Rutas por defecto ─────────────────────────────────────────────────────────
_TFG_DIR = Path(__file__).resolve().parents[2]
POLIGRAPH_DEFAULT = _TFG_DIR / "PoliGraph/example/elpais_audit"
RESULTADO_PATH = Path(__file__).resolve().parents[1] / "analysis_data/r14_resultado.json"

ICONO = {"PASSED": "✅", "WARNING": "⚠️ ", "FAILED": "❌"}

# ── Umbrales Szigriszt-Muñoz ──────────────────────────────────────────────────
# Basados en la escala original del índice adaptado al español.
# Para cumplir RGPD Art. 12 ("inteligible para el ciudadano medio") se requiere
# al menos nivel Normal (≥ 50).
UMBRAL_PASSED  = 50
UMBRAL_WARNING = 35

# ── Umbral banner: longitud media de frase (palabras) ─────────────────────────
# Frases de más de 25 palabras de media en un banner indican redacción compleja.
UMBRAL_BANNER_PALABRAS = 25

# ── Tecnicismos legales RGPD / LSSI ───────────────────────────────────────────
# Términos que, si aparecen con alta frecuencia, indican exceso de jerga legal.
# Se detectan sin distinguir mayúsculas/minúsculas.
TECNICISMOS = [
    # Roles del tratamiento
    r"responsable\s+del\s+tratamiento",
    r"encargado\s+del\s+tratamiento",
    r"corresponsable",
    r"destinatario\s+de\s+los\s+datos",
    # Bases jurídicas
    r"base\s+jur[ií]dica",
    r"inter[eé]s\s+leg[ií]timo",
    r"obligaci[oó]n\s+legal",
    r"ejecuci[oó]n\s+de\s+un\s+contrato",
    # Categorías de datos
    r"datos?\s+personales?\s+de\s+categor[ií]a\s+especial",
    r"datos?\s+sensibles?",
    r"datos?\s+biom[eé]tricos?",
    r"datos?\s+gen[eé]ticos?",
    # Transferencias
    r"transferencia\s+internacional",
    r"cl[aá]usulas?\s+contractuales?\s+tipo",
    r"normas?\s+corporativas?\s+vinculantes?",
    r"mecanismo\s+de\s+transferencia",
    # Derechos
    r"derecho\s+de\s+portabilidad",
    r"derecho\s+de\s+oposici[oó]n",
    r"derecho\s+de\s+rectificaci[oó]n",
    r"derecho\s+de\s+supresi[oó]n",
    r"derecho\s+al\s+olvido",
    r"limitaci[oó]n\s+del\s+tratamiento",
    # Otros
    r"seudonimizaci[oó]n",
    r"anonimizaci[oó]n",
    r"evaluaci[oó]n\s+de\s+impacto",
    r"perfilado\s+autom[aá]tico",
    r"decisi[oó]n\s+automatizada",
]

RE_TECNICISMOS = [(t, re.compile(t, re.IGNORECASE)) for t in TECNICISMOS]


# ── Conteo de sílabas (español) ───────────────────────────────────────────────

def contar_silabas(palabra: str) -> int:
    """
    Aproximación lingüística al conteo de sílabas en español.
    Cuenta grupos de vocales (incluyendo diptongos y triptongos).
    Cubre vocales acentuadas y diéresis.
    """
    palabra = palabra.lower().strip(".:,;!?()\"'«»—–-")
    if not palabra:
        return 0
    grupos = re.findall(r"[aeiouáéíóúüàèìòùâêîôû]{1,3}", palabra)
    return max(1, len(grupos))


# ── Carga y limpieza de texto ─────────────────────────────────────────────────

def leer_politica(readability_path: Path) -> str:
    """Lee readability.json y devuelve el texto limpio de la política."""
    with open(readability_path, encoding="utf-8") as f:
        data = json.load(f)
    html = data.get("content", "") or data.get("textContent", "")
    texto = BeautifulSoup(html, "html.parser").get_text(separator=" ")
    # Reemplaza secuencias literales \n \t \r (si el JSON las contiene como texto)
    # y luego colapsa todo espacio múltiple. NO usar clase de chars [\\n\\t\\r]
    # porque dentro de [] los dobles backslashes coinciden con las letras n, t, r.
    texto = texto.replace("\\n", " ").replace("\\t", " ").replace("\\r", " ")
    return re.sub(r"\s+", " ", texto).strip()


def leer_banner(tree_path: Path) -> str:
    """
    Lee accessibility_tree.json y extrae el texto visible del banner de cookies.
    Reutiliza la misma lógica de búsqueda que r1_capas.py.
    """
    with open(tree_path, encoding="utf-8") as f:
        arbol = json.load(f)

    RE_COOKIES = re.compile(
        r"cook(ie|ies)|privac(y|idad)|consent(imiento)?"
        r"|aceptaci[oó]n|datos\s+personales|personal\s+data|gdpr|rgpd",
        re.IGNORECASE,
    )

    def recopilar_texto(nodo):
        rol    = nodo.get("role", "")
        nombre = nodo.get("name", "") or ""
        hijos  = nodo.get("children", [])
        if rol == "text leaf":
            return nombre
        partes = [nombre] if nombre else []
        for h in hijos:
            partes.append(recopilar_texto(h))
        return " ".join(p for p in partes if p)

    def buscar_banner(nodo, depth=0):
        if depth > 12:
            return None
        rol    = nodo.get("role", "")
        nombre = nodo.get("name", "") or ""
        hijos  = nodo.get("children", [])
        if rol in ("dialog", "alertdialog"):
            texto = recopilar_texto(nodo)
            if RE_COOKIES.search(nombre) or RE_COOKIES.search(texto):
                return recopilar_texto(nodo)
        for hijo in hijos:
            r = buscar_banner(hijo, depth + 1)
            if r:
                return r
        return None

    texto = buscar_banner(arbol)
    if texto:
        return re.sub(r"\s{2,}", " ", texto).strip()
    return ""


# ── Métricas de legibilidad ───────────────────────────────────────────────────

def calcular_metricas(texto: str) -> dict:
    """
    Calcula las métricas necesarias para el índice Szigriszt-Muñoz
    y devuelve también estadísticas secundarias.
    """
    oraciones = [s.strip() for s in re.split(r"[.!?]+", texto) if len(s.strip()) > 5]
    palabras  = [w for w in texto.split() if any(c.isalpha() for c in w)]

    num_oraciones = len(oraciones)
    num_palabras  = len(palabras)

    if num_palabras == 0 or num_oraciones == 0:
        return None

    total_silabas = sum(contar_silabas(w) for w in palabras)

    sil_por_palabra  = total_silabas / num_palabras
    palabras_por_frase = num_palabras / num_oraciones

    # Fórmula Szigriszt-Muñoz
    indice = 206.835 - (62.3 * sil_por_palabra) - palabras_por_frase

    return {
        "num_palabras":       num_palabras,
        "num_oraciones":      num_oraciones,
        "total_silabas":      total_silabas,
        "sil_por_palabra":    round(sil_por_palabra, 2),
        "palabras_por_frase": round(palabras_por_frase, 2),
        "indice_szigriszt":   round(indice, 2),
    }


def interpretar_indice(score: float) -> str:
    if score < 15: return "Muy difícil"
    if score < 35: return "Difícil"
    if score < 50: return "Algo difícil"
    if score < 65: return "Normal / Adecuado"
    if score < 75: return "Algo fácil"
    if score < 85: return "Fácil"
    return "Muy fácil"


def veredicto_indice(score: float) -> str:
    if score >= UMBRAL_PASSED:  return "PASSED"
    if score >= UMBRAL_WARNING: return "WARNING"
    return "FAILED"


def detectar_tecnicismos(texto: str) -> list[dict]:
    """Busca los tecnicismos definidos y devuelve los que aparecen en el texto."""
    encontrados = []
    for patron_str, patron_re in RE_TECNICISMOS:
        coincidencias = patron_re.findall(texto)
        if coincidencias:
            encontrados.append({
                "termino":     coincidencias[0],
                "ocurrencias": len(coincidencias),
            })
    return sorted(encontrados, key=lambda x: -x["ocurrencias"])


# ── Evaluación del banner ─────────────────────────────────────────────────────

def evaluar_banner(texto_banner: str) -> dict:
    """
    Para el banner no calculamos Szigriszt (pocas frases → índice no fiable).
    Usamos la longitud media de frase como indicador de complejidad.
    """
    if not texto_banner:
        return {"disponible": False}

    oraciones = [s.strip() for s in re.split(r"[.!?]+", texto_banner) if len(s.strip()) > 5]
    palabras  = [w for w in texto_banner.split() if any(c.isalpha() for c in w)]

    if not oraciones:
        return {"disponible": False}

    media_palabras = len(palabras) / len(oraciones)

    if media_palabras <= UMBRAL_BANNER_PALABRAS:
        veredicto = "PASSED"
        descripcion = f"Frases concisas (media {media_palabras:.1f} palabras/frase)."
    else:
        veredicto = "WARNING"
        descripcion = (
            f"Frases largas en el banner (media {media_palabras:.1f} palabras/frase, "
            f"umbral: {UMBRAL_BANNER_PALABRAS}). Dificulta la comprensión inmediata."
        )

    return {
        "disponible":          True,
        "num_frases":          len(oraciones),
        "media_palabras_frase": round(media_palabras, 1),
        "veredicto":           veredicto,
        "descripcion":         descripcion,
    }


# ── Veredicto global ──────────────────────────────────────────────────────────

_ORDEN = {"FAILED": 0, "WARNING": 1, "PASSED": 2}

def peor(a: str, b: str) -> str:
    return a if _ORDEN[a] <= _ORDEN[b] else b


def evaluar_global(metricas_pol: dict | None, banner: dict, tecnicismos: list) -> dict:
    partes = []

    # Veredicto de la política
    if metricas_pol is None:
        veredicto_pol = "WARNING"
        partes.append("No se pudo calcular el índice de la política (texto insuficiente).")
    else:
        score         = metricas_pol["indice_szigriszt"]
        veredicto_pol = veredicto_indice(score)
        nivel         = interpretar_indice(score)
        partes.append(
            f"Política: índice Szigriszt-Muñoz {score:.1f} ({nivel}) → {veredicto_pol}."
        )

    # Veredicto del banner
    veredicto_ban = "PASSED"
    if banner.get("disponible"):
        veredicto_ban = banner["veredicto"]
        partes.append(f"Banner: {banner['descripcion']}")
    else:
        partes.append("Banner: no disponible (accessibility_tree.json no encontrado).")

    # Tecnicismos (informativo, no cambia veredicto)
    if tecnicismos:
        top = [f"«{t['termino']}» ({t['ocurrencias']}×)" for t in tecnicismos[:4]]
        partes.append(f"Tecnicismos frecuentes: {', '.join(top)}.")

    veredicto_final = peor(veredicto_pol, veredicto_ban)

    return {
        "estado":  veredicto_final,
        "detalle": " ".join(partes),
    }


# ── Salida por consola ────────────────────────────────────────────────────────

def imprimir(resultado: dict, detalle: bool) -> None:
    sitio     = resultado.get("sitio", "—")
    veredicto = resultado["veredicto"]
    ev        = resultado["evaluacion"]
    pol       = resultado.get("politica", {})
    ban       = resultado.get("banner", {})
    tec       = resultado.get("tecnicismos", [])

    print(f"\n{'='*64}")
    print(f"  R14 — Lenguaje claro y sencillo")
    print(f"  Sitio    : {sitio}")
    print(f"  Veredicto: {ICONO[veredicto]} {veredicto}")
    print(f"{'='*64}")
    print(f"\n{ev['detalle']}")

    if not detalle:
        print()
        return

    # ── Política de privacidad ────────────────────────────────────────────────
    if pol:
        score = pol.get("indice_szigriszt")
        print(f"\n{'─'*64}")
        print("Política de privacidad — Índice Szigriszt-Muñoz:\n")
        print(f"  Palabras analizadas : {pol['num_palabras']}")
        print(f"  Oraciones           : {pol['num_oraciones']}")
        print(f"  Palabras / frase    : {pol['palabras_por_frase']:.2f}")
        print(f"  Sílabas / palabra   : {pol['sil_por_palabra']:.2f}")
        print(f"  {'─'*30}")
        print(f"  Índice Szigriszt    : {score:.2f}")
        print(f"  Clasificación       : {interpretar_indice(score)}")
        print(f"  Veredicto política  : {ICONO[veredicto_indice(score)]} {veredicto_indice(score)}")

    # ── Banner ────────────────────────────────────────────────────────────────
    if ban.get("disponible"):
        print(f"\n{'─'*64}")
        print("Banner de cookies — Longitud de frase:\n")
        print(f"  Frases en el banner : {ban['num_frases']}")
        print(f"  Media palabras/frase: {ban['media_palabras_frase']}")
        print(f"  Umbral              : ≤ {UMBRAL_BANNER_PALABRAS} palabras/frase")
        print(f"  Veredicto banner    : {ICONO[ban['veredicto']]} {ban['veredicto']}")

    # ── Tecnicismos ───────────────────────────────────────────────────────────
    if tec:
        print(f"\n{'─'*64}")
        print(f"Tecnicismos legales detectados ({len(tec)} tipos):\n")
        for t in tec[:10]:
            print(f"  · {t['termino']:<45} {t['ocurrencias']}×")

    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def resolver_rutas(ruta_arg: str) -> tuple[Path, Path | None]:
    p = Path(ruta_arg)
    if p.is_dir():
        r_json = p / "readability.json"
        a_json = p / "accessibility_tree.json"
        return r_json, a_json if a_json.exists() else None
    # Fichero directo: buscamos el accessibility_tree.json en el mismo directorio
    a_json = p.parent / "accessibility_tree.json"
    return p, a_json if a_json.exists() else None


def main():
    args    = sys.argv[1:]
    detalle = "--no-detalle" not in args
    args    = [a for a in args if a != "--no-detalle"]

    ruta_arg = args[0] if args else str(POLIGRAPH_DEFAULT)
    r_path, a_path = resolver_rutas(ruta_arg)

    if not r_path.exists():
        print(f"[ERROR] No se encontró: {r_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[*] Leyendo política: {r_path}")
    texto_pol = leer_politica(r_path)

    texto_ban = ""
    if a_path:
        print(f"[*] Leyendo banner desde: {a_path.name}")
        texto_ban = leer_banner(a_path)
        if texto_ban:
            print(f"[*] Texto del banner extraído ({len(texto_ban.split())} palabras)")
        else:
            print("[*] No se encontró banner de cookies en el árbol de accesibilidad")
    else:
        print("[*] accessibility_tree.json no disponible — análisis de banner omitido")

    # Inferir sitio desde readability.json
    try:
        raw  = json.load(open(r_path, encoding="utf-8"))
        sitio = raw.get("siteName") or raw.get("title") or r_path.parent.name
    except Exception:
        sitio = r_path.parent.name

    metricas_pol = calcular_metricas(texto_pol)
    if metricas_pol is None:
        print("[AVISO] Texto de política insuficiente para calcular el índice")

    banner      = evaluar_banner(texto_ban)
    tecnicismos = detectar_tecnicismos(texto_pol)
    evaluacion  = evaluar_global(metricas_pol, banner, tecnicismos)
    veredicto   = evaluacion["estado"]

    resultado = {
        "sitio":        sitio,
        "veredicto":    veredicto,
        "evaluacion":   evaluacion,
        "politica":     metricas_pol or {},
        "banner":       banner,
        "tecnicismos":  tecnicismos,
    }

    RESULTADO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f"[✓] Resultado guardado en {RESULTADO_PATH}")

    imprimir(resultado, detalle)


if __name__ == "__main__":
    main()
