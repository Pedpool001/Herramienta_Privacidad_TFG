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
    "none":               "whitespace",
    "GenericContainer":   "group",
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
