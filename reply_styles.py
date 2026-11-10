import os
import subway_home
import sys
import json
import tempfile

STYLES = {
    "explanatory": "Reply style: explanatory. Explain causes and effects, the reasoning behind choices and the trade-offs, in short sections. Where it helps, end with a brief insight on why.",
    "normal": "Reply style: normal. Lead with a one-line summary, then bullet points and short headings, presentation-style. About 20 lines unless asked for more.",
    "concise": "Reply style: concise. Answer first, then action points only. At most 8 lines. No recaps of tool output or earlier turns.",
}
COMMON = " Think as long as you need: this shapes the reply, not the reasoning."

def _subway_dir() -> str:
    srv = sys.modules.get("server")
    return (
        getattr(srv, "SUBWAY_DIR", None)
        or os.environ.get("SUBWAY_DIR")
        or subway_home.subway_dir()
    )

def settings() -> dict:
    defaults = {"default": "concise", "providers": {}}
    cfg_path = os.path.join(_subway_dir(), "config.json")
    if not os.path.isfile(cfg_path):
        return defaults
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        rs = data.get("reply_style")
        if isinstance(rs, dict):
            d = rs.get("default", "concise")
            if not isinstance(d, str) or d not in STYLES:
                d = "concise"
            provs = rs.get("providers")
            clean_provs = {}
            if isinstance(provs, dict):
                for p, v in provs.items():
                    if isinstance(v, str):
                        clean_provs[str(p).lower()] = v if v in STYLES else "concise"
            return {"default": d, "providers": clean_provs}
    except Exception:
        pass
    return defaults

def style_for(provider: str) -> str:
    s = settings()
    provs = s.get("providers") if isinstance(s.get("providers"), dict) else {}
    val = provs.get(str(provider).lower()) if provider else None
    if not val:
        val = s.get("default")
    if not isinstance(val, str) or val not in STYLES:
        return "concise"
    return val

def text_for(provider: str) -> str:
    return STYLES[style_for(provider)] + COMMON

def install_claude_styles(home=None) -> None:
    base = home or os.path.expanduser("~")
    claude_dir = os.path.join(base, ".claude")
    styles_dir = os.path.join(claude_dir, "output-styles")
    os.makedirs(styles_dir, exist_ok=True)
    for name, desc in STYLES.items():
        title = name.capitalize()
        content = (
            f"---\n"
            f"name: Subway {title}\n"
            f"description: {desc}\n"
            f"keep-coding-instructions: true\n"
            f"---\n\n"
            f"{desc}{COMMON}\n"
        )
        filepath = os.path.join(styles_dir, f"subway-{name}.md")
        tmp_fd, tmp_path = tempfile.mkstemp(dir=styles_dir, prefix=".tmp_style_")
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, filepath)

def current_claude_output_style(home=None) -> str:
    base = home or os.path.expanduser("~")
    settings_file = os.path.join(base, ".claude", "settings.json")
    if not os.path.isfile(settings_file):
        return ""
    try:
        with open(settings_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            val = data.get("outputStyle")
            return val if isinstance(val, str) else ""
    except Exception:
        pass
    return ""

def apply_claude(style: str, home=None) -> None:
    install_claude_styles(home=home)
    base = home or os.path.expanduser("~")
    claude_dir = os.path.join(base, ".claude")
    os.makedirs(claude_dir, exist_ok=True)
    settings_file = os.path.join(claude_dir, "settings.json")
    
    clean_style = str(style).lower()
    if clean_style.startswith("subway "):
        clean_style = clean_style[7:].strip()
    if clean_style not in STYLES:
        clean_style = "concise"
    output_style_name = f"Subway {clean_style.capitalize()}"
    
    cfg = {}
    if os.path.isfile(settings_file):
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg = data
        except Exception:
            cfg = {}
            
    cfg["outputStyle"] = output_style_name
    
    tmp_fd, tmp_path = tempfile.mkstemp(dir=claude_dir, prefix=".tmp_settings_")
    with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp_path, settings_file)
