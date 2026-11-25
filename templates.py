"""
Codex Futura — Prompt Templates
Named, reusable prompts with {placeholder} substitution.
Stored as JSON files in ~/codex_futura/templates/.
"""
import json
import os
import re
import config

_DIR = config.TEMPLATES_DIR


def _path(name: str) -> str:
    safe = re.sub(r"[^\w\-]", "_", name.lower().strip())
    return os.path.join(_DIR, f"{safe}.json")


def save(name: str, text: str) -> str:
    """Persist a template. Returns the file path."""
    os.makedirs(_DIR, exist_ok=True)
    with open(_path(name), "w", encoding="utf-8") as f:
        json.dump({"name": name, "text": text}, f, indent=2, ensure_ascii=False)
    return _path(name)


def get(name: str) -> dict | None:
    """Return template dict or None if not found."""
    p = _path(name)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def render(name: str, **kwargs) -> str:
    """
    Render a template with keyword substitution.
    Raises ValueError for unknown names or missing placeholders.
    """
    tpl = get(name)
    if tpl is None:
        raise ValueError(f"Template '{name}' not found.")
    try:
        return tpl["text"].format(**kwargs)
    except KeyError as e:
        raise ValueError(f"Template '{name}' requires placeholder {e}.")


def list_all() -> list[dict]:
    """Return all templates sorted by name."""
    if not os.path.exists(_DIR):
        return []
    result = []
    for fname in sorted(os.listdir(_DIR)):
        if fname.endswith(".json"):
            try:
                with open(os.path.join(_DIR, fname), encoding="utf-8") as f:
                    result.append(json.load(f))
            except Exception:
                pass
    return result


def delete(name: str) -> bool:
    """Delete a template. Returns True if it existed."""
    p = _path(name)
    if os.path.exists(p):
        os.remove(p)
        return True
    return False


def parse_kwargs(args: str) -> dict:
    """
    Parse 'key=value key2=value2' strings into a dict.
    Values with spaces should be quoted: key="hello world"
    """
    pattern = re.findall(r'(\w+)=(?:"([^"]*)"|([\S]*))', args)
    return {k: (v1 or v2) for k, v1, v2 in pattern}
