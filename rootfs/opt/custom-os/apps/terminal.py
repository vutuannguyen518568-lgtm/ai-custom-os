#!/usr/bin/env python3
"""Terminal — tabbed GTK3 window embedding real Vte.Terminal widgets running /bin/bash.
Usage: terminal.py [--cwd DIR] [-e COMMAND [ARGS...]]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gtk, Gdk, GLib, Pango, Vte
from core import common

PALETTES = {
    "dark": dict(fg="#e6e9f0", bg="#0e1118", colors=[
        "#1a1f2b", "#e5604d", "#5fb85a", "#e0a030", "#5b8def", "#c066d8", "#2fb7b0", "#c9cfdb",
        "#3a4256", "#ff7d6a", "#7fd37a", "#f2be55", "#7fa7ff", "#d98cf0", "#58d6cf", "#ffffff"]),
    "light": dict(fg="#1a2030", bg="#fbfcfd", colors=[
        "#1a2030", "#c93c2b", "#2e8b3a", "#a8730f", "#2f5fd0", "#9a3fb5", "#14827c", "#d5dbe5",
        "#5d6678", "#e0503d", "#3fa84b", "#c28a1c", "#4a78e6", "#b257cd", "#209d96", "#f3f5f8"]),
}


def rgba(h):
    c = Gdk.RGBA()
    c.parse(h)
    return c


class TerminalWindow(Gtk.Window):
    def __init__(self, cwd, command):
        super().__init__(title="Terminal")
        self.set_default_size(900, 560)
        self.cfg = common.load_config()
        self.cwd = cwd
        self.nb = Gtk.Notebook(scrollable=True)
        self.nb.set_show_border(False)
        self.add(self.nb)
        self.connect("key-press-event", self.on_key)
        self.new_tab(command)
        self.connect("destroy", Gtk.main_quit)

    def new_tab(self, command=None):
        term = Vte.Terminal()
        pal = PALETTES.get(self.cfg["desktop"]["mode"], PALETTES["dark"])
        term.set_colors(rgba(pal["fg"]), rgba(pal["bg"]), [rgba(c) for c in pal["colors"]])
        term.set_cursor_shape(Vte.CursorShape.BLOCK)
        term.set_font(Pango.FontDescription(self.cfg["desktop"].get("mono_font", "Monospace 11")))
        term.set_scrollback_lines(10000)
        term.set_scroll_on_output(False)
        term.set_scroll_on_keystroke(True)
        term.set_mouse_autohide(True)
        term.set_allow_hyperlink(True)
        argv = ["/bin/bash", "-c", 'exec "$@"', "bash"] + list(command) if command else ["/bin/bash"]
        env = ["TERM=xterm-256color", "COLORTERM=truecolor"]
        term.spawn_async(Vte.PtyFlags.DEFAULT, self.cwd or os.path.expanduser("~"), argv, env,
                         GLib.SpawnFlags.DEFAULT, None, None, -1, None, None)
        label = Gtk.Box(spacing=6)
        title = Gtk.Label(label="bash")
        close = Gtk.Button(label="×")
        close.set_relief(Gtk.ReliefStyle.NONE)
        label.pack_start(title, True, True, 0)
        label.pack_start(close, False, False, 0)
        label.show_all()
        page = self.nb.append_page(term, label)
        self.nb.set_tab_reorderable(term, True)
        term.connect("child-exited", lambda t, status: self.close_tab(t))
        term.connect("window-title-changed", lambda t: title.set_text((t.get_window_title() or "bash")[:24]))
        close.connect("clicked", lambda b: self.close_tab(term))
        term.show()
        self.nb.set_show_tabs(self.nb.get_n_pages() > 1)
        self.nb.set_current_page(page)
        term.grab_focus()

    def close_tab(self, term):
        i = self.nb.page_num(term)
        if i >= 0:
            self.nb.remove_page(i)
        self.nb.set_show_tabs(self.nb.get_n_pages() > 1)
        if self.nb.get_n_pages() == 0:
            self.destroy()

    def current(self):
        return self.nb.get_nth_page(self.nb.get_current_page())

    def on_key(self, w, e):
        ctrl = e.state & Gdk.ModifierType.CONTROL_MASK
        shift = e.state & Gdk.ModifierType.SHIFT_MASK
        t = self.current()
        if not (ctrl and t):
            return False
        k = Gdk.keyval_name(e.keyval) or ""
        if shift and k in ("T", "t"):
            self.new_tab()
        elif shift and k in ("W", "w"):
            self.close_tab(t)
        elif shift and k in ("C", "c"):
            t.copy_clipboard_format(Vte.Format.TEXT)
        elif shift and k in ("V", "v"):
            t.paste_clipboard()
        elif k in ("plus", "equal", "KP_Add"):
            t.set_font_scale(min(t.get_font_scale() + 0.1, 4.0))
        elif k in ("minus", "underscore", "KP_Subtract"):
            t.set_font_scale(max(t.get_font_scale() - 0.1, 0.4))
        elif k in ("0", "KP_0"):
            t.set_font_scale(1.0)
        else:
            return False
        return True


def main():
    args = sys.argv[1:]
    cwd, command = None, None
    if "--cwd" in args:
        i = args.index("--cwd")
        cwd = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    if "-e" in args:
        i = args.index("-e")
        command = args[i + 1:] or None
    common.apply_theme()
    TerminalWindow(cwd, command).show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
