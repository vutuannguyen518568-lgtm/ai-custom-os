#!/usr/bin/env python3
"""AI Custom OS desktop shell: wallpaper + desktop icons, taskbar/dock with system tray,
searchable app launcher.  Talks to the window manager through EWMH / _CUSTOMOS_WINDOWS and is
controlled through a UNIX socket (reload, toggle-launcher, ...)."""
import configparser
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GLib, Gio, GdkPixbuf, GdkX11, Pango  # noqa: F401
import psutil
from Xlib import X, Xatom, display as xdisplay
from Xlib.protocol import event as xevent

from core import common, ui, fileops

DESKTOP_DIR = os.path.join(os.path.expanduser("~"), "Desktop")


def sh_out(argv, timeout=4):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def bg(fn, done=None):
    """Run fn in a thread; deliver its result to done() on the GTK main loop."""
    def work():
        res = fn()
        if done:
            GLib.idle_add(lambda: (done(res), False)[1])
    threading.Thread(target=work, daemon=True).start()


def emoji_markup(icon, px):
    return '<span size="%d">%s</span>' % (int(px * 0.75 * 1024), GLib.markup_escape_text(icon))


def monitor_geometry():
    disp = Gdk.Display.get_default()
    mon = disp.get_primary_monitor() or disp.get_monitor(0)
    return mon.get_geometry()


# ============================================================================ window tracking
class WindowTracker:
    """Reads the WM's window list and sends it standard EWMH/ICCCM requests."""

    def __init__(self, on_change):
        self.d = xdisplay.Display()
        self.root = self.d.screen().root
        self.on_change = on_change
        self.windows = []
        self.atom_list = self.d.intern_atom("_CUSTOMOS_WINDOWS")
        self.root.change_attributes(event_mask=X.PropertyChangeMask)
        GLib.io_add_watch(self.d.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN, self._on_io)
        self.refresh()

    def _on_io(self, fd, cond):
        changed = False
        while self.d.pending_events():
            ev = self.d.next_event()
            if ev.type == X.PropertyNotify and ev.atom == self.atom_list:
                changed = True
        if changed:
            self.refresh()
        return True

    def refresh(self):
        try:
            p = self.root.get_full_property(self.atom_list, X.AnyPropertyType)
            raw = bytes(p.value).decode("utf-8", "replace") if p is not None else "[]"
            self.windows = [w for w in json.loads(raw) if not w.get("skip")]
        except Exception:
            self.windows = []
        self.on_change(self.windows)

    def _send(self, wid, ctype, data):
        w = self.d.create_resource_object("window", wid)
        ev = xevent.ClientMessage(window=w, client_type=self.d.intern_atom(ctype),
                                  data=(32, (list(data) + [0] * 5)[:5]))
        self.root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        self.d.flush()

    def activate(self, wid):
        self._send(wid, "_NET_ACTIVE_WINDOW", [2, 0, 0])

    def minimize(self, wid):
        self._send(wid, "WM_CHANGE_STATE", [3])

    def close(self, wid):
        self._send(wid, "_NET_CLOSE_WINDOW", [0, 2])

    def set_struts(self, gdk_window, left, right, top, bottom, ranges):
        """ranges: (start,end) along the strut edge"""
        xid = gdk_window.get_xid()
        w = self.d.create_resource_object("window", xid)
        partial = [left, right, top, bottom, 0, 0, 0, 0, 0, 0, 0, 0]
        if top:
            partial[8:10] = ranges
        if bottom:
            partial[10:12] = ranges
        w.change_property(self.d.intern_atom("_NET_WM_STRUT_PARTIAL"), Xatom.CARDINAL, 32, partial)
        w.change_property(self.d.intern_atom("_NET_WM_STRUT"), Xatom.CARDINAL, 32, [left, right, top, bottom])
        self.d.flush()


# ============================================================================ flyouts
class Flyout(Gtk.Window):
    """Borderless popup window that hides itself when it loses focus."""

    def __init__(self, name):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_name(name)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_resizable(False)
        self._at = 0.0
        self.connect("focus-out-event", self._lost)
        self.connect("key-press-event", self._key)
        self.connect("delete-event", lambda *a: (self.hide(), True)[1])

    def _lost(self, *a):
        if time.monotonic() - self._at > 0.3:
            self.hide()
        return False

    def _key(self, w, e):
        if e.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return False

    def popup_at(self, x, y):
        self._at = time.monotonic()
        self.move(x, y)
        self.show_all()
        self.present()


class Calendar(Flyout):
    def __init__(self):
        super().__init__("calendar")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
        self.time_lbl = Gtk.Label(xalign=0)
        self.time_lbl.set_markup("")
        box.pack_start(self.time_lbl, False, False, 0)
        self.cal = Gtk.Calendar()
        box.pack_start(self.cal, False, False, 0)
        self.add(box)

    def refresh(self):
        now = time.localtime()
        self.time_lbl.set_markup('<span size="22000" weight="bold">%s</span>\n<span alpha="65%%">%s</span>' %
                                 (time.strftime("%H:%M", now), time.strftime("%A, %d %B %Y", now)))
        self.cal.select_month(now.tm_mon - 1, now.tm_year)
        self.cal.select_day(now.tm_mday)


class Launcher(Flyout):
    W, H = 560, 540

    def __init__(self, shell):
        super().__init__("launcher")
        self.shell = shell
        self.set_default_size(self.W, self.H)
        self.set_size_request(self.W, self.H)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.entry = Gtk.SearchEntry(placeholder_text="Search apps or type a command")
        self.entry.connect("search-changed", lambda e: self.populate())
        self.entry.connect("activate", self.on_activate)
        self.entry.connect("key-press-event", self.on_entry_key)
        box.pack_start(self.entry, False, False, 0)
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list.connect("row-activated", lambda lb, row: self.launch(row.item))
        sw.add(self.list)
        box.pack_start(sw, True, True, 0)
        self.hint = Gtk.Label(label="Enter to open  ·  Esc to close", xalign=0, margin=10)
        self.hint.get_style_context().add_class("dim")
        box.pack_start(self.hint, False, False, 0)
        self.add(box)
        self.items, self._bins = [], None

    # ---- data
    def collect(self):
        cfg = self.shell.cfg
        items = [dict(name=a["name"], icon=a.get("icon", "▫"), exec=a["exec"], desc=a.get("desc", ""), terminal=False)
                 for a in cfg.get("apps", [])]
        seen = {i["name"].lower() for i in items}
        dirs = ["/usr/share/applications", os.path.expanduser("~/.local/share/applications")]
        for f in sorted(sum((glob.glob(os.path.join(d, "*.desktop")) for d in dirs), [])):
            cp = configparser.ConfigParser(strict=False, interpolation=None)
            try:
                cp.read(f, encoding="utf-8")
                e = cp["Desktop Entry"]
            except (configparser.Error, KeyError, UnicodeDecodeError):
                continue
            if e.get("Type") != "Application" or e.get("NoDisplay", "").lower() == "true" \
                    or e.get("Hidden", "").lower() == "true" or not e.get("Exec") or not e.get("Name"):
                continue
            if e["Name"].lower() in seen:
                continue
            seen.add(e["Name"].lower())
            items.append(dict(name=e["Name"], icon="🧩", exec=re.sub(r"%[a-zA-Z]", "", e["Exec"]).strip(),
                              desc=e.get("Comment", ""), terminal=e.get("Terminal", "").lower() == "true"))
        self.items = items

    def binaries(self):
        if self._bins is None:
            names = set()
            for d in os.environ.get("PATH", "/usr/bin:/bin").split(":"):
                try:
                    for n in os.listdir(d):
                        if os.access(os.path.join(d, n), os.X_OK) and not os.path.isdir(os.path.join(d, n)):
                            names.add(n)
                except OSError:
                    pass
            self._bins = sorted(names)
        return self._bins

    def populate(self):
        for r in self.list.get_children():
            self.list.remove(r)
        q = self.entry.get_text().strip().lower()
        rows = [i for i in self.items if not q or q in i["name"].lower() or q in i["desc"].lower()]
        if len(q) >= 2:
            known = {i["exec"].split()[0] for i in rows}
            rows += [dict(name=b, icon="▸", exec=b, desc="Installed program", terminal=False)
                     for b in self.binaries() if q in b.lower() and b not in known][:30]
        if q and not rows:
            rows = [dict(name="Run “%s”" % q, icon="▸", exec=self.entry.get_text().strip(),
                         desc="Run as a command", terminal=False)]
        for it in rows:
            row = Gtk.ListBoxRow()
            row.item = it
            h = Gtk.Box(spacing=14)
            ic = Gtk.Label()
            ic.set_markup(emoji_markup(it["icon"], 30))
            ic.set_size_request(40, -1)
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            n = Gtk.Label(label=it["name"], xalign=0)
            n.get_style_context().add_class("app-name")
            v.pack_start(n, False, False, 0)
            if it["desc"]:
                d = Gtk.Label(label=it["desc"], xalign=0, ellipsize=Pango.EllipsizeMode.END)
                d.get_style_context().add_class("dim")
                v.pack_start(d, False, False, 0)
            h.pack_start(ic, False, False, 0)
            h.pack_start(v, True, True, 0)
            row.add(h)
            self.list.add(row)
        self.list.show_all()
        first = self.list.get_row_at_index(0)
        if first:
            self.list.select_row(first)

    def on_entry_key(self, w, e):
        if e.keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            cur = self.list.get_selected_row()
            idx = cur.get_index() if cur else -1
            idx += 1 if e.keyval == Gdk.KEY_Down else -1
            row = self.list.get_row_at_index(max(0, idx))
            if row:
                self.list.select_row(row)
            return True
        return False

    def on_activate(self, *a):
        row = self.list.get_selected_row() or self.list.get_row_at_index(0)
        if row:
            self.launch(row.item)

    def launch(self, it):
        self.hide()
        if it.get("terminal"):
            common.run(common.app_cmd("terminal.py", "-e", *it["exec"].split()))
        else:
            common.run(it["exec"])

    # ---- show/hide
    def toggle(self):
        if self.get_visible():
            self.hide()
        else:
            self.show_launcher()

    def show_launcher(self):
        self.collect()
        self.entry.set_text("")
        self.populate()
        mon = monitor_geometry()
        ph = self.shell.cfg["desktop"]["panel_height"]
        bottom = self.shell.cfg["desktop"]["panel_position"] == "bottom"
        x = mon.x + 8
        y = mon.y + mon.height - ph - self.H - 8 if bottom else mon.y + ph + 8
        self.popup_at(x, max(0, y))
        self.entry.grab_focus()


# ============================================================================ panel
class Panel(Gtk.Window):
    def __init__(self, shell):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.shell = shell
        self.set_name("panel")
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_resizable(False)
        self.task_sig = None
        self.menu_open = None

        self.bar = Gtk.Box(spacing=6, margin_start=8, margin_end=8, margin_top=4, margin_bottom=4)
        self.add(self.bar)
        self.launch_btn = Gtk.Button(label="Apps")
        self.launch_btn.get_style_context().add_class("launch-btn")
        self.launch_btn.connect("clicked", lambda b: shell.launcher.toggle())
        self.bar.pack_start(self.launch_btn, False, False, 0)
        self.pinned = Gtk.Box(spacing=2)
        self.bar.pack_start(self.pinned, False, False, 0)
        self.bar.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 6)
        self.tasks = Gtk.Box(spacing=4)
        self.bar.pack_start(self.tasks, True, True, 0)
        self.build_tray()
        self.connect("map-event", lambda *a: self.set_struts())
        self.connect("realize", lambda *a: self.set_struts())

    # ---- tray
    def build_tray(self):
        tray = Gtk.Box(spacing=6)
        self.wifi = Gtk.Button(label="…")
        self.wifi.connect("clicked", lambda b: common.run(common.app_cmd("settings.py", "--page", "wifi")))
        self.vol = Gtk.Button(label="…")
        self.vol.connect("clicked", lambda b: self.shell.volume_toggle_mute())
        self.vol.connect("button-press-event", self.on_vol_press)
        self.vol.add_events(Gdk.EventMask.SCROLL_MASK)
        self.vol.connect("scroll-event", lambda w, e: self.shell.volume_step(5 if e.direction == Gdk.ScrollDirection.UP else -5))
        pill = Gtk.Box()
        pill.get_style_context().add_class("pill")
        self.bat = Gtk.Label()
        self.cpu = Gtk.Label()
        pill.pack_start(self.bat, False, False, 0)
        pill.pack_start(self.cpu, False, False, 0)
        self.clock = Gtk.Button(label="")
        self.clock.connect("clicked", self.on_clock)
        self.power = Gtk.Button(label="⏻")
        self.power.set_tooltip_text("Power")
        self.power.connect("clicked", self.on_power)
        for w in (self.wifi, self.vol, pill, self.clock, self.power):
            tray.pack_start(w, False, False, 0)
        self.bar.pack_end(tray, False, False, 0)

    def on_vol_press(self, w, e):
        if e.button == 3:
            common.run(common.app_cmd("settings.py", "--page", "sound"))
            return True
        return False

    def on_clock(self, btn):
        cal = self.shell.calendar
        if cal.get_visible():
            cal.hide()
            return
        cal.refresh()
        mon = monitor_geometry()
        pos = self.shell.cfg["desktop"]
        ph = pos["panel_height"]
        x = mon.x + mon.width - 280 - 8
        y = mon.y + mon.height - ph - 380 if pos["panel_position"] == "bottom" else mon.y + ph + 8
        cal.popup_at(x, max(0, y))

    def on_power(self, btn):
        m = Gtk.Menu()
        for label, act in (("Log out", "logout"), ("Restart", "reboot"), ("Shut down", "poweroff")):
            it = Gtk.MenuItem(label=label)
            it.connect("activate", lambda i, a=act: self.shell.power(a))
            m.append(it)
        m.show_all()
        self.menu_open = m
        m.popup_at_widget(btn, *self.anchors())

    def anchors(self):
        if self.shell.cfg["desktop"]["panel_position"] == "bottom":
            return (Gdk.Gravity.NORTH_EAST, Gdk.Gravity.SOUTH_EAST, None)
        return (Gdk.Gravity.SOUTH_EAST, Gdk.Gravity.NORTH_EAST, None)

    # ---- layout
    def place(self):
        mon = monitor_geometry()
        d = self.shell.cfg["desktop"]
        h = int(d["panel_height"])
        y = mon.y + mon.height - h if d["panel_position"] == "bottom" else mon.y
        self.get_style_context().remove_class("top")
        if d["panel_position"] == "top":
            self.get_style_context().add_class("top")
        self.set_size_request(mon.width, h)
        self.resize(mon.width, h)
        self.move(mon.x, y)
        self.set_struts()

    def set_struts(self):
        gw = self.get_window()
        if gw is None:
            return
        d = self.shell.cfg["desktop"]
        mon = monitor_geometry()
        scr = Gdk.Screen.get_default()
        h = int(d["panel_height"])
        rng = (mon.x, mon.x + mon.width - 1)
        if d["panel_position"] == "bottom":
            self.shell.tracker.set_struts(gw, 0, 0, 0, h + (scr.get_height() - (mon.y + mon.height)), rng)
        else:
            self.shell.tracker.set_struts(gw, 0, 0, h + mon.y, 0, rng)

    def rebuild_pinned(self):
        for c in self.pinned.get_children():
            self.pinned.remove(c)
        cfg = self.shell.cfg
        pt = int(cfg["desktop"]["panel_height"] * 0.30)
        for name in cfg.get("pinned_apps", []):
            app = common.find_app(cfg, name)
            if not app:
                continue
            b = Gtk.Button()
            b.get_style_context().add_class("pinned")
            lab = Gtk.Label()
            lab.set_markup('<span size="%d">%s</span>' % (pt * 1024, GLib.markup_escape_text(app.get("icon", "▫"))))
            b.add(lab)
            b.set_tooltip_text(app["name"])
            b.connect("clicked", lambda w, a=app: common.run(a["exec"]))
            self.pinned.pack_start(b, False, False, 0)
        self.pinned.show_all()

    def update_tasks(self, windows):
        sig = json.dumps([(w["id"], w["title"], w["active"] and not w["minimized"], w["minimized"]) for w in windows])
        if sig == self.task_sig:
            return
        self.task_sig = sig
        for c in self.tasks.get_children():
            self.tasks.remove(c)
        for w in windows:
            title = w["title"] or w.get("class") or "Window"
            b = Gtk.ToggleButton()
            b.get_style_context().add_class("task")
            lab = Gtk.Label(label=("·" if w["minimized"] else "") + title, xalign=0,
                            ellipsize=Pango.EllipsizeMode.END, max_width_chars=22)
            b.add(lab)
            b.set_active(w["active"] and not w["minimized"])
            b.connect("button-press-event", self.on_task_press, w)
            self.tasks.pack_start(b, False, False, 0)
        self.tasks.show_all()

    def on_task_press(self, btn, ev, w):
        t = self.shell.tracker
        if ev.button == 1:
            if w["active"] and not w["minimized"]:
                t.minimize(w["id"])
            else:
                t.activate(w["id"])
        elif ev.button == 3:
            m = Gtk.Menu()
            for label, fn in (("Restore" if w["minimized"] else "Minimize",
                               lambda: t.activate(w["id"]) if w["minimized"] else t.minimize(w["id"])),
                              ("Close", lambda: t.close(w["id"]))):
                it = Gtk.MenuItem(label=label)
                it.connect("activate", lambda i, f=fn: f())
                m.append(it)
            m.show_all()
            m.popup_at_widget(btn, *self.anchors())
        return True


# ============================================================================ desktop
class Desktop(Gtk.Window):
    def __init__(self, shell):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.shell = shell
        self.set_name("desktop")
        self.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_app_paintable(True)
        self._pb_key, self._pb = None, None

        self.wall = Gtk.DrawingArea()
        self.wall.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.wall.connect("draw", self.draw_wall)
        self.wall.connect("button-press-event", self.on_bg_press)
        ov = Gtk.Overlay()
        ov.add(self.wall)
        self.flow = Gtk.FlowBox()
        self.flow.set_orientation(Gtk.Orientation.VERTICAL)
        self.flow.set_halign(Gtk.Align.START)
        self.flow.set_valign(Gtk.Align.START)
        self.flow.set_homogeneous(True)
        self.flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.flow.set_activate_on_single_click(False)
        self.flow.set_margin_top(14)
        self.flow.set_margin_start(14)
        self.flow.set_row_spacing(6)
        self.flow.set_column_spacing(6)
        self.flow.connect("child-activated", lambda fb, ch: self.open_item(ch.item))
        self.flow.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.flow.connect("button-press-event", self.on_bg_press)
        ov.add_overlay(self.flow)
        self.add(ov)
        self.monitor = None
        os.makedirs(DESKTOP_DIR, exist_ok=True)
        try:
            self.monitor = Gio.File.new_for_path(DESKTOP_DIR).monitor_directory(Gio.FileMonitorFlags.NONE, None)
            self.monitor.connect("changed", lambda *a: GLib.timeout_add(250, self._deferred_rebuild))
        except GLib.Error:
            pass

    def _deferred_rebuild(self):
        self.rebuild()
        return False

    def place(self):
        scr = Gdk.Screen.get_default()
        w, h = scr.get_width(), scr.get_height()
        self.set_default_size(w, h)
        self.resize(w, h)
        self.move(0, 0)
        self.flow.set_max_children_per_line(max(1, int((h - 140) // (self.shell.cfg["desktop"]["icon_size"] + 64))))
        self.wall.queue_draw()

    # ---- wallpaper
    def draw_wall(self, w, cr):
        a = w.get_allocation()
        W, H = a.width, a.height
        pb = self.scaled(W, H)
        if pb:
            Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
            cr.paint()
        else:
            p = common.palette(self.shell.cfg)
            import cairo
            g = cairo.LinearGradient(0, 0, W, H)
            r, gg, b, _ = common.parse_color(p["win_bg"])
            ar, ag, ab, _ = common.parse_color(p["accent"])
            g.add_color_stop_rgb(0, r, gg, b)
            g.add_color_stop_rgb(1, r * 0.6 + ar * 0.4, gg * 0.6 + ag * 0.4, b * 0.6 + ab * 0.4)
            cr.set_source(g)
            cr.paint()
        return False

    def scaled(self, W, H):
        path = common.wallpaper_path(self.shell.cfg)
        key = (path, W, H, os.path.getmtime(path) if os.path.exists(path) else 0)
        if key == self._pb_key:
            return self._pb
        self._pb_key, self._pb = key, None
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(path)
            s = max(W / pb.get_width(), H / pb.get_height())
            nw, nh = max(W, int(pb.get_width() * s + 0.99)), max(H, int(pb.get_height() * s + 0.99))
            pb = pb.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)
            self._pb = pb.new_subpixbuf((nw - W) // 2, (nh - H) // 2, W, H)
        except GLib.Error:
            self._pb = None
        return self._pb

    def invalidate(self):
        self._pb_key = None
        self.wall.queue_draw()

    # ---- icons
    def items(self):
        cfg = self.shell.cfg
        out = []
        for n in cfg.get("desktop_shortcuts", []):
            a = common.find_app(cfg, n)
            if a:
                out.append(dict(kind="app", name=a["name"], icon=a.get("icon", "▫"), exec=a["exec"]))
        out.append(dict(kind="path", name="Trash", icon="🗑️", path=fileops.TRASH_FILES, trash=True))
        try:
            for n in sorted(os.listdir(DESKTOP_DIR), key=str.lower):
                if n.startswith("."):
                    continue
                p = os.path.join(DESKTOP_DIR, n)
                k = common.classify(p)
                out.append(dict(kind="path", name=n, path=p,
                                icon={"dir": "📁", "image": "🖼️", "archive": "🗜️", "text": "📄"}.get(k, "📦")))
        except OSError:
            pass
        return out

    def rebuild(self):
        for c in self.flow.get_children():
            self.flow.remove(c)
        size = int(self.shell.cfg["desktop"]["icon_size"])
        for it in self.items():
            child = Gtk.FlowBoxChild()
            child.item = it
            eb = Gtk.EventBox()
            eb.set_visible_window(False)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            ic = Gtk.Label()
            ic.set_markup(emoji_markup(it["icon"], size))
            lab = Gtk.Label(label=it["name"], justify=Gtk.Justification.CENTER, wrap=True,
                            wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=12, ellipsize=Pango.EllipsizeMode.END,
                            lines=2)
            lab.get_style_context().add_class("desktop-label")
            box.pack_start(ic, False, False, 0)
            box.pack_start(lab, False, False, 0)
            box.set_size_request(size + 28, -1)
            eb.add(box)
            eb.connect("button-press-event", self.on_item_press, child)
            child.add(eb)
            self.flow.add(child)
        self.flow.show_all()

    def open_item(self, it):
        if it["kind"] == "app":
            common.run(it["exec"])
        elif it.get("trash"):
            common.run(common.app_cmd("file_manager.py", "trash://"))
        elif os.path.exists(it["path"]):
            common.open_path(it["path"])

    # ---- menus
    def on_item_press(self, eb, ev, child):
        self.shell.hide_flyouts()
        if ev.button == 3:
            self.flow.select_child(child)
            it = child.item
            m = Gtk.Menu()
            a = Gtk.MenuItem(label="Open")
            a.connect("activate", lambda i: self.open_item(it))
            m.append(a)
            if it["kind"] == "path" and not it.get("trash"):
                d = Gtk.MenuItem(label="Move to Trash")
                d.connect("activate", lambda i: self.trash(it["path"]))
                m.append(d)
            m.show_all()
            m.popup_at_pointer(ev)
            return True
        return False

    def trash(self, path):
        try:
            fileops.trash_item(path)
        except OSError as e:
            ui.notify(None, "Cannot move to Trash", str(e))

    def on_bg_press(self, w, ev):
        self.shell.hide_flyouts()
        if ev.button == 3:
            m = Gtk.Menu()
            def add(label, fn, target=m):
                it = Gtk.MenuItem(label=label)
                it.connect("activate", lambda i: fn())
                target.append(it)
            add("New Folder", self.new_folder)
            add("Open Terminal", lambda: common.run(common.app_cmd("terminal.py")))
            wp = Gtk.MenuItem(label="Change Wallpaper")
            sub = Gtk.Menu()
            for f in sorted(glob.glob(os.path.join(common.WALLPAPER_DIR, "*.png")) +
                            glob.glob(os.path.join(common.WALLPAPER_DIR, "*.jpg"))):
                add(os.path.splitext(os.path.basename(f))[0].title(), lambda f=f: self.shell.set_wallpaper(f), sub)
            add("Browse…", self.browse_wallpaper, sub)
            wp.set_submenu(sub)
            m.append(wp)
            add("Settings", lambda: common.run(common.app_cmd("settings.py")))
            m.show_all()
            m.popup_at_pointer(ev)
            return True
        self.flow.unselect_all()
        return False

    def new_folder(self):
        name = ui.ask_text(None, "New Folder", "Folder name", "New Folder", "Create")
        if name:
            try:
                os.makedirs(os.path.join(DESKTOP_DIR, name), exist_ok=False)
            except OSError as e:
                ui.notify(None, "Cannot create folder", str(e))

    def browse_wallpaper(self):
        dlg = Gtk.FileChooserDialog(title="Choose wallpaper", action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Use wallpaper", Gtk.ResponseType.OK)
        flt = Gtk.FileFilter()
        flt.set_name("Images")
        for pat in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp"):
            flt.add_pattern(pat)
        dlg.add_filter(flt)
        if dlg.run() == Gtk.ResponseType.OK:
            self.shell.set_wallpaper(dlg.get_filename())
        dlg.destroy()


# ============================================================================ shell
class Shell:
    def __init__(self):
        common.apply_theme()
        self.cfg = common.load_config()
        self.vol_state = (50, False)
        self.tracker = WindowTracker(lambda w: self.panel.update_tasks(w) if hasattr(self, "panel") else None)
        self.desktop = Desktop(self)
        self.launcher = Launcher(self)
        self.calendar = Calendar()
        self.panel = Panel(self)
        self.apply_config(first=True)
        self.tracker.refresh()
        self.serve()
        scr = Gdk.Screen.get_default()
        scr.connect("size-changed", lambda *a: self.relayout())
        scr.connect("monitors-changed", lambda *a: self.relayout())
        psutil.cpu_percent(None)
        GLib.timeout_add(1000, self.tick_clock)
        GLib.timeout_add_seconds(2, self.tick_stats)
        GLib.timeout_add_seconds(5, self.tick_net)
        GLib.timeout_add_seconds(3, self.tick_vol)
        self.tick_clock()
        self.tick_stats()
        self.tick_net()
        self.tick_vol()

    # ---- config / layout
    def apply_config(self, first=False):
        self.panel.rebuild_pinned()
        self.desktop.rebuild()
        self.relayout(first)

    def relayout(self, first=False):
        self.desktop.place()
        self.panel.place()
        if first:
            self.desktop.show_all()
            self.panel.show_all()
        self.panel.set_struts()

    def reload(self):
        common.apply_theme()
        self.cfg = common.load_config()
        self.desktop.invalidate()
        self.apply_config()
        return "ok"

    def hide_flyouts(self):
        self.launcher.hide()
        self.calendar.hide()

    def set_wallpaper(self, path):
        cfg = common.load_config()
        cfg["desktop"]["wallpaper_path"] = path
        try:
            common.save_config(cfg)
        except OSError as e:
            ui.notify(None, "Cannot save settings", str(e))
            return
        self.reload()

    # ---- control socket
    def serve(self):
        path = common.shell_sock_path()
        try:
            os.unlink(path)
        except OSError:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        os.chmod(path, 0o600)
        self.sock.listen(4)
        self.sock.setblocking(False)
        GLib.io_add_watch(self.sock.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN, self._accept)

    def _accept(self, fd, cond):
        try:
            conn, _ = self.sock.accept()
        except (BlockingIOError, InterruptedError):
            return True
        conn.settimeout(0.3)
        try:
            cmd = conn.recv(256).decode().strip()
            conn.sendall((self.handle(cmd) + "\n").encode())
        except Exception as e:                      # keep the shell alive whatever a client sends
            print("socket error:", e)
        finally:
            conn.close()
        return True

    def handle(self, cmd):
        if cmd == "reload":
            return self.reload()
        if cmd == "toggle-launcher":
            self.launcher.toggle()
            return "ok"
        if cmd == "show-launcher":
            self.launcher.show_launcher()
            return "ok"
        if cmd == "hide-launcher":
            self.launcher.hide()
            return "ok"
        if cmd == "ping":
            return "pong"
        return "unknown command"

    # ---- tray ticks
    def tick_clock(self):
        self.panel.clock.set_label(time.strftime("%a %d %b   %H:%M"))
        return True

    def tick_stats(self):
        b = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if b is None:
            self.panel.bat.set_text("AC")
        else:
            self.panel.bat.set_text(("⚡ " if b.power_plugged else "🔋 ") + "%d%%" % b.percent)
        self.panel.cpu.set_text("CPU %d%%" % psutil.cpu_percent(None))
        return True

    def tick_net(self):
        def work():
            out = sh_out(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev"])
            if out is None:
                return "No network"
            wired = None
            for line in out.splitlines():
                p = re.split(r"(?<!\\):", line)
                if len(p) >= 4 and p[2] == "connected":
                    if p[1] == "wifi":
                        return "📶 " + p[3].replace("\\:", ":")
                    if p[1] == "ethernet":
                        wired = "🌐 Wired"
            return wired or "📵 Offline"
        bg(work, self.panel.wifi.set_label)
        return True

    def tick_vol(self):
        def work():
            v = sh_out(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
            m = sh_out(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
            if v is None:
                return None
            mm = re.search(r"(\d+)%", v)
            return (int(mm.group(1)) if mm else 0, bool(m and "yes" in m))
        def done(res):
            if res is None:
                self.panel.vol.set_label("🔇 n/a")
                return
            self.vol_state = res
            self.panel.vol.set_label(("🔇 muted" if res[1] else ("🔊 %d%%" % res[0])))
        bg(work, done)
        return True

    def volume_step(self, delta):
        sign = "+" if delta > 0 else "-"
        bg(lambda: sh_out(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "%s%d%%" % (sign, abs(delta))]),
           lambda r: self.tick_vol())

    def volume_toggle_mute(self):
        bg(lambda: sh_out(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"]), lambda r: self.tick_vol())

    def power(self, action):
        if action == "logout":
            if ui.confirm(None, "Log out", "Close all windows and end this session?", "Log out"):
                common.signal_wm(15)
            return
        label = "Restart" if action == "reboot" else "Shut down"
        if ui.confirm(None, label, "%s this computer now?" % label, label, danger=True):
            subprocess.Popen(["sudo", "-n", "systemctl", action])


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--reload", "--toggle-launcher"):
        print(common.send_shell(sys.argv[1][2:]) or "shell not running")
        return
    Shell()
    Gtk.main()


if __name__ == "__main__":
    main()
