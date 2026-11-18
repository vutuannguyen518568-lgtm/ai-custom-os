"""Shared helpers: config, theme palette, IPC, launching.  No GTK import at module level
so the window manager (which only needs python-xlib + cairo) can use it too."""
import json, os, re, shlex, signal, socket, subprocess, mimetypes

ROOT = os.environ.get("CUSTOM_OS_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "os_config.json")
THEME_PATH = os.path.join(ROOT, "config", "theme.css")
WALLPAPER_DIR = os.path.join(ROOT, "wallpapers")

DEFAULTS = {
    "os_name": "AI Custom OS", "os_version": "1.0",
    "desktop": {"panel_position": "bottom", "panel_height": 48, "icon_size": 56,
                "wallpaper_path": "{ROOT}/wallpapers/lagoon.png", "mode": "dark",
                "accent": "#2fb7b0", "font": "Inter 10", "mono_font": "JetBrains Mono 11"},
    "wm": {"titlebar_height": 32, "border_width": 4, "title_font": "Inter Bold 10"},
    "display": {"output": "", "mode": ""},
    "apps": [], "pinned_apps": [], "desktop_shortcuts": [], "shortcuts": {}, "startup_commands": [],
}

PALETTES = {
    "dark": {
        "win_bg": "#12151d", "surface": "#1a1f2b", "surface_hover": "#252c3b",
        "panel_bg": "rgba(14,17,24,0.94)", "fg": "#e6e9f0", "fg_dim": "#8d96a8",
        "border": "#2a3142", "danger": "#e5604d",
        "wm_active": "#1d2331", "wm_inactive": "#151922",
        "wm_fg_active": "#e6e9f0", "wm_fg_inactive": "#6f788b", "wm_border_inactive": "#2a3142",
    },
    "light": {
        "win_bg": "#eef1f5", "surface": "#ffffff", "surface_hover": "#e1e7ef",
        "panel_bg": "rgba(250,251,253,0.95)", "fg": "#1a2030", "fg_dim": "#5d6678",
        "border": "#cfd6e1", "danger": "#c93c2b",
        "wm_active": "#ffffff", "wm_inactive": "#e3e8ee",
        "wm_fg_active": "#1a2030", "wm_fg_inactive": "#7b8496", "wm_border_inactive": "#cfd6e1",
    },
}
ACCENTS = [("Lagoon", "#2fb7b0"), ("Iris", "#7a7cf0"), ("Amber", "#e0a030"),
           ("Coral", "#ef6f61"), ("Orchid", "#c066d8"), ("Meadow", "#5fb85a")]
PALETTE_BEGIN = "/* >>> palette (managed by Settings) */"
PALETTE_END = "/* <<< palette */"


# ---------------------------------------------------------------- config
def deep_merge(base, over):
    out = dict(base)
    for k, v in over.items():
        out[k] = deep_merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def expand(text):
    return text.replace("{ROOT}", ROOT) if isinstance(text, str) else text


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return deep_merge(DEFAULTS, json.load(f))
    except (OSError, ValueError) as e:
        print("os_config.json unreadable (%s); using defaults" % e)
        return json.loads(json.dumps(DEFAULTS))


def save_config(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, CONFIG_PATH)


def wallpaper_path(cfg):
    return expand(cfg["desktop"].get("wallpaper_path", ""))


def find_app(cfg, name):
    for a in cfg.get("apps", []):
        if a.get("name") == name:
            return a
    return None


# ---------------------------------------------------------------- colours / theme
def parse_color(s):
    """'#rrggbb', '#rrggbbaa', 'rgb(r,g,b)', 'rgba(r,g,b,a)' -> (r,g,b,a) floats 0..1"""
    s = s.strip()
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        a = int(h[6:8], 16) / 255 if len(h) >= 8 else 1.0
        return (r, g, b, a)
    m = re.match(r"rgba?\(([^)]*)\)", s)
    if m:
        p = [x.strip() for x in m.group(1).split(",")]
        a = float(p[3]) if len(p) > 3 else 1.0
        return (int(float(p[0])) / 255, int(float(p[1])) / 255, int(float(p[2])) / 255, a)
    raise ValueError("bad colour: %r" % s)


def luminance(hexcolor):
    r, g, b, _ = parse_color(hexcolor)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def palette(cfg):
    d = cfg["desktop"]
    p = dict(PALETTES.get(d.get("mode", "dark"), PALETTES["dark"]))
    accent = d.get("accent", "#2fb7b0")
    p["accent"] = accent
    p["accent_fg"] = "#06201f" if luminance(accent) > 0.45 else "#ffffff"
    p["wm_border_active"] = accent
    return p


PALETTE_ORDER = ["win_bg", "surface", "surface_hover", "panel_bg", "fg", "fg_dim", "border", "danger",
                 "accent", "accent_fg", "wm_active", "wm_inactive", "wm_fg_active", "wm_fg_inactive",
                 "wm_border_active", "wm_border_inactive"]


def palette_block(cfg):
    p = palette(cfg)
    lines = [PALETTE_BEGIN] + ["@define-color %s %s;" % (k, p[k]) for k in PALETTE_ORDER] + [PALETTE_END]
    return "\n".join(lines)


def write_theme_vars(cfg, path=None):
    """Rewrite only the managed palette block of theme.css; everything else stays hand-editable."""
    path = path or THEME_PATH
    with open(path, encoding="utf-8") as f:
        css = f.read()
    block = palette_block(cfg)
    if PALETTE_BEGIN in css and PALETTE_END in css:
        a = css.index(PALETTE_BEGIN)
        b = css.index(PALETTE_END) + len(PALETTE_END)
        css = css[:a] + block + css[b:]
    else:
        css = block + "\n\n" + css
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(css)
    os.replace(tmp, path)


def parse_theme_colors(path=None):
    out = {}
    try:
        with open(path or THEME_PATH, encoding="utf-8") as f:
            css = f.read()
    except OSError:
        return out
    for m in re.finditer(r"@define-color\s+(\w+)\s+(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))\s*;", css):
        try:
            out[m.group(1)] = parse_color(m.group(2))
        except ValueError:
            pass
    return out


def apply_theme():
    """Load theme.css into GTK (call from GTK programs; safe to call again to hot-reload)."""
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gtk, Gdk, GLib
    global _provider
    cfg = load_config()
    d = cfg["desktop"]
    st = Gtk.Settings.get_default()
    st.set_property("gtk-application-prefer-dark-theme", d.get("mode") == "dark")
    st.set_property("gtk-font-name", d.get("font", "Sans 10"))
    st.set_property("gtk-icon-theme-name", "Adwaita")
    screen = Gdk.Screen.get_default()
    if _provider is not None:
        Gtk.StyleContext.remove_provider_for_screen(screen, _provider)
    _provider = Gtk.CssProvider()
    try:
        _provider.load_from_path(THEME_PATH)
    except GLib.Error as e:
        print("theme.css error:", e.message)
    Gtk.StyleContext.add_provider_for_screen(screen, _provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)


_provider = None


# ---------------------------------------------------------------- process / IPC
def runtime_dir():
    d = os.environ.get("XDG_RUNTIME_DIR")
    if d and os.path.isdir(d) and os.access(d, os.W_OK):
        return d
    return "/tmp"


def shell_sock_path():
    return os.path.join(runtime_dir(), "custom-os-shell-%d.sock" % os.getuid())


def wm_pid_path():
    return os.path.join(runtime_dir(), "custom-os-wm-%d.pid" % os.getuid())


def send_shell(cmd, timeout=1.0):
    """Send one command line to the running desktop shell; returns its reply or None."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(shell_sock_path())
        s.sendall((cmd + "\n").encode())
        return s.recv(256).decode().strip()
    except OSError:
        return None
    finally:
        s.close()


def signal_wm(sig=signal.SIGHUP):
    try:
        with open(wm_pid_path()) as f:
            os.kill(int(f.read().strip()), sig)
        return True
    except (OSError, ValueError):
        return False


def reload_all():
    """Tell the window manager and desktop shell to re-read config + theme."""
    signal_wm(signal.SIGHUP)
    return send_shell("reload")


def run(cmd, cwd=None):
    """Launch a detached GUI/command line; returns the Popen or None."""
    argv = shlex.split(expand(cmd)) if isinstance(cmd, str) else [expand(a) for a in cmd]
    try:
        return subprocess.Popen(argv, cwd=cwd, start_new_session=True, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        print("cannot run %r: %s" % (argv, e))
        return None


def app_cmd(script, *args):
    return ["python3", os.path.join(ROOT, "apps", script)] + list(args)


# ---------------------------------------------------------------- file type handling
TEXT_EXT = {".txt", ".md", ".rst", ".py", ".sh", ".bash", ".json", ".css", ".html", ".htm", ".js", ".ts",
            ".c", ".h", ".cpp", ".hpp", ".rs", ".go", ".java", ".yml", ".yaml", ".toml", ".ini", ".cfg",
            ".conf", ".xml", ".csv", ".log", ".service", ".desktop", ".rules", ".sql", ".tex", ".lua"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico", ".tif", ".tiff"}
ARCHIVE_EXT = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")


def is_text_file(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXT:
        return True
    mt = mimetypes.guess_type(path)[0] or ""
    if mt.startswith("text/"):
        return True
    try:
        with open(path, "rb") as f:
            chunk = f.read(2048)
        return b"\0" not in chunk
    except OSError:
        return False


def classify(path):
    if os.path.isdir(path):
        return "dir"
    low = path.lower()
    if low.endswith(ARCHIVE_EXT):
        return "archive"
    if os.path.splitext(low)[1] in IMAGE_EXT:
        return "image"
    return "text" if is_text_file(path) else "binary"


def open_path(path):
    """Open a path with the matching built-in app. Returns a short status string."""
    kind = classify(path)
    if kind == "dir":
        run(app_cmd("file_manager.py", path))
    elif kind == "text":
        run(app_cmd("editor.py", path))
    elif kind == "image":
        run(app_cmd("file_manager.py", "--preview", path))
    elif kind == "archive":
        run(app_cmd("file_manager.py", "--extract", path))
    else:
        return "No built-in app can open this file type."
    return "ok"
