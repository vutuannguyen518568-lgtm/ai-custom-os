#!/usr/bin/env python3
"""Settings — control center: Appearance & Layout, Display, Wi-Fi & Network, Sound, System Info.
Usage: settings.py [--page appearance|display|wifi|sound|system]"""
import glob
import os
import platform
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf, Pango
import psutil
from core import common, ui


def sh(argv, timeout=15):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.SubprocessError) as e:
        return 127, "", str(e)


def bg(fn, done=None):
    def work():
        res = fn()
        if done:
            GLib.idle_add(lambda: (done(res), False)[1])
    threading.Thread(target=work, daemon=True).start()


def page_box(title):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=24)
    t = Gtk.Label(label=title, xalign=0)
    t.get_style_context().add_class("page-title")
    box.pack_start(t, False, False, 0)
    return box


def row(label, widget, hint=None):
    h = Gtk.Box(spacing=16)
    left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    left.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
    if hint:
        d = Gtk.Label(label=hint, xalign=0, wrap=True)
        d.get_style_context().add_class("dim")
        left.pack_start(d, False, False, 0)
    left.set_size_request(220, -1)
    h.pack_start(left, False, False, 0)
    h.pack_start(widget, True, True, 0)
    return h


# ====================================================================== Appearance & Layout
class AppearancePage(Gtk.ScrolledWindow):
    def __init__(self, win):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.win = win
        self.loading = True
        cfg = common.load_config()
        d = cfg["desktop"]
        box = page_box("Appearance & Layout")

        mode = Gtk.Box(spacing=6)
        self.m_dark = Gtk.RadioButton.new_with_label_from_widget(None, "Dark")
        self.m_light = Gtk.RadioButton.new_with_label_from_widget(self.m_dark, "Light")
        (self.m_dark if d["mode"] == "dark" else self.m_light).set_active(True)
        for b in (self.m_dark, self.m_light):
            b.connect("toggled", lambda w: self.changed())
            mode.pack_start(b, False, False, 8)
        box.pack_start(row("Theme", mode), False, False, 0)

        sw = Gtk.Box(spacing=8)
        self.swatches, self.accent = [], d["accent"]
        first = None
        for name, hexv in common.ACCENTS:
            b = Gtk.RadioButton.new_from_widget(first)
            b.set_mode(False)
            first = first or b
            b.get_style_context().add_class("swatch")
            b.set_tooltip_text(name)
            css = Gtk.CssProvider()
            css.load_from_data(("button { background-image: none; background-color: %s; }" % hexv).encode())
            b.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
            b.hexv = hexv
            b.set_active(hexv.lower() == self.accent.lower())
            b.connect("toggled", self.on_swatch)
            self.swatches.append(b)
            sw.pack_start(b, False, False, 0)
        self.custom = Gtk.ColorButton()
        rgba = Gdk.RGBA()
        rgba.parse(self.accent)
        self.custom.set_rgba(rgba)
        self.custom.set_tooltip_text("Custom colour")
        self.custom.connect("color-set", self.on_custom)
        sw.pack_start(self.custom, False, False, 8)
        box.pack_start(row("Accent colour", sw), False, False, 0)

        self.pos = Gtk.ComboBoxText()
        self.pos.append("bottom", "Bottom")
        self.pos.append("top", "Top")
        self.pos.set_active_id(d["panel_position"])
        self.pos.connect("changed", lambda w: self.changed())
        box.pack_start(row("Taskbar position", self.pos), False, False, 0)
        self.ph = Gtk.SpinButton.new_with_range(32, 96, 2)
        self.ph.set_value(d["panel_height"])
        self.ph.connect("value-changed", lambda w: self.changed())
        box.pack_start(row("Taskbar height", self.ph, "pixels"), False, False, 0)
        self.isz = Gtk.SpinButton.new_with_range(32, 128, 4)
        self.isz.set_value(d["icon_size"])
        self.isz.connect("value-changed", lambda w: self.changed())
        box.pack_start(row("Desktop icon size", self.isz, "pixels"), False, False, 0)

        wl = Gtk.Label(label="Wallpaper", xalign=0)
        box.pack_start(wl, False, False, 0)
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.SINGLE, max_children_per_line=4, min_children_per_line=2,
                                homogeneous=True, column_spacing=10, row_spacing=10)
        self.flow.connect("child-activated", self.on_wall)
        box.pack_start(self.flow, False, False, 0)
        browse = Gtk.Button(label="Browse for an image…", halign=Gtk.Align.START)
        browse.get_style_context().add_class("boxed")
        browse.connect("clicked", self.on_browse)
        box.pack_start(browse, False, False, 0)
        self.fill_wallpapers(common.wallpaper_path(cfg))
        self.add(box)
        self.loading = False

    def fill_wallpapers(self, current):
        for c in self.flow.get_children():
            self.flow.remove(c)
        files = sorted(glob.glob(os.path.join(common.WALLPAPER_DIR, "*.png")) + glob.glob(os.path.join(common.WALLPAPER_DIR, "*.jpg")))
        if current and current not in files and os.path.exists(current):
            files.append(current)
        for f in files:
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(f, 168, 96, False)
            except GLib.Error:
                continue
            ch = Gtk.FlowBoxChild()
            ch.path = f
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            v.pack_start(Gtk.Image.new_from_pixbuf(pb), False, False, 0)
            v.pack_start(Gtk.Label(label=os.path.splitext(os.path.basename(f))[0].title()), False, False, 0)
            ch.add(v)
            self.flow.add(ch)
            if f == current:
                self.flow.select_child(ch)
        self.flow.show_all()

    def collect(self):
        cfg = common.load_config()
        d = cfg["desktop"]
        d["mode"] = "dark" if self.m_dark.get_active() else "light"
        d["accent"] = self.accent
        d["panel_position"] = self.pos.get_active_id() or "bottom"
        d["panel_height"] = int(self.ph.get_value())
        d["icon_size"] = int(self.isz.get_value())
        return cfg

    def changed(self):
        if self.loading:
            return
        self.win.save_and_reload(self.collect())

    def on_swatch(self, b):
        if b.get_active() and not self.loading:
            self.accent = b.hexv
            rgba = Gdk.RGBA()
            rgba.parse(b.hexv)
            self.custom.set_rgba(rgba)
            self.changed()

    def on_custom(self, btn):
        c = btn.get_rgba()
        self.accent = "#%02x%02x%02x" % (round(c.red * 255), round(c.green * 255), round(c.blue * 255))
        self.loading = True
        for s in self.swatches:
            s.set_active(False)
        self.loading = False
        self.changed()

    def on_wall(self, fb, child):
        cfg = self.collect()
        cfg["desktop"]["wallpaper_path"] = child.path
        self.win.save_and_reload(cfg)

    def on_browse(self, btn):
        dlg = Gtk.FileChooserDialog(title="Choose wallpaper", transient_for=self.win, action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Use wallpaper", Gtk.ResponseType.OK)
        flt = Gtk.FileFilter()
        flt.set_name("Images")
        for pat in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp"):
            flt.add_pattern(pat)
        dlg.add_filter(flt)
        path = dlg.get_filename() if dlg.run() == Gtk.ResponseType.OK else None
        dlg.destroy()
        if path:
            cfg = self.collect()
            cfg["desktop"]["wallpaper_path"] = path
            self.win.save_and_reload(cfg)
            self.fill_wallpapers(path)


# ====================================================================== Display
class DisplayPage(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=24)
        self.win = win
        t = Gtk.Label(label="Display", xalign=0)
        t.get_style_context().add_class("page-title")
        self.pack_start(t, False, False, 0)
        self.outputs = {}
        self.out_combo = Gtk.ComboBoxText()
        self.out_combo.connect("changed", self.on_output)
        self.res_combo = Gtk.ComboBoxText()
        self.rate_combo = Gtk.ComboBoxText()
        self.res_combo.connect("changed", self.on_res)
        self.pack_start(row("Screen", self.out_combo), False, False, 0)
        self.pack_start(row("Resolution", self.res_combo), False, False, 0)
        self.pack_start(row("Refresh rate", self.rate_combo), False, False, 0)
        self.msg = Gtk.Label(xalign=0, wrap=True)
        self.msg.get_style_context().add_class("dim")
        apply_btn = Gtk.Button(label="Apply", halign=Gtk.Align.START)
        apply_btn.get_style_context().add_class("suggested")
        apply_btn.connect("clicked", self.apply)
        self.pack_start(apply_btn, False, False, 0)
        self.pack_start(self.msg, False, False, 0)
        self.refresh()

    def refresh(self):
        rc, out, err = sh(["xrandr", "--query"])
        if rc != 0:
            self.msg.set_text("Could not query displays: " + (err.strip() or "xrandr failed"))
            return
        self.outputs, cur, order = {}, None, []
        for line in out.splitlines():
            m = re.match(r"^(\S+) connected", line)
            if m:
                cur = m.group(1)
                self.outputs[cur] = {"modes": {}, "current": None}
                order.append(cur)
                continue
            if re.match(r"^\S+ (disconnected|unknown)", line):
                cur = None
                continue
            m = re.match(r"^\s+(\d+x\d+)\s+(.*)$", line)
            if m and cur:
                rates = []
                for tok in m.group(2).split():
                    r = re.match(r"^(\d+(?:\.\d+)?)([*+]*)$", tok)
                    if r:
                        rates.append(r.group(1))
                        if "*" in r.group(2):
                            self.outputs[cur]["current"] = (m.group(1), r.group(1))
                self.outputs[cur]["modes"][m.group(1)] = rates
        self.out_combo.remove_all()
        for o in order:
            self.out_combo.append(o, o)
        if order:
            self.out_combo.set_active(0)

    def on_output(self, combo):
        o = combo.get_active_text()
        self.res_combo.remove_all()
        if not o:
            return
        for mode in self.outputs[o]["modes"]:
            self.res_combo.append(mode, mode)
        cur = self.outputs[o]["current"]
        if cur and cur[0] in self.outputs[o]["modes"]:
            self.res_combo.set_active_id(cur[0])
        else:
            self.res_combo.set_active(0)

    def on_res(self, combo):
        o, r = self.out_combo.get_active_text(), combo.get_active_text()
        self.rate_combo.remove_all()
        if not o or not r:
            return
        for rate in self.outputs[o]["modes"].get(r, []):
            self.rate_combo.append(rate, rate + " Hz")
        cur = self.outputs[o]["current"]
        if cur and cur[0] == r:
            self.rate_combo.set_active_id(cur[1])
        else:
            self.rate_combo.set_active(0)

    def apply(self, btn):
        o, r, rate = self.out_combo.get_active_text(), self.res_combo.get_active_text(), self.rate_combo.get_active_id()
        if not (o and r):
            return
        argv = ["xrandr", "--output", o, "--mode", r] + (["--rate", rate] if rate else [])
        rc, _, err = sh(argv)
        if rc != 0:
            self.msg.set_text("xrandr refused that mode: " + err.strip())
            return
        cfg = common.load_config()
        cfg["display"] = {"output": o, "mode": r, "rate": rate or ""}
        try:
            common.save_config(cfg)
        except OSError:
            pass
        self.msg.set_text("Applied %s at %s. It will be restored on the next start." % (o, r))
        GLib.timeout_add(600, lambda: (common.reload_all(), False)[1])


# ====================================================================== Wi-Fi & Network
def split_terse(line):
    return [p.replace("\\:", ":").replace("\\\\", "\\") for p in re.split(r"(?<!\\):", line)]


class WifiPage(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=24)
        self.win = win
        t = Gtk.Label(label="Wi-Fi & Network", xalign=0)
        t.get_style_context().add_class("page-title")
        self.pack_start(t, False, False, 0)
        top = Gtk.Box(spacing=12)
        self.radio = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.radio_handler = self.radio.connect("notify::active", self.on_radio)
        top.pack_start(Gtk.Label(label="Wi-Fi"), False, False, 0)
        top.pack_start(self.radio, False, False, 0)
        self.scan_btn = Gtk.Button(label="Scan")
        self.scan_btn.get_style_context().add_class("boxed")
        self.scan_btn.connect("clicked", lambda b: self.scan(True))
        top.pack_end(self.scan_btn, False, False, 0)
        self.pack_start(top, False, False, 0)
        self.status = Gtk.Label(xalign=0, wrap=True)
        self.status.get_style_context().add_class("dim")
        self.pack_start(self.status, False, False, 0)
        sw = Gtk.ScrolledWindow(hexpand=True, vexpand=True, min_content_height=240)
        sw.set_shadow_type(Gtk.ShadowType.IN)
        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list.connect("row-activated", lambda lb, r: self.connect_row(r))
        sw.add(self.list)
        self.pack_start(sw, True, True, 0)
        btns = Gtk.Box(spacing=8)
        self.btn_connect = Gtk.Button(label="Connect")
        self.btn_connect.get_style_context().add_class("suggested")
        self.btn_connect.connect("clicked", lambda b: self.connect_row(self.list.get_selected_row()))
        self.btn_disc = Gtk.Button(label="Disconnect")
        self.btn_disc.get_style_context().add_class("boxed")
        self.btn_disc.connect("clicked", lambda b: self.disconnect())
        btns.pack_start(self.btn_connect, False, False, 0)
        btns.pack_start(self.btn_disc, False, False, 0)
        self.pack_start(btns, False, False, 0)
        self.devs = Gtk.Label(xalign=0, wrap=True)
        self.devs.get_style_context().add_class("dim")
        self.pack_start(self.devs, False, False, 0)
        self.scan(False)

    def wifi_device(self):
        rc, out, _ = sh(["nmcli", "-t", "-f", "DEVICE,TYPE", "dev"])
        for line in out.splitlines():
            p = split_terse(line)
            if len(p) >= 2 and p[1] == "wifi":
                return p[0]
        return None

    def scan(self, rescan):
        self.status.set_text("Scanning…")
        self.scan_btn.set_sensitive(False)
        def work():
            rc0, radio, err0 = sh(["nmcli", "radio", "wifi"])
            rc, out, err = sh(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list",
                               "--rescan", "yes" if rescan else "auto"], timeout=40)
            drc, dout, _ = sh(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev"])
            return rc0, radio.strip(), rc, out, err or err0, dout
        def done(res):
            rc0, radio, rc, out, err, dout = res
            self.scan_btn.set_sensitive(True)
            self.radio.handler_block(self.radio_handler)
            self.radio.set_active(radio == "enabled")
            self.radio.handler_unblock(self.radio_handler)
            for r in self.list.get_children():
                self.list.remove(r)
            if rc0 != 0:
                self.status.set_text("NetworkManager is not available: " + err.strip())
                return
            nets = {}
            for line in out.splitlines():
                p = split_terse(line)
                if len(p) < 4 or not p[1]:
                    continue
                inuse, ssid, sig, sec = p[0] == "*", p[1], int(p[2] or 0), p[3]
                if ssid not in nets or inuse or sig > nets[ssid][1]:
                    nets[ssid] = (inuse, sig, sec)
            for ssid, (inuse, sig, sec) in sorted(nets.items(), key=lambda kv: (not kv[1][0], -kv[1][1])):
                r = Gtk.ListBoxRow()
                r.ssid, r.secured = ssid, bool(sec and sec != "--")
                h = Gtk.Box(spacing=12, margin=8)
                h.pack_start(Gtk.Label(label="📶"), False, False, 0)
                h.pack_start(Gtk.Label(label=ssid + ("   ✓ connected" if inuse else ""), xalign=0), True, True, 0)
                h.pack_start(Gtk.Label(label="🔒" if r.secured else ""), False, False, 0)
                lv = Gtk.Label(label="%d%%" % sig)
                lv.get_style_context().add_class("dim")
                h.pack_start(lv, False, False, 0)
                r.add(h)
                self.list.add(r)
            self.list.show_all()
            self.status.set_text("%d networks found." % len(nets) if nets else
                                 ("Wi-Fi is off." if radio != "enabled" else "No networks found (or no Wi-Fi adapter)."))
            lines = []
            for line in dout.splitlines():
                p = split_terse(line)
                if len(p) >= 4 and p[1] in ("wifi", "ethernet"):
                    lines.append("%s (%s): %s%s" % (p[0], p[1], p[2], " — " + p[3] if p[3] else ""))
            self.devs.set_text("\n".join(lines))
        bg(work, done)

    def on_radio(self, sw, _):
        bg(lambda: sh(["nmcli", "radio", "wifi", "on" if sw.get_active() else "off"]),
           lambda r: GLib.timeout_add(1500, lambda: (self.scan(False), False)[1]))

    def connect_row(self, r):
        if r is None:
            return
        pw = None
        known = sh(["nmcli", "-t", "-f", "NAME", "connection", "show"])[1].splitlines()
        if r.secured and r.ssid not in known:
            pw = ui.ask_password(self.win, r.ssid)
            if pw is None:
                return
        argv = ["nmcli", "dev", "wifi", "connect", r.ssid] + (["password", pw] if pw else [])
        self.status.set_text("Connecting to %s…" % r.ssid)
        def done(res):
            rc, out, err = res
            if rc == 0:
                self.status.set_text("Connected to %s." % r.ssid)
            else:
                self.status.set_text("")
                msg = (err or out).strip()
                if r.ssid in known and "secrets" in msg.lower():
                    sh(["nmcli", "connection", "delete", r.ssid])
                ui.notify(self.win, "Could not connect to " + r.ssid, msg)
            self.scan(False)
        bg(lambda: sh(argv, timeout=60), done)

    def disconnect(self):
        dev = self.wifi_device()
        if not dev:
            self.status.set_text("No Wi-Fi adapter found.")
            return
        bg(lambda: sh(["nmcli", "dev", "disconnect", dev]), lambda r: self.scan(False))


# ====================================================================== Sound
class SoundPage(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=24)
        t = Gtk.Label(label="Sound", xalign=0)
        t.get_style_context().add_class("page-title")
        self.pack_start(t, False, False, 0)
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 150, 1)
        self.scale.set_draw_value(True)
        self.scale.add_mark(100, Gtk.PositionType.BOTTOM, "100%")
        self.scale_handler = self.scale.connect("value-changed", self.on_volume)
        self.pack_start(row("Master volume", self.scale), False, False, 0)
        self.mute = Gtk.Switch(halign=Gtk.Align.START)
        self.mute_handler = self.mute.connect("notify::active", self.on_mute)
        self.pack_start(row("Mute", self.mute), False, False, 0)
        self.sinks = Gtk.ComboBoxText()
        self.sink_handler = self.sinks.connect("changed", self.on_sink)
        self.pack_start(row("Output device", self.sinks), False, False, 0)
        self.msg = Gtk.Label(xalign=0, wrap=True)
        self.msg.get_style_context().add_class("dim")
        self.pack_start(self.msg, False, False, 0)
        self.pending = None
        self.refresh()

    def refresh(self):
        def work():
            return (sh(["pactl", "get-sink-volume", "@DEFAULT_SINK@"]), sh(["pactl", "get-sink-mute", "@DEFAULT_SINK@"]),
                    sh(["pactl", "list", "short", "sinks"]), sh(["pactl", "get-default-sink"]))
        def done(res):
            (rv, ov, ev), (rm, om, _), (rs, os_, _), (rd, od, _) = res
            if rv != 0:
                self.msg.set_text("No audio server found. (%s)" % (ev.strip() or "pactl failed"))
                return
            m = re.search(r"(\d+)%", ov)
            self.scale.handler_block(self.scale_handler)
            self.scale.set_value(int(m.group(1)) if m else 0)
            self.scale.handler_unblock(self.scale_handler)
            self.mute.handler_block(self.mute_handler)
            self.mute.set_active("yes" in om)
            self.mute.handler_unblock(self.mute_handler)
            self.sinks.handler_block(self.sink_handler)
            self.sinks.remove_all()
            default = od.strip()
            for line in os_.splitlines():
                p = line.split("\t")
                if len(p) >= 2:
                    self.sinks.append(p[1], p[1].replace("alsa_output.", "").replace("_", " "))
            self.sinks.set_active_id(default)
            self.sinks.handler_unblock(self.sink_handler)
            self.msg.set_text("" if os_.strip() else "No output devices detected.")
        bg(work, done)

    def on_volume(self, scale):
        if self.pending:
            GLib.source_remove(self.pending)
        v = int(scale.get_value())
        self.pending = GLib.timeout_add(120, lambda: (bg(lambda: sh(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "%d%%" % v])),
                                                      setattr(self, "pending", None), False)[2])

    def on_mute(self, sw, _):
        bg(lambda: sh(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if sw.get_active() else "0"]))

    def on_sink(self, combo):
        s = combo.get_active_id()
        if s:
            bg(lambda: sh(["pactl", "set-default-sink", s]), lambda r: self.refresh())


# ====================================================================== System Info
class SystemPage(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=24)
        t = Gtk.Label(label="System Info", xalign=0)
        t.get_style_context().add_class("page-title")
        self.pack_start(t, False, False, 0)
        self.bars = {}
        for key, label in (("cpu", "CPU"), ("ram", "Memory"), ("disk", "Disk (system)")):
            pb = Gtk.ProgressBar(show_text=True, valign=Gtk.Align.CENTER)
            self.bars[key] = pb
            self.pack_start(row(label, pb), False, False, 0)
        self.pack_start(Gtk.Separator(), False, False, 6)
        cfg = common.load_config()
        info = [("Operating system", "%s %s" % (cfg["os_name"], cfg["os_version"])),
                ("Based on", self.os_release()), ("Kernel", platform.release()), ("Architecture", platform.machine()),
                ("Host name", platform.node()), ("Processor", self.cpu_model()),
                ("Python", platform.python_version())]
        for k, v in info:
            lab = Gtk.Label(label=v, xalign=0, selectable=True, wrap=True)
            self.pack_start(row(k, lab), False, False, 0)
        self.uptime = Gtk.Label(xalign=0)
        self.pack_start(row("Uptime", self.uptime), False, False, 0)
        psutil.cpu_percent(None)
        GLib.timeout_add(1000, self.tick)
        self.tick()

    @staticmethod
    def os_release():
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
        return "Linux"

    @staticmethod
    def cpu_model():
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return "%s (%d threads)" % (line.split(":", 1)[1].strip(), os.cpu_count() or 1)
        except OSError:
            pass
        return "%d threads" % (os.cpu_count() or 1)

    def tick(self):
        cpu = psutil.cpu_percent(None)
        vm = psutil.virtual_memory()
        try:
            du = psutil.disk_usage("/")
        except OSError:
            du = None
        self.set_bar("cpu", cpu / 100.0, "%d%%" % cpu)
        self.set_bar("ram", vm.percent / 100.0, "%.1f / %.1f GB" % (vm.used / 2**30, vm.total / 2**30))
        if du:
            self.set_bar("disk", du.percent / 100.0, "%.1f / %.1f GB" % (du.used / 2**30, du.total / 2**30))
        up = int(time.time() - psutil.boot_time())
        self.uptime.set_text("%dh %02dm" % (up // 3600, up % 3600 // 60))
        return True

    def set_bar(self, key, frac, text):
        self.bars[key].set_fraction(min(1.0, max(0.0, frac)))
        self.bars[key].set_text(text)


# ====================================================================== window
class SettingsWindow(Gtk.Window):
    PAGES = (("appearance", "Appearance & Layout", AppearancePage), ("display", "Display", DisplayPage),
             ("wifi", "Wi-Fi & Network", WifiPage), ("sound", "Sound", SoundPage), ("system", "System Info", SystemPage))

    def __init__(self, page):
        super().__init__(title="Settings")
        self.set_default_size(900, 640)
        box = Gtk.Box()
        self.add(box)
        self.stack = Gtk.Stack()
        side = Gtk.StackSidebar()
        side.set_stack(self.stack)
        side.set_size_request(210, -1)
        side.get_style_context().add_class("sidebar")
        box.pack_start(side, False, False, 0)
        box.pack_start(self.stack, True, True, 0)
        for name, title, cls in self.PAGES:
            self.stack.add_titled(cls(self), name, title)
        self.connect("destroy", Gtk.main_quit)
        self.show_all()
        if page in [p[0] for p in self.PAGES]:
            self.stack.set_visible_child_name(page)

    def save_and_reload(self, cfg):
        try:
            common.save_config(cfg)
            common.write_theme_vars(cfg)
        except OSError as e:
            ui.notify(self, "Cannot save settings", "%s\n\nThe config folder must be writable by your user." % e)
            return
        common.apply_theme()
        common.reload_all()


def main():
    page = "appearance"
    if "--page" in sys.argv:
        i = sys.argv.index("--page")
        page = sys.argv[i + 1] if i + 1 < len(sys.argv) else page
    common.apply_theme()
    SettingsWindow(page)
    Gtk.main()


if __name__ == "__main__":
    main()
