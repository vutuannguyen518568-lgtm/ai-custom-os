#!/usr/bin/env python3
"""Notes — lightweight tabbed text & code editor with line numbers.
Saving os_config.json or theme.css inside the config folder live-reloads the desktop.
Usage: editor.py [FILE ...]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo
from core import common, ui


class Doc:
    """One editor tab."""

    def __init__(self, path=None):
        self.path = path
        self.modified = False
        self.buf = Gtk.TextBuffer()
        self.view = Gtk.TextView(buffer=self.buf, monospace=True, left_margin=8, right_margin=8,
                                 top_margin=6, bottom_margin=6)
        self.view.set_border_window_size(Gtk.TextWindowType.LEFT, 44)
        self.view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.view.set_accepts_tab(True)
        self.digits = 2
        self.sw = Gtk.ScrolledWindow()
        self.sw.add(self.view)
        self.label = Gtk.Label(label="Untitled", ellipsize=Pango.EllipsizeMode.END, max_width_chars=22)
        self.close_btn = Gtk.Button(label="×")
        self.close_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.tab = Gtk.Box(spacing=4)
        self.tab.pack_start(self.label, True, True, 0)
        self.tab.pack_start(self.close_btn, False, False, 0)
        self.tab.show_all()
        self.view.connect("draw", self.draw_gutter)
        self.buf.connect("changed", self.on_changed)
        self.sw.show_all()
        self.refresh_label()

    def name(self):
        return os.path.basename(self.path) if self.path else "Untitled"

    def refresh_label(self):
        self.label.set_text(("● " if self.modified else "") + self.name())
        self.label.set_tooltip_text(self.path or "Unsaved note")

    def load(self, path):
        with open(path, "rb") as f:
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        self.buf.handler_block_by_func(self.on_changed)
        self.buf.set_text(text)
        self.buf.handler_unblock_by_func(self.on_changed)
        self.buf.place_cursor(self.buf.get_start_iter())
        self.path, self.modified = path, False
        self.update_gutter_width()
        self.refresh_label()

    def save(self, path=None):
        path = path or self.path
        text = self.buf.get_text(self.buf.get_start_iter(), self.buf.get_end_iter(), True)
        tmp = path + ".tmp~"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
        self.path, self.modified = path, False
        self.refresh_label()

    def on_changed(self, *a):
        if not self.modified:
            self.modified = True
            self.refresh_label()
        self.update_gutter_width()
        self.view.queue_draw()

    def update_gutter_width(self):
        digits = max(2, len(str(self.buf.get_line_count())))
        if digits != self.digits or self.view.get_border_window_size(Gtk.TextWindowType.LEFT) == 0:
            self.digits = digits
            self.view.set_border_window_size(Gtk.TextWindowType.LEFT, 14 + digits * 9)

    def draw_gutter(self, view, cr):
        win = view.get_window(Gtk.TextWindowType.LEFT)
        if win is None or not Gtk.cairo_should_draw_window(cr, win):
            return False
        Gtk.cairo_transform_to_window(cr, view, win)
        ctx = view.get_style_context()
        fg = ctx.get_color(Gtk.StateFlags.NORMAL)
        width = view.get_border_window_size(Gtk.TextWindowType.LEFT)
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.06)
        cr.rectangle(0, 0, width, view.get_allocated_height())
        cr.fill()
        layout = view.create_pango_layout("")
        layout.set_alignment(Pango.Alignment.RIGHT)
        layout.set_width((width - 12) * Pango.SCALE)
        cursor_line = self.buf.get_iter_at_mark(self.buf.get_insert()).get_line()
        rect = view.get_visible_rect()
        it, _ = view.get_line_at_y(rect.y)
        while True:
            y, h = view.get_line_yrange(it)
            _, wy = view.buffer_to_window_coords(Gtk.TextWindowType.LEFT, 0, y)
            line = it.get_line()
            layout.set_text(str(line + 1), -1)
            alpha = 0.95 if line == cursor_line else 0.45
            cr.set_source_rgba(fg.red, fg.green, fg.blue, alpha)
            cr.move_to(0, wy)
            PangoCairo.show_layout(cr, layout)
            if y + h > rect.y + rect.height or not it.forward_line():
                break
        return False


class Notes(Gtk.Window):
    def __init__(self, files):
        super().__init__(title="Notes")
        self.set_default_size(980, 640)
        cfg = common.load_config()
        self.mono = Pango.FontDescription(cfg["desktop"].get("mono_font", "Monospace 11"))
        self.docs = []
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)

        tb = Gtk.Box(spacing=4)
        tb.get_style_context().add_class("toolbar")
        for label, fn in (("New", self.act_new), ("Open", self.act_open), ("Save", self.act_save),
                          ("Save As", self.act_save_as)):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda w, f=fn: f())
            tb.pack_start(b, False, False, 0)
        tb.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 6)
        for label, rel in (("os_config.json", "config/os_config.json"), ("theme.css", "config/theme.css")):
            b = Gtk.Button(label=label)
            b.set_tooltip_text("Open " + os.path.join(common.ROOT, rel))
            b.connect("clicked", lambda w, r=rel: self.open_file(os.path.join(common.ROOT, r)))
            b.get_style_context().add_class("boxed")
            tb.pack_start(b, False, False, 0)
        self.wrap = Gtk.CheckButton(label="Wrap lines")
        self.wrap.connect("toggled", self.on_wrap)
        tb.pack_end(self.wrap, False, False, 8)
        tb.pack_end(Gtk.Button(label="Find", relief=Gtk.ReliefStyle.NONE), False, False, 0)
        tb.get_children()[-1].connect("clicked", lambda w: self.show_find())
        root.pack_start(tb, False, False, 0)

        self.find_bar = Gtk.Box(spacing=6, margin=6)
        self.find_entry = Gtk.SearchEntry(placeholder_text="Find in this note")
        self.find_entry.connect("activate", lambda e: self.find_next())
        self.find_entry.connect("search-changed", lambda e: self.find_next(from_cursor=False))
        nxt = Gtk.Button(label="Next")
        nxt.connect("clicked", lambda w: self.find_next())
        prv = Gtk.Button(label="Previous")
        prv.connect("clicked", lambda w: self.find_next(backward=True))
        cls = Gtk.Button(label="Close")
        cls.connect("clicked", lambda w: self.find_bar.hide())
        for w in (self.find_entry, nxt, prv, cls):
            self.find_bar.pack_start(w, w is self.find_entry, w is self.find_entry, 0)
        root.pack_start(self.find_bar, False, False, 0)

        self.nb = Gtk.Notebook(scrollable=True)
        self.nb.set_show_border(False)
        root.pack_start(self.nb, True, True, 0)
        self.status = Gtk.Label(xalign=0)
        self.status.get_style_context().add_class("statusbar")
        root.pack_start(self.status, False, False, 0)

        self.connect("delete-event", self.on_delete)
        self.connect("key-press-event", self.on_key)
        self.nb.connect("switch-page", lambda nb, page, i: GLib.idle_add(self.update_status))
        self.show_all()
        self.find_bar.hide()
        for f in files:
            self.open_file(f)
        if not self.docs:
            self.add_doc()

    # ---- tabs
    def current(self):
        i = self.nb.get_current_page()
        return self.docs[i] if 0 <= i < len(self.docs) else None

    def add_doc(self, path=None):
        d = Doc(path)
        d.view.override_font(self.mono)
        d.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR if self.wrap.get_active() else Gtk.WrapMode.NONE)
        d.close_btn.connect("clicked", lambda b, d=d: self.close_doc(d))
        d.buf.connect("mark-set", lambda *a: self.update_status())
        d.buf.connect("changed", lambda *a: self.update_status())
        self.docs.append(d)
        self.nb.append_page(d.sw, d.tab)
        self.nb.set_tab_reorderable(d.sw, False)
        self.nb.set_current_page(len(self.docs) - 1)
        d.view.grab_focus()
        return d

    def open_file(self, path):
        path = os.path.abspath(path)
        for i, d in enumerate(self.docs):
            if d.path == path:
                self.nb.set_current_page(i)
                return
        cur = self.current()
        reuse = cur if cur and not cur.path and not cur.modified else None
        d = reuse or self.add_doc()
        try:
            if os.path.exists(path):
                d.load(path)
            else:
                d.path = path
                d.refresh_label()
        except OSError as e:
            ui.notify(self, "Cannot open file", str(e))
            if not reuse:
                self.close_doc(d, force=True)

    def close_doc(self, d, force=False):
        if d.modified and not force and not self.ask_discard(d):
            return
        i = self.docs.index(d)
        self.docs.remove(d)
        self.nb.remove_page(i)
        if not self.docs:
            self.add_doc()

    def ask_discard(self, d):
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                text="Save changes to “%s”?" % d.name())
        dlg.format_secondary_text("Your changes will be lost if you don’t save them.")
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Discard", Gtk.ResponseType.REJECT,
                        "Save", Gtk.ResponseType.ACCEPT)
        r = dlg.run()
        dlg.destroy()
        if r == Gtk.ResponseType.ACCEPT:
            return self.act_save(d)
        return r == Gtk.ResponseType.REJECT

    # ---- actions
    def act_new(self):
        self.add_doc()

    def act_open(self):
        dlg = Gtk.FileChooserDialog(title="Open", transient_for=self, action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Open", Gtk.ResponseType.OK)
        dlg.set_select_multiple(True)
        cur = self.current()
        if cur and cur.path:
            dlg.set_current_folder(os.path.dirname(cur.path))
        if dlg.run() == Gtk.ResponseType.OK:
            files = dlg.get_filenames()
            dlg.destroy()
            for f in files:
                self.open_file(f)
        else:
            dlg.destroy()

    def act_save(self, d=None):
        d = d or self.current()
        if d is None:
            return False
        if not d.path:
            return self.act_save_as(d)
        return self._write(d, d.path)

    def act_save_as(self, d=None):
        d = d or self.current()
        if d is None:
            return False
        dlg = Gtk.FileChooserDialog(title="Save As", transient_for=self, action=Gtk.FileChooserAction.SAVE)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.OK)
        dlg.set_do_overwrite_confirmation(True)
        if d.path:
            dlg.set_filename(d.path)
        else:
            dlg.set_current_name("note.txt")
        ok = dlg.run() == Gtk.ResponseType.OK
        path = dlg.get_filename() if ok else None
        dlg.destroy()
        return self._write(d, path) if path else False

    def _write(self, d, path):
        try:
            d.save(path)
        except OSError as e:
            ui.notify(self, "Cannot save", str(e))
            return False
        cfg_dir = os.path.realpath(os.path.join(common.ROOT, "config"))
        if os.path.realpath(os.path.dirname(path)) == cfg_dir and path.endswith((".json", ".css")):
            common.reload_all()
            self.status.set_text("Saved and reloaded the desktop.")
        else:
            self.update_status()
        return True

    def on_wrap(self, btn):
        mode = Gtk.WrapMode.WORD_CHAR if btn.get_active() else Gtk.WrapMode.NONE
        for d in self.docs:
            d.view.set_wrap_mode(mode)

    def show_find(self):
        self.find_bar.show_all()
        self.find_entry.grab_focus()

    def find_next(self, backward=False, from_cursor=True):
        d, text = self.current(), self.find_entry.get_text()
        if not d or not text:
            return
        buf = d.buf
        flags = Gtk.TextSearchFlags.CASE_INSENSITIVE
        if buf.get_has_selection():
            sel_start, sel_end = buf.get_selection_bounds()
            start = sel_start if (backward or not from_cursor) else sel_end
        else:
            start = buf.get_iter_at_mark(buf.get_insert())
        if backward:
            m = start.backward_search(text, flags, None) or buf.get_end_iter().backward_search(text, flags, None)
        else:
            m = start.forward_search(text, flags, None) or buf.get_start_iter().forward_search(text, flags, None)
        if m:
            buf.select_range(m[0], m[1])
            d.view.scroll_to_iter(m[0], 0.2, False, 0, 0)
        else:
            self.status.set_text("No matches for “%s”." % text)

    def update_status(self):
        d = self.current()
        if not d:
            return False
        it = d.buf.get_iter_at_mark(d.buf.get_insert())
        self.status.set_text("Line %d, column %d    %d lines    %s" % (
            it.get_line() + 1, it.get_line_offset() + 1, d.buf.get_line_count(), d.path or "unsaved"))
        return False

    def on_key(self, w, e):
        if not e.state & Gdk.ModifierType.CONTROL_MASK:
            return False
        k = Gdk.keyval_name(e.keyval) or ""
        shift = e.state & Gdk.ModifierType.SHIFT_MASK
        actions = {"n": self.act_new, "o": self.act_open, "f": self.show_find,
                   "s": self.act_save_as if shift else self.act_save,
                   "w": lambda: self.current() and self.close_doc(self.current())}
        fn = actions.get(k.lower())
        if fn:
            fn()
            return True
        return False

    def on_delete(self, *a):
        for d in list(self.docs):
            if d.modified:
                self.nb.set_current_page(self.docs.index(d))
                if not self.ask_discard(d):
                    return True
        Gtk.main_quit()
        return False


def main():
    common.apply_theme()
    Notes(sys.argv[1:])
    Gtk.main()


if __name__ == "__main__":
    main()
