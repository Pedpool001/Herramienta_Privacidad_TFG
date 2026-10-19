"""
Utilidades para convertir el árbol de accesibilidad de CDP (Chromium DevTools
Protocol) al formato de árbol anidado {role, name, children} que usan los
scripts de análisis de PoliGraph (r1_capas, r5_revocabilidad, r14_lenguaje).

Usado por playwright_mod.py para capturar el árbol del sitio principal con
stealth y por html_crawler.py de PoliGraph para el árbol de la política.
"""

_CDP_ROLE_MAP = {
    # Root
    "RootWebArea":        "document",
    "WebArea":            "document",
    # Text atoms
    "StaticText":         "statictext",
    "InlineTextBox":      "text leaf",
    "LineBreak":          "whitespace",
    "strong":             "text leaf",
    "Legend":             "label",
    "LabelText":          "label",
    # Structural / layout (iterate into children)
    "generic":            "group",
    # Un nodo CDP con rol "none"/"presentation" es un contenedor estructural
    # (un <div> sin semántica), NO espacio en blanco. Mapearlo a "whitespace"
    # (que PoliGraph tiene en IGNORED_ELEMENTS) podaba TODO el subárbol que
    # colgaba de él: en muchas políticas el contenido entero cuelga de un primer
    # <div role=none>, así que el SegmentExtractor extraía 0 segmentos y el grafo
    # salía vacío. Debe recorrerse como contenedor → "group".
    "none":               "group",
    "presentation":       "group",
    "GenericContainer":   "group",
    # Contenedores de CONTENIDO principal → se recorren.
    "main":               "section",
    "complementary":      "group",
    # Landmarks de "chrome" (cabecera/nav/pie/buscador). El árbol se captura
    # antes de readability, así que header/nav/footer siguen presentes con sus
    # roles ARIA (banner/contentinfo/navigation/search). Se dejan pasar con su
    # nombre crudo para que PoliGraph los IGNORE explícitamente (ver
    # IGNORED_ELEMENTS en document.py): así el SegmentExtractor no cuela los
    # menús de cabecera/pie como texto de la política (el ListAnnotator los
    # tomaba por estructura de la política y contaminaba actores de R15/R16),
    # replicando el stripping de header/footer/nav que PoliGraph hacía con
    # readability en su diseño original.
    "searchbox":          "textbox",
    # List decoration
    "ListMarker":         "list item marker",
    # Menus
    "MenuListPopup":      "menu",
    "menuitemradio":      "menuitem",
    "menuitemcheckbox":   "menuitem",
    # Images / media
    "image":              "img",
    "Image":              "img",
    "Video":              "img",
    "Audio":              "img",
    # Misc
    "LabelWrapper":       "label",
    "PluginObject":       "application",
}


def cdp_to_snapshot(nodes: list) -> dict | None:
    """
    Convierte la lista plana de CDP Accessibility.getFullAXTree al formato de
    árbol anidado que producía page.accessibility.snapshot(interesting_only=False).
    """
    if not nodes:
        return None

    index = {n["nodeId"]: n for n in nodes}

    def _map_role(raw: str) -> str:
        return _CDP_ROLE_MAP.get(raw, raw)

    def _map_properties(cdp_node: dict, result: dict) -> None:
        for prop in cdp_node.get("properties") or []:
            if prop.get("name") == "level":
                val = (prop.get("value") or {}).get("value")
                if val is not None:
                    result["level"] = int(val)

    def build(cdp_node: dict) -> dict:
        role_raw = (cdp_node.get("role") or {}).get("value", "")
        role = _map_role(role_raw)
        name = (cdp_node.get("name") or {}).get("value", "") or ""
        result: dict = {"role": role}
        if name:
            result["name"] = name
        _map_properties(cdp_node, result)
        children = []
        for cid in cdp_node.get("childIds") or []:
            child = index.get(cid)
            if child is not None:
                children.append(build(child))
        if children:
            result["children"] = children
        return result

    root = None
    for n in nodes:
        pid = n.get("parentId")
        if pid is None or pid not in index:
            root = n
            break

    return build(root) if root else None
