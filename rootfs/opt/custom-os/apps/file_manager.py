#!/usr/bin/env python3
"""Folder — file manager.
Usage: file_manager.py [PATH | trash://]   |   --preview IMAGE   |   --extract ARCHIVE"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GLib, GObject, GdkPixbuf, Pango
from core import common, ui, fileops

HOME = os.path.expanduser("~")
TRASH_URI = "trash://"
CLIP_TAG = "customos-fm:"
(C_NAME, C_PATH, C_ISDIR, C_SIZE, C_MTIME, C_ICON, C_MARKUP, C_TYPE) = range(8)

ICONS = {".png": "🖼️", ".jpg": "🖼️", ".jpeg": "🖼️", ".gif": "🖼️", ".svg": "🖼️", ".webp": "🖼️",
         ".zip": "🗜️", ".gz": "🗜️", ".tar": "🗜️", ".xz": "🗜️", ".bz2": "🗜️", ".tgz": "🗜️",
         ".mp3": "🎵", ".wav": "🎵", ".flac": "🎵", ".ogg": "🎵", ".mp4": "🎞️", ".mkv": "🎞️", ".webm": "🎞️",
         ".pdf": "📕", ".py": "🐍", ".sh": "📜", ".json": "🧾", ".css": "🎨", ".html": "🌐", ".md": "📝",
         ".txt": "📄", ".iso": "💿", ".deb": "📦"}


def entry_icon(path, isdir):
    return "📁" if isdir else ICONS.get(os.path.splitext(path)[1].lower(), "📄")


def kind_label(path, isdir):
    if isdir:
        return "Folder"
    ext = os.path.splitext(path)[1].lstrip(".")
    return (ext.upper() + " file") if ext else "File"


def list_devices():
    """Block devices the user can mount (lsblk JSON), minus the live medium / system disks."""
    try:
        r = subprocess.run(["lsblk", "-J", "-o", "NAME,PATH,LABEL,SIZE,MOUNTPOINT,MOUNTPOINTS,RM,HOTPLUG,TYPE,FSTYPE"],
                           capture_output=True, text=True, timeout=5)
        data = json.loads(r.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return []

    def truthy(v):
        return str(v).lower() in ("1", "true")

    def mounts(n):
        mp = [n.get("mountpoint")] + list(n.get("mountpoints") or [])
        return [m for m in mp if m]

    def walk(n):
        yield n
        for ch in n.get("children") or []:
            yield from walk(ch)

    out = []
    for disk in data.get("blockdevices", []):
        if disk.get("type") in ("loop", "rom") and not disk.get("fstype"):
            continue
        nodes = list(walk(disk))
        allm = [m for n in nodes for m in mounts(n)]
        if any(m.startswith("/run/live") or m in ("/", "/boot", "/boot/efi", "[SWAP]") for m in allm):
            continue
        removable = truthy(disk.get("rm")) or truthy(disk.get("hotplug"))
        for n in nodes:
            fs = n.get("fstype")
            if not fs or fs in ("swap", "crypto_LUKS", "LVM2_member", "linux_raid_member", "squashfs") \
                    or n.get("type") not in ("part", "disk", "rom"):
                continue
            mp = mounts(n)
            out.append({"path": n.get("path") or "/dev/" + n["name"], "label": n.get("label") or n["name"],
                        "size": n.get("size") or "", "mount": mp[0] if mp else None, "removable": removable})
    return out


class Folder(Gtk.Window):
    def __init__(self, start):
        super().__init__(title="Folder")
        self.set_default_size(1040, 680)
        self.history, self.hidx = [], -1
        self.cwd = None
        self.in_trash = False
        self.show_hidden = False
        self.filter_text = ""
        self.devices = []

        self.store = Gtk.ListStore(str, str, bool, GObject.TYPE_INT64, float, str, str, str)
        self.filt = self.store.filter_new()
        self.filt.set_visible_func(self.row_visible)
        self.model = Gtk.TreeModelSort.new_with_model(self.filt)
        for col in (C_NAME, C_SIZE, C_MTIME, C_TYPE):
            self.model.set_sort_func(col, self.compare, col)
        self.model.set_sort_column_id(C_NAME, Gtk.SortType.ASCENDING)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)
        root.pack_start(self.build_toolbar(), False, False, 0)
        body = Gtk.Box()
        body.pack_start(self.build_sidebar(), False, False, 0)
        body.pack_start(self.build_content(), True, True, 0)
        root.pack_start(body, True, True, 0)
        self.status_l = Gtk.Label(xalign=0)
        self.status_r = Gtk.Label(xalign=1)
        sb = Gtk.Box()
        sb.get_style_context().add_class("statusbar")
        sb.pack_start(self.status_l, True, True, 0)
        sb.pack_end(self.status_r, False, False, 0)
        root.pack_start(sb, False, False, 0)

        self.connect("key-press-event", self.on_key)
        self.connect("destroy", Gtk.main_quit)
        self.show_all()
        self.path_entry.hide()
        self.go(start)
        self.refresh_devices()
        GLib.timeout_add_seconds(5, lambda: (self.refresh_devices(), True)[1])

    # ================================================================== UI construction
    def build_toolbar(self):
        tb = Gtk.Box(spacing=4)
        tb.get_style_context().add_class("toolbar")
        self.btn_back = Gtk.Button(label="◀")
        self.btn_fwd = Gtk.Button(label="▶")
        self.btn_up = Gtk.Button(label="▲")
        for b, tip, fn in ((self.btn_back, "Back (Alt+Left)", self.go_back), (self.btn_fwd, "Forward (Alt+Right)", self.go_forward),
                           (self.btn_up, "Up (Alt+Up)", self.go_up)):
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda w, f=fn: f())
            tb.pack_start(b, False, False, 0)
        self.crumbs = Gtk.Box(spacing=0)
        self.path_entry = Gtk.Entry()
        self.path_entry.connect("activate", lambda e: (self.go(os.path.expanduser(e.get_text())), self.path_entry.hide(), self.crumbs.show()))
        self.path_entry.connect("key-press-event", self.on_path_key)
        edit = Gtk.Button(label="✎")
        edit.set_tooltip_text("Type a path (Ctrl+L)")
        edit.connect("clicked", lambda w: self.edit_path())
        pathbox = Gtk.Box(spacing=4)
        pathbox.pack_start(self.crumbs, False, False, 4)
        pathbox.pack_start(self.path_entry, True, True, 0)
        pathbox.pack_start(edit, False, False, 0)
        tb.pack_start(pathbox, True, True, 6)
        self.search = Gtk.SearchEntry(placeholder_text="Filter this folder")
        self.search.set_size_request(190, -1)
        self.search.connect("search-changed", self.on_search)
        tb.pack_start(self.search, False, False, 0)
        self.btn_grid = Gtk.ToggleButton(label="▦")
        self.btn_list = Gtk.ToggleButton(label="☰")
        self.btn_grid.set_active(True)
        self.btn_grid.connect("clicked", lambda b: self.set_view("grid"))
        self.btn_list.connect("clicked", lambda b: self.set_view("list"))
        self.btn_grid.set_tooltip_text("Grid view (Ctrl+1)")
        self.btn_list.set_tooltip_text("List view (Ctrl+2)")
        tb.pack_start(self.btn_grid, False, False, 0)
        tb.pack_start(self.btn_list, False, False, 0)
        return tb

    def build_sidebar(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.get_style_context().add_class("sidebar")
        box.set_size_request(200, -1)
        t = Gtk.Label(label="Places", xalign=0)
        t.get_style_context().add_class("section-title")
        box.pack_start(t, False, False, 0)
        self.places = Gtk.ListBox()
        self.places.set_selection_mode(Gtk.SelectionMode.NONE)
        self.places.connect("row-activated", lambda lb, row: self.go(row.target))
        for icon, name, target in (("🏠", "Home", HOME), ("🖥️", "Desktop", os.path.join(HOME, "Desktop")),
                                   ("📄", "Documents", os.path.join(HOME, "Documents")),
                                   ("⬇️", "Downloads", os.path.join(HOME, "Downloads")), ("🗑️", "Trash", TRASH_URI)):
            if target != TRASH_URI:
                os.makedirs(target, exist_ok=True)
            self.places.add(self.sidebar_row(icon, name, target))
        box.pack_start(self.places, False, False, 0)
        t2 = Gtk.Label(label="Devices", xalign=0)
        t2.get_style_context().add_class("section-title")
        box.pack_start(t2, False, False, 0)
        self.dev_list = Gtk.ListBox()
        self.dev_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.dev_list.connect("row-activated", self.on_device_activated)
        box.pack_start(self.dev_list, False, False, 0)
        return box

    def sidebar_row(self, icon, name, target, extra=None):
        row = Gtk.ListBoxRow()
        row.target = target
        h = Gtk.Box(spacing=10)
        h.pack_start(Gtk.Label(label=icon), False, False, 0)
        h.pack_start(Gtk.Label(label=name, xalign=0, ellipsize=Pango.EllipsizeMode.END), True, True, 0)
        if extra:
            h.pack_end(extra, False, False, 0)
        row.add(h)
        return row

    def build_content(self):
        self.stack = Gtk.Stack()
        # grid
        self.icons = Gtk.IconView(model=self.model)
        self.icons.set_markup_column(C_MARKUP)
        self.icons.set_item_width(104)
        self.icons.set_spacing(4)
        self.icons.set_selection_mode(Gtk.SelectionMode.MULTIPLE)
        self.icons.connect("item-activated", lambda v, p: self.activate_path(p))
        self.icons.connect("button-press-event", self.on_view_press, "grid")
        self.icons.connect("selection-changed", lambda v: self.update_status())
        sw1 = Gtk.ScrolledWindow()
        sw1.add(self.icons)
        # list
        self.tree = Gtk.TreeView(model=self.model)
        self.tree.set_headers_visible(True)
        self.tree.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        col = Gtk.TreeViewColumn("Name")
        r_icon = Gtk.CellRendererText()
        r_name = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END)
        col.pack_start(r_icon, False)
        col.pack_start(r_name, True)
        col.add_attribute(r_icon, "text", C_ICON)
        col.add_attribute(r_name, "text", C_NAME)
        col.set_expand(True)
        col.set_resizable(True)
        col.set_sort_column_id(C_NAME)
        self.tree.append_column(col)
        r_size = Gtk.CellRendererText(xalign=1.0)
        c2 = Gtk.TreeViewColumn("Size", r_size)
        c2.set_cell_data_func(r_size, lambda c, r, m, i, d: r.set_property(
            "text", "" if m[i][C_ISDIR] or m[i][C_SIZE] < 0 else fileops.human_size(m[i][C_SIZE])))
        c2.set_sort_column_id(C_SIZE)
        c2.set_min_width(90)
        self.tree.append_column(c2)
        r_mt = Gtk.CellRendererText()
        c3 = Gtk.TreeViewColumn("Modified", r_mt)
        c3.set_cell_data_func(r_mt, lambda c, r, m, i, d: r.set_property(
            "text", time.strftime("%Y-%m-%d %H:%M", time.localtime(m[i][C_MTIME])) if m[i][C_MTIME] else ""))
        c3.set_sort_column_id(C_MTIME)
        c3.set_min_width(150)
        self.tree.append_column(c3)
        c4 = Gtk.TreeViewColumn("Type", Gtk.CellRendererText(), text=C_TYPE)
        c4.set_sort_column_id(C_TYPE)
        c4.set_min_width(110)
        self.tree.append_column(c4)
        self.tree.connect("row-activated", lambda t, p, c: self.activate_path(p))
        self.tree.connect("button-press-event", self.on_view_press, "list")
        self.tree.get_selection().connect("changed", lambda s: self.update_status())
        sw2 = Gtk.ScrolledWindow()
        sw2.add(self.tree)
        self.stack.add_named(sw1, "grid")
        self.stack.add_named(sw2, "list")
        return self.stack

    # ================================================================== model helpers
    def compare(self, model, a, b, col):
        ra, rb = model[a], model[b]
        if ra[C_ISDIR] != rb[C_ISDIR]:
            d = -1 if ra[C_ISDIR] else 1
            # keep folders on top regardless of sort direction
            order = self.model.get_sort_column_id()[2]
            return d if order == Gtk.SortType.ASCENDING else -d
        if col == C_NAME:
            x, y = ra[C_NAME].lower(), rb[C_NAME].lower()
        else:
            x, y = ra[col], rb[col]
            if x == y:
                x, y = ra[C_NAME].lower(), rb[C_NAME].lower()
        return (x > y) - (x < y)

    def row_visible(self, model, it, data=None):
        name = model[it][C_NAME]
        if not name:
            return False
        if not self.show_hidden and name.startswith("."):
            return False
        return not self.filter_text or self.filter_text in name.lower()

    def add_row(self, name, path, isdir, size, mtime, typ=None):
        icon = entry_icon(path, isdir)
        short = name if len(name) <= 22 else name[:20] + "…"
        markup = '<span size="26000">%s</span>\n<span size="9500">%s</span>' % (icon, GLib.markup_escape_text(short))
        self.store.append([name, path, isdir, size, mtime, icon, markup, typ or kind_label(path, isdir)])

    def selected_paths(self):
        if self.stack.get_visible_child_name() == "grid":
            return [self.model[p][C_PATH] for p in self.icons.get_selected_items()]
        model, paths = self.tree.get_selection().get_selected_rows()
        return [model[p][C_PATH] for p in paths]

    def status(self, text):
        self.status_l.set_text(text)

    def update_status(self):
        sel = self.selected_paths()
        n = len(self.model)
        self.status_l.set_text("%d selected" % len(sel) if sel else "%d items" % n)
        try:
            if self.cwd and not self.in_trash:
                self.status_r.set_text("%s free" % fileops.human_size(shutil.disk_usage(self.cwd).free))
            else:
                self.status_r.set_text("")
        except OSError:
            self.status_r.set_text("")

    # ================================================================== navigation
    def go(self, path, push=True):
        path = path if path == TRASH_URI else os.path.abspath(path)
        if path != TRASH_URI and not os.path.isdir(path):
            if os.path.exists(path):
                self.open_file(path)
            else:
                self.status("No such folder: " + path)
            return
        self.in_trash = path == TRASH_URI
        self.cwd = path
        if push:
            self.history = self.history[:self.hidx + 1] + [path]
            self.hidx = len(self.history) - 1
        self.load()

    def load(self):
        self.icons.set_model(None)
        self.tree.set_model(None)
        self.store.clear()
        src = fileops.TRASH_FILES if self.in_trash else self.cwd
        if self.in_trash:
            os.makedirs(src, exist_ok=True)
        try:
            with os.scandir(src) as it:
                entries = list(it)
        except OSError as e:
            entries = []
            self.status(str(e))
        for e in entries:
            try:
                isdir = e.is_dir()
                st = e.stat(follow_symlinks=False)
                typ = None
                if self.in_trash:
                    orig = fileops.trash_original(e.path)
                    typ = "from " + (os.path.dirname(orig) if orig else "unknown")
                self.add_row(e.name, e.path, isdir, -1 if isdir else st.st_size, st.st_mtime, typ)
            except OSError:
                self.add_row(e.name, e.path, False, -1, 0)
        self.icons.set_model(self.model)
        self.tree.set_model(self.model)
        self.rebuild_crumbs()
        self.btn_back.set_sensitive(self.hidx > 0)
        self.btn_fwd.set_sensitive(self.hidx < len(self.history) - 1)
        self.btn_up.set_sensitive(not self.in_trash and self.cwd != "/")
        self.search.set_text("")
        self.set_title(("Trash" if self.in_trash else os.path.basename(self.cwd) or "/") + " — Folder")
        self.update_status()

    def go_back(self):
        if self.hidx > 0:
            self.hidx -= 1
            self.go(self.history[self.hidx], push=False)

    def go_forward(self):
        if self.hidx < len(self.history) - 1:
            self.hidx += 1
            self.go(self.history[self.hidx], push=False)

    def go_up(self):
        if not self.in_trash:
            self.go(os.path.dirname(self.cwd))

    def refresh(self):
        self.load()

    def rebuild_crumbs(self):
        for c in self.crumbs.get_children():
            self.crumbs.remove(c)
        parts = []
        if self.in_trash:
            parts = [("🗑️ Trash", TRASH_URI)]
        else:
            if self.cwd == HOME or self.cwd.startswith(HOME + os.sep):
                parts.append(("Home", HOME))
                rel = os.path.relpath(self.cwd, HOME)
                cur = HOME
            else:
                parts.append(("/", "/"))
                rel = self.cwd.lstrip("/")
                cur = "/"
            for seg in [s for s in rel.split(os.sep) if s and s != "."]:
                cur = os.path.join(cur, seg)
                parts.append((seg, cur))
        for i, (label, target) in enumerate(parts):
            if i:
                self.crumbs.pack_start(Gtk.Label(label="›"), False, False, 0)
            b = Gtk.Button(label=label)
            b.get_style_context().add_class("crumb")
            b.connect("clicked", lambda w, t=target: self.go(t))
            self.crumbs.pack_start(b, False, False, 0)
        self.crumbs.show_all()

    def edit_path(self):
        self.crumbs.hide()
        self.path_entry.set_text(self.cwd if not self.in_trash else HOME)
        self.path_entry.show()
        self.path_entry.grab_focus()

    def on_path_key(self, w, e):
        if e.keyval == Gdk.KEY_Escape:
            self.path_entry.hide()
            self.crumbs.show()
            return True
        return False

    def on_search(self, entry):
        self.filter_text = entry.get_text().strip().lower()
        self.filt.refilter()
        self.update_status()

    def set_view(self, name):
        self.stack.set_visible_child_name(name)
        self.btn_grid.set_active(name == "grid")
        self.btn_list.set_active(name == "list")

    # ================================================================== opening
    def activate_path(self, treepath):
        path = self.model[treepath][C_PATH]
        if self.in_trash:
            self.status("Restore the item from the right-click menu to open it.")
            return
        if os.path.isdir(path):
            self.go(path)
        else:
            self.open_file(path)

    def open_file(self, path):
        res = common.open_path(path)
        if res != "ok":
            self.status(res)

    # ================================================================== devices
    def refresh_devices(self):
        def work():
            return list_devices()
        def done(devs):
            self.devices = devs
            for r in self.dev_list.get_children():
                self.dev_list.remove(r)
            for d in devs:
                eject = None
                if d["mount"]:
                    eject = Gtk.Button(label="⏏")
                    eject.set_relief(Gtk.ReliefStyle.NONE)
                    eject.set_tooltip_text("Unmount")
                    eject.connect("clicked", lambda b, d=d: self.unmount(d))
                name = "%s  (%s)" % (d["label"], d["size"]) if d["size"] else d["label"]
                row = self.sidebar_row("🔌" if d["removable"] else "💽", name, d["mount"], eject)
                row.dev = d
                self.dev_list.add(row)
            self.dev_list.show_all()
        threading.Thread(target=lambda: GLib.idle_add(lambda r=work(): (done(r), False)[1]), daemon=True).start()

    def on_device_activated(self, lb, row):
        d = row.dev
        if d["mount"]:
            self.go(d["mount"])
            return
        self.status("Mounting %s…" % d["label"])
        self.bg(lambda: subprocess.run(["udisksctl", "mount", "-b", d["path"]], capture_output=True, text=True, timeout=60),
                self.after_mount)

    def after_mount(self, r):
        if r.returncode == 0 and " at " in r.stdout:
            mp = r.stdout.strip().split(" at ", 1)[1].rstrip(".")
            self.refresh_devices()
            self.go(mp)
        else:
            self.status("")
            ui.notify(self, "Cannot mount drive", (r.stderr or r.stdout or "Unknown error").strip())

    def unmount(self, d):
        def after(r):
            self.refresh_devices()
            if r.returncode != 0:
                ui.notify(self, "Cannot unmount drive", (r.stderr or r.stdout).strip())
            elif self.cwd and d["mount"] and self.cwd.startswith(d["mount"]):
                self.go(HOME)
        self.bg(lambda: subprocess.run(["udisksctl", "unmount", "-b", d["path"]], capture_output=True, text=True, timeout=60), after)

    # ================================================================== file operations
    def bg(self, fn, done=None):
        def work():
            try:
                res, err = fn(), None
            except Exception as e:                  # report any failure on the UI thread
                res, err = None, e
            def finish():
                if err is not None:
                    ui.notify(self, "Operation failed", str(err))
                elif done:
                    done(res)
                self.refresh()
                return False
            GLib.idle_add(finish)
        threading.Thread(target=work, daemon=True).start()

    def target_dir(self):
        return None if self.in_trash else self.cwd

    def clip_set(self, mode, paths):
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(CLIP_TAG + mode + "\n" + "\n".join(paths), -1)
        self.status("%s %d item(s)" % ("Cut" if mode == "cut" else "Copied", len(paths)))

    def act_copy(self):
        sel = self.selected_paths()
        if sel:
            self.clip_set("copy", sel)

    def act_cut(self):
        sel = self.selected_paths()
        if sel and not self.in_trash:
            self.clip_set("cut", sel)

    def act_paste(self):
        dst = self.target_dir()
        text = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text() or ""
        if not dst or not text.startswith(CLIP_TAG):
            return
        head, *paths = text.split("\n")
        mode = head[len(CLIP_TAG):]
        paths = [p for p in paths if os.path.lexists(p)]
        def work():
            for p in paths:
                if mode == "cut":
                    fileops.move_item(p, dst)
                else:
                    fileops.copy_item(p, dst)
        self.status("Pasting…")
        self.bg(work, lambda r: self.status("Pasted %d item(s)" % len(paths)))
        if mode == "cut":
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).clear()

    def act_new_folder(self):
        if self.target_dir():
            name = ui.ask_text(self, "New Folder", "Folder name", "New Folder", "Create")
            if name:
                self.run_op(lambda: os.makedirs(os.path.join(self.cwd, name)))

    def act_new_file(self):
        if self.target_dir():
            name = ui.ask_text(self, "New File", "File name", "New File.txt", "Create")
            if name:
                self.run_op(lambda: open(os.path.join(self.cwd, name), "x").close())

    def run_op(self, fn):
        try:
            fn()
        except OSError as e:
            ui.notify(self, "Operation failed", str(e))
        self.refresh()

    def act_rename(self):
        sel = self.selected_paths()
        if len(sel) != 1 or self.in_trash:
            return
        old = sel[0]
        name = ui.ask_text(self, "Rename", "New name", os.path.basename(old), "Rename")
        if name and name != os.path.basename(old):
            try:
                fileops.rename_item(old, name)
            except (OSError, ValueError) as e:
                ui.notify(self, "Cannot rename", str(e))
            self.refresh()

    def act_trash(self):
        sel = self.selected_paths()
        if not sel or self.in_trash:
            return
        def work():
            for p in sel:
                fileops.trash_item(p)
        self.bg(work, lambda r: self.status("Moved %d item(s) to Trash" % len(sel)))

    def act_delete(self):
        sel = self.selected_paths()
        if not sel:
            return
        if not ui.confirm(self, "Delete permanently",
                          "Permanently delete %d item(s)? This cannot be undone." % len(sel), "Delete", danger=True):
            return
        def work():
            for p in sel:
                (fileops.purge_trashed if self.in_trash else fileops.delete_item)(p)
        self.bg(work)

    def act_restore(self):
        sel = self.selected_paths()
        if self.in_trash and sel:
            self.bg(lambda: [fileops.restore_item(p) for p in sel], lambda r: self.status("Restored %d item(s)" % len(sel)))

    def act_empty_trash(self):
        if ui.confirm(self, "Empty Trash", "Permanently delete everything in the Trash?", "Empty Trash", danger=True):
            self.bg(fileops.empty_trash)

    def act_extract(self):
        for p in self.selected_paths():
            if fileops.is_archive(p):
                self.status("Extracting %s…" % os.path.basename(p))
                self.bg(lambda p=p: fileops.extract(p), lambda r: self.status("Extracted to " + r))

    def act_compress(self, kind):
        sel = self.selected_paths()
        if sel and not self.in_trash:
            self.status("Compressing…")
            self.bg(lambda: fileops.compress(sel, kind, self.cwd), lambda r: self.status("Created " + os.path.basename(r)))

    def act_terminal(self):
        common.run(common.app_cmd("terminal.py", "--cwd", self.cwd if not self.in_trash else HOME))

    def act_select_all(self):
        if self.stack.get_visible_child_name() == "grid":
            self.icons.select_all()
        else:
            self.tree.get_selection().select_all()

    # ================================================================== menus / keys
    def on_view_press(self, view, ev, kind):
        if ev.button != 3:
            return False
        if kind == "grid":
            p = view.get_path_at_pos(int(ev.x), int(ev.y))
            if p is not None and not view.path_is_selected(p):
                view.unselect_all()
                view.select_path(p)
            elif p is None:
                view.unselect_all()
        else:
            hit = view.get_path_at_pos(int(ev.x), int(ev.y))
            sel = view.get_selection()
            if hit and not sel.path_is_selected(hit[0]):
                sel.unselect_all()
                sel.select_path(hit[0])
            elif not hit:
                sel.unselect_all()
        self.popup_menu(ev)
        return True

    def popup_menu(self, ev):
        sel = self.selected_paths()
        m = Gtk.Menu()
        def add(label, fn, enabled=True):
            it = Gtk.MenuItem(label=label)
            it.set_sensitive(enabled)
            it.connect("activate", lambda i: fn())
            m.append(it)
        sep = lambda: m.append(Gtk.SeparatorMenuItem())
        if self.in_trash:
            add("Restore", self.act_restore, bool(sel))
            add("Delete permanently", self.act_delete, bool(sel))
            sep()
            add("Empty Trash", self.act_empty_trash)
        else:
            if sel:
                add("Open", lambda: [self.go(p) if os.path.isdir(p) else self.open_file(p) for p in sel[:1]])
                sep()
                add("Cut", self.act_cut)
                add("Copy", self.act_copy)
            add("Paste", self.act_paste, (Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text() or "").startswith(CLIP_TAG))
            if sel:
                sep()
                add("Rename", self.act_rename, len(sel) == 1)
                add("Move to Trash", self.act_trash)
                add("Delete permanently", self.act_delete)
                sep()
                if any(fileops.is_archive(p) for p in sel):
                    add("Extract here", self.act_extract)
                add("Compress to .zip", lambda: self.act_compress("zip"))
                add("Compress to .tar.gz", lambda: self.act_compress("tar.gz"))
            sep()
            add("New Folder", self.act_new_folder)
            add("New File", self.act_new_file)
            add("Open Terminal Here", self.act_terminal)
        m.show_all()
        m.popup_at_pointer(ev)

    def on_key(self, w, e):
        if self.search.is_focus() or self.path_entry.is_focus():
            return False
        ctrl = e.state & Gdk.ModifierType.CONTROL_MASK
        alt = e.state & Gdk.ModifierType.MOD1_MASK
        shift = e.state & Gdk.ModifierType.SHIFT_MASK
        k = Gdk.keyval_name(e.keyval) or ""
        if ctrl:
            table = {"c": self.act_copy, "x": self.act_cut, "v": self.act_paste, "a": self.act_select_all,
                     "l": self.edit_path, "f": lambda: self.search.grab_focus(), "h": self.toggle_hidden,
                     "1": lambda: self.set_view("grid"), "2": lambda: self.set_view("list"),
                     "n": self.act_new_folder if shift else self.act_new_file}
            fn = table.get(k.lower())
            if fn:
                fn()
                return True
        elif alt:
            fn = {"Left": self.go_back, "Right": self.go_forward, "Up": self.go_up}.get(k)
            if fn:
                fn()
                return True
        elif k == "F2":
            self.act_rename()
        elif k == "Delete":
            (self.act_delete if shift or self.in_trash else self.act_trash)()
        elif k == "BackSpace":
            self.go_up()
        elif k == "F5":
            self.refresh()
        else:
            return False
        return True

    def toggle_hidden(self):
        self.show_hidden = not self.show_hidden
        self.filt.refilter()
        self.update_status()


# ====================================================================== standalone helpers
def show_preview(path):
    win = Gtk.Window(title=os.path.basename(path))
    win.set_default_size(900, 640)
    sw = Gtk.ScrolledWindow()
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file(path)
        w, h = pb.get_width(), pb.get_height()
        s = min(1.0, 1100 / w, 780 / h)
        if s < 1.0:
            pb = pb.scale_simple(int(w * s), int(h * s), GdkPixbuf.InterpType.BILINEAR)
        sw.add(Gtk.Image.new_from_pixbuf(pb))
        win.resize(min(1100, max(360, pb.get_width() + 4)), min(780, max(240, pb.get_height() + 4)))
    except GLib.Error as e:
        sw.add(Gtk.Label(label="Cannot display this image:\n%s" % e.message, margin=24))
    win.add(sw)
    win.connect("destroy", Gtk.main_quit)
    win.connect("key-press-event", lambda w, e: win.destroy() if e.keyval == Gdk.KEY_Escape else None)
    win.show_all()


def main():
    common.apply_theme()
    args = sys.argv[1:]
    if args[:1] == ["--preview"] and len(args) > 1:
        show_preview(args[1])
    elif args[:1] == ["--extract"] and len(args) > 1:
        try:
            dest = fileops.extract(args[1])
        except Exception as e:
            ui.notify(None, "Cannot extract archive", str(e))
            return
        Folder(dest)
    else:
        start = args[0] if args else HOME
        Folder(start if start == TRASH_URI else os.path.expanduser(start))
    Gtk.main()


if __name__ == "__main__":
    main()
