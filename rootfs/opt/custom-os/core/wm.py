#!/usr/bin/env python3
"""AI Custom OS window manager.

Pure python-xlib.  Features: reparenting frames, cairo/Pango-drawn titlebars (pushed with
PutImage), minimise / maximise / close buttons, drag-to-move, edge/corner resize, click-to-focus,
Alt+Tab MRU cycling, configurable global shortcuts, EWMH hints, struts from the panel, and a
JSON window list (_CUSTOMOS_WINDOWS root property) for the desktop shell's task switcher.

Signals: SIGHUP reloads config + theme, SIGTERM/SIGINT exit cleanly.
"""
import json
import os
import select
import signal
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Xlib import X, XK, Xatom, Xcursorfont, display as xdisplay, error as xerror
from Xlib.protocol import event as xevent
import cairo
import gi
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo

from core import common

L, R, T, B = 1, 2, 4, 8                      # resize edge flags
LOCKS = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)
MOD_NAMES = {"super": X.Mod4Mask, "win": X.Mod4Mask, "alt": X.Mod1Mask, "ctrl": X.ControlMask,
             "control": X.ControlMask, "shift": X.ShiftMask}
RELEVANT = X.ShiftMask | X.ControlMask | X.Mod1Mask | X.Mod4Mask
NORMAL_STATE, ICONIC_STATE, WITHDRAWN_STATE = 1, 3, 0
UNFRAMED_TYPES = {"_NET_WM_WINDOW_TYPE_MENU", "_NET_WM_WINDOW_TYPE_POPUP_MENU", "_NET_WM_WINDOW_TYPE_DROPDOWN_MENU",
                  "_NET_WM_WINDOW_TYPE_TOOLTIP", "_NET_WM_WINDOW_TYPE_NOTIFICATION", "_NET_WM_WINDOW_TYPE_COMBO",
                  "_NET_WM_WINDOW_TYPE_DND", "_NET_WM_WINDOW_TYPE_SPLASH"}

ATOM_NAMES = """WM_PROTOCOLS WM_DELETE_WINDOW WM_TAKE_FOCUS WM_STATE WM_CHANGE_STATE UTF8_STRING
_NET_SUPPORTED _NET_SUPPORTING_WM_CHECK _NET_CLIENT_LIST _NET_CLIENT_LIST_STACKING _NET_ACTIVE_WINDOW
_NET_WORKAREA _NET_NUMBER_OF_DESKTOPS _NET_CURRENT_DESKTOP _NET_DESKTOP_GEOMETRY _NET_CLOSE_WINDOW
_NET_WM_NAME _NET_WM_STATE _NET_WM_STATE_MAXIMIZED_VERT _NET_WM_STATE_MAXIMIZED_HORZ _NET_WM_STATE_FULLSCREEN
_NET_WM_STATE_HIDDEN _NET_WM_STATE_ABOVE _NET_WM_STATE_SKIP_TASKBAR _NET_WM_STATE_SKIP_PAGER
_NET_WM_WINDOW_TYPE _NET_WM_WINDOW_TYPE_DOCK _NET_WM_WINDOW_TYPE_DESKTOP _NET_WM_WINDOW_TYPE_NORMAL
_NET_WM_WINDOW_TYPE_DIALOG _NET_WM_WINDOW_TYPE_UTILITY _NET_WM_WINDOW_TYPE_MENU _NET_WM_WINDOW_TYPE_POPUP_MENU
_NET_WM_WINDOW_TYPE_DROPDOWN_MENU _NET_WM_WINDOW_TYPE_TOOLTIP _NET_WM_WINDOW_TYPE_NOTIFICATION
_NET_WM_WINDOW_TYPE_COMBO _NET_WM_WINDOW_TYPE_DND _NET_WM_WINDOW_TYPE_SPLASH
_NET_WM_STRUT _NET_WM_STRUT_PARTIAL _NET_FRAME_EXTENTS _MOTIF_WM_HINTS _CUSTOMOS_WINDOWS""".split()


def log(*a):
    print("[wm]", *a, file=sys.stderr, flush=True)


def px(rgba):
    return (int(rgba[0] * 255) << 16) | (int(rgba[1] * 255) << 8) | int(rgba[2] * 255)


class Client:
    def __init__(self, win):
        self.win = win
        self.frame = None
        self.title = ""
        self.wmclass = ""
        self.fx = self.fy = 0                 # frame position
        self.cw = self.ch = 1                 # client size
        self.decorated = True
        self.maxed = self.fs = self.minimized = False
        self.above = self.skip_taskbar = False
        self.saved = None
        self.protocols = []
        self.minw, self.minh = 160, 80
        self.ignore_unmap = 0
        self.hover = None                     # 'min' | 'max' | 'close'
        self.cursor_key = None

    def deco(self, wm):
        """(side, top, bottom) frame thickness"""
        if not self.decorated or self.fs:
            return 0, 0, 0
        return wm.bw, wm.th, wm.bw


class WindowManager:
    def __init__(self):
        self.d = xdisplay.Display()
        self.screen = self.d.screen()
        self.root = self.screen.root
        self.depth = self.screen.root_depth
        self.sw, self.sh = self.screen.width_in_pixels, self.screen.height_in_pixels
        try:
            self.d.display.default_error_handler = lambda err: None   # async errors for vanished windows
        except AttributeError:
            pass
        self.A = {n: self.d.intern_atom(n) for n in ATOM_NAMES}
        self.RA = {v: k for k, v in self.A.items()}
        self.clients = []                     # map order
        self.mru = []                         # focus order, most recent first
        self.by_client, self.by_frame = {}, {}
        self.docks, self.struts, self.desktops = {}, {}, {}
        self.active = None
        self.drag = None
        self.press = None
        self.last_click = (0, 0)
        self.cycling = False
        self.cycle_list, self.cycle_idx = [], 0
        self.hidden_for_desktop = []
        self.keymap = {}
        self.running = True
        self.reload_flag = False
        self.cursors = {}
        self.gc = self.root.create_gc()
        self.load_theme()
        self.become_wm()
        self.setup_ewmh()
        self.make_cursors()
        self.grab_keys()
        self.scan()
        with open(common.wm_pid_path(), "w") as f:
            f.write(str(os.getpid()))
        log("ready on %dx%d" % (self.sw, self.sh))

    # ------------------------------------------------------------------ setup
    def become_wm(self):
        catch = xerror.CatchError(xerror.BadAccess)
        mask = (X.SubstructureRedirectMask | X.SubstructureNotifyMask | X.StructureNotifyMask |
                X.PropertyChangeMask)
        self.root.change_attributes(event_mask=mask, onerror=catch)
        self.d.sync()
        if catch.get_error():
            log("another window manager is already running")
            sys.exit(1)

    def setup_ewmh(self):
        A = self.A
        self.check = self.root.create_window(-100, -100, 1, 1, 0, X.CopyFromParent)
        for w in (self.root, self.check):
            w.change_property(A["_NET_SUPPORTING_WM_CHECK"], Xatom.WINDOW, 32, [self.check.id])
        self.check.change_property(A["_NET_WM_NAME"], A["UTF8_STRING"], 8, b"CustomWM")
        supported = [A[n] for n in ATOM_NAMES if n.startswith("_NET_") and n not in
                     ("_NET_WM_WINDOW_TYPE_MENU", "_NET_WM_WINDOW_TYPE_TOOLTIP")]
        self.root.change_property(A["_NET_SUPPORTED"], Xatom.ATOM, 32, supported)
        self.root.change_property(A["_NET_NUMBER_OF_DESKTOPS"], Xatom.CARDINAL, 32, [1])
        self.root.change_property(A["_NET_CURRENT_DESKTOP"], Xatom.CARDINAL, 32, [0])
        self.root.change_property(A["_NET_DESKTOP_GEOMETRY"], Xatom.CARDINAL, 32, [self.sw, self.sh])
        self.publish_workarea()
        self.publish()

    def make_cursor(self, glyph):
        f = self.d.open_font("cursor")
        return f.create_glyph_cursor(f, glyph, glyph + 1, (0, 0, 0), (0xffff, 0xffff, 0xffff))

    def make_cursors(self):
        cf = Xcursorfont
        self.cursors = {0: self.make_cursor(cf.left_ptr), L: self.make_cursor(cf.left_side),
                        R: self.make_cursor(cf.right_side), T: self.make_cursor(cf.top_side),
                        B: self.make_cursor(cf.bottom_side), L | T: self.make_cursor(cf.top_left_corner),
                        R | T: self.make_cursor(cf.top_right_corner), L | B: self.make_cursor(cf.bottom_left_corner),
                        R | B: self.make_cursor(cf.bottom_right_corner), "move": self.make_cursor(cf.fleur)}
        self.root.change_attributes(cursor=self.cursors[0])

    def load_theme(self):
        self.cfg = common.load_config()
        wmc = self.cfg["wm"]
        self.th = int(wmc.get("titlebar_height", 32))
        self.bw = int(wmc.get("border_width", 4))
        self.font = Pango.FontDescription(wmc.get("title_font", "Sans Bold 10"))
        pal = common.palette(self.cfg)
        file_cols = common.parse_theme_colors()
        self.col = {}
        for k in ("wm_active", "wm_inactive", "wm_fg_active", "wm_fg_inactive", "wm_border_active",
                  "wm_border_inactive", "danger", "fg_dim"):
            self.col[k] = file_cols.get(k) or common.parse_color(pal[k])

    # ------------------------------------------------------------------ small X helpers
    def prop(self, win, name):
        try:
            p = win.get_full_property(self.A[name] if name in self.A else self.d.intern_atom(name), X.AnyPropertyType)
        except xerror.XError:
            return None
        return p.value if p is not None else None

    def text_prop(self, win, name):
        v = self.prop(win, name)
        if v is None:
            return None
        if isinstance(v, (bytes, bytearray)):
            v = bytes(v).decode("utf-8", "replace")
        return v

    def get_title(self, win):
        t = self.text_prop(win, "_NET_WM_NAME")
        if not t:
            try:
                t = win.get_wm_name()
            except xerror.XError:
                t = None
            if isinstance(t, bytes):
                t = t.decode("latin-1", "replace")
        return t or ""

    def window_types(self, win):
        v = self.prop(win, "_NET_WM_WINDOW_TYPE")
        return {self.RA.get(a, "") for a in v} if v is not None else set()

    def send_msg(self, win, ctype, data):
        ev = xevent.ClientMessage(window=win, client_type=self.A[ctype], data=(32, (list(data) + [0] * 5)[:5]))
        win.send_event(ev, event_mask=0)

    def set_wm_state(self, c, state):
        c.win.change_property(self.A["WM_STATE"], self.A["WM_STATE"], 32, [state, 0])

    # ------------------------------------------------------------------ workarea
    def workarea(self):
        l = r = t = b = 0
        for s in self.struts.values():
            l, r, t, b = max(l, s[0]), max(r, s[1]), max(t, s[2]), max(b, s[3])
        return l, t, max(100, self.sw - l - r), max(100, self.sh - t - b)

    def read_strut(self, win):
        v = self.prop(win, "_NET_WM_STRUT_PARTIAL") or self.prop(win, "_NET_WM_STRUT")
        self.struts[win.id] = tuple(v[:4]) if v is not None and len(v) >= 4 else (0, 0, 0, 0)

    def publish_workarea(self):
        x, y, w, h = self.workarea()
        self.root.change_property(self.A["_NET_WORKAREA"], Xatom.CARDINAL, 32, [x, y, w, h])

    def workarea_changed(self):
        self.publish_workarea()
        for c in self.clients:
            if c.maxed or c.fs:
                self.relayout(c)

    # ------------------------------------------------------------------ discovery / framing
    def scan(self):
        for w in self.root.query_tree().children:
            try:
                a = w.get_attributes()
            except xerror.XError:
                continue
            if a.override_redirect or a.map_state != X.IsViewable:
                continue
            self.manage(w, existing=True)

    def manage(self, win, existing=False):
        try:
            attrs = win.get_attributes()
            geom = win.get_geometry()
        except xerror.XError:
            return
        if attrs.override_redirect or win.id in self.by_client or win.id in self.docks:
            return
        types = self.window_types(win)
        if "_NET_WM_WINDOW_TYPE_DOCK" in types:
            self.docks[win.id] = win
            win.change_attributes(event_mask=X.PropertyChangeMask | X.StructureNotifyMask)
            self.read_strut(win)
            win.map()
            win.configure(stack_mode=X.Above)
            self.workarea_changed()
            return
        if "_NET_WM_WINDOW_TYPE_DESKTOP" in types:
            self.desktops[win.id] = win
            win.configure(x=0, y=0, width=self.sw, height=self.sh, stack_mode=X.Below)
            win.map()
            win.configure(stack_mode=X.Below)
            return
        if types & UNFRAMED_TYPES:
            win.map()
            return

        c = Client(win)
        c.title = self.get_title(win)
        cls = None
        try:
            cls = win.get_wm_class()
        except xerror.XError:
            pass
        c.wmclass = cls[1] if cls else ""
        proto = self.prop(win, "WM_PROTOCOLS")
        c.protocols = [self.RA.get(a, "") for a in proto] if proto is not None else []
        mw = self.prop(win, "_MOTIF_WM_HINTS")
        if mw is not None and len(mw) >= 3 and (mw[0] & 2) and mw[2] == 0:
            c.decorated = False
        st = self.prop(win, "_NET_WM_STATE")
        states = {self.RA.get(a, "") for a in st} if st is not None else set()
        c.above = "_NET_WM_STATE_ABOVE" in states
        c.skip_taskbar = "_NET_WM_STATE_SKIP_TASKBAR" in states or "_NET_WM_WINDOW_TYPE_UTILITY" in types
        want_max = "_NET_WM_STATE_MAXIMIZED_HORZ" in states and "_NET_WM_STATE_MAXIMIZED_VERT" in states
        want_fs = "_NET_WM_STATE_FULLSCREEN" in states

        user_pos = False
        try:
            h = win.get_wm_normal_hints()
            if h is not None:
                user_pos = bool(h.flags & 5)       # USPosition | PPosition
                if h.flags & 16:
                    c.minw, c.minh = max(h.min_width, 50), max(h.min_height, 30)
        except (xerror.XError, AttributeError):
            pass
        c.cw, c.ch = max(geom.width, 1), max(geom.height, 1)
        side, top, bottom = c.deco(self)
        ax, ay, aw, ah = self.workarea()
        c.cw = min(c.cw, aw - 2 * side)
        c.ch = min(c.ch, ah - top - bottom)
        if existing or user_pos or geom.x or geom.y:
            c.fx, c.fy = geom.x - side, geom.y - top      # honour the position the client asked for
        else:
            n = len(self.clients) % 8
            c.fx = ax + (aw - c.cw - 2 * side) // 2 + n * 28 - 56
            c.fy = ay + (ah - c.ch - top - bottom) // 2 + n * 28 - 56
        c.fx = max(ax - 20, min(c.fx, ax + aw - 80))
        c.fy = max(ay, min(c.fy, ay + ah - top - 20))

        mask = (X.ExposureMask | X.ButtonPressMask | X.ButtonReleaseMask | X.ButtonMotionMask | X.PointerMotionMask |
                X.LeaveWindowMask | X.SubstructureRedirectMask)
        c.frame = self.root.create_window(
            c.fx, c.fy, c.cw + 2 * side, c.ch + top + bottom, 0, self.depth, X.InputOutput, X.CopyFromParent,
            background_pixel=px(self.col["wm_border_inactive"]), event_mask=mask, cursor=self.cursors[0])
        win.change_save_set(X.SetModeInsert)
        win.change_attributes(event_mask=X.PropertyChangeMask | X.StructureNotifyMask | X.FocusChangeMask)
        if attrs.map_state == X.IsViewable:
            c.ignore_unmap += 1
        win.reparent(c.frame, side, top)
        win.grab_button(X.AnyButton, X.AnyModifier, False,
                        X.ButtonPressMask | X.ButtonReleaseMask | X.ButtonMotionMask,
                        X.GrabModeSync, X.GrabModeAsync, X.NONE, X.NONE)
        self.by_client[win.id] = c
        self.by_frame[c.frame.id] = c
        self.clients.append(c)
        self.mru.append(c)
        c.frame.map()
        win.map()
        self.set_wm_state(c, NORMAL_STATE)
        win.change_property(self.A["_NET_FRAME_EXTENTS"], Xatom.CARDINAL, 32, [side, side, top, bottom])
        self.apply(c, notify=True)
        if want_max:
            self.set_max(c, True)
        if want_fs:
            self.set_fs(c, True)
        self.update_state(c)
        self.focus(c)

    def unmanage(self, c, destroyed=False):
        if c not in self.clients:
            return
        self.clients.remove(c)
        if c in self.mru:
            self.mru.remove(c)
        self.by_client.pop(c.win.id, None)
        self.by_frame.pop(c.frame.id, None)
        try:
            if not destroyed:
                c.win.unmap()
                side, top, _ = c.deco(self)
                c.win.reparent(self.root, c.fx + side, c.fy + top)
                c.win.change_save_set(X.SetModeDelete)
                self.set_wm_state(c, WITHDRAWN_STATE)
            c.frame.destroy()
        except xerror.XError:
            pass
        if self.active is c:
            self.active = None
            self.focus_next()
        self.publish()

    # ------------------------------------------------------------------ geometry
    def apply(self, c, notify=True):
        side, top, bottom = c.deco(self)
        fw, fh = c.cw + 2 * side, c.ch + top + bottom
        c.frame.configure(x=c.fx, y=c.fy, width=fw, height=fh)
        c.win.configure(x=side, y=top, width=c.cw, height=c.ch)
        c.win.change_property(self.A["_NET_FRAME_EXTENTS"], Xatom.CARDINAL, 32, [side, side, top, bottom])
        self.draw(c)
        if notify:
            self.notify_geometry(c)

    def notify_geometry(self, c):
        side, top, _ = c.deco(self)
        ev = xevent.ConfigureNotify(window=c.win, event=c.win, x=c.fx + side, y=c.fy + top, width=c.cw,
                                    height=c.ch, border_width=0, above_sibling=X.NONE, override_redirect=False)
        c.win.send_event(ev, event_mask=X.StructureNotifyMask)

    def relayout(self, c):
        side, top, bottom = c.deco(self)
        if c.fs:
            c.fx, c.fy, c.cw, c.ch = 0, 0, self.sw, self.sh
        elif c.maxed:
            ax, ay, aw, ah = self.workarea()
            c.fx, c.fy = ax, ay
            c.cw, c.ch = max(aw - 2 * side, 50), max(ah - top - bottom, 50)
        self.apply(c)

    def set_max(self, c, on):
        if on == c.maxed or c.fs:
            return
        if on:
            c.saved = (c.fx, c.fy, c.cw, c.ch)
            c.maxed = True
            self.relayout(c)
        else:
            c.maxed = False
            if c.saved:
                c.fx, c.fy, c.cw, c.ch = c.saved
            self.relayout(c)
        self.update_state(c)

    def set_fs(self, c, on):
        if on == c.fs:
            return
        if on:
            if not c.maxed:
                c.saved = (c.fx, c.fy, c.cw, c.ch)
            c.fs = True
            c.frame.configure(stack_mode=X.Above)
        else:
            c.fs = False
            if c.maxed:
                pass
            elif c.saved:
                c.fx, c.fy, c.cw, c.ch = c.saved
        self.relayout(c)
        self.update_state(c)

    def update_state(self, c):
        A = self.A
        atoms = []
        if c.maxed:
            atoms += [A["_NET_WM_STATE_MAXIMIZED_VERT"], A["_NET_WM_STATE_MAXIMIZED_HORZ"]]
        if c.fs:
            atoms.append(A["_NET_WM_STATE_FULLSCREEN"])
        if c.minimized:
            atoms.append(A["_NET_WM_STATE_HIDDEN"])
        if c.above:
            atoms.append(A["_NET_WM_STATE_ABOVE"])
        if c.skip_taskbar:
            atoms.append(A["_NET_WM_STATE_SKIP_TASKBAR"])
        c.win.change_property(A["_NET_WM_STATE"], Xatom.ATOM, 32, atoms)
        self.draw(c)
        self.publish()

    def minimize(self, c):
        if c.minimized:
            return
        c.minimized = True
        c.frame.unmap()
        self.set_wm_state(c, ICONIC_STATE)
        self.update_state(c)
        if self.active is c:
            self.active = None
            self.focus_next()

    def restore(self, c):
        if not c.minimized:
            return
        c.minimized = False
        c.frame.map()
        self.set_wm_state(c, NORMAL_STATE)
        self.update_state(c)

    def toggle_show_desktop(self):
        visible = [c for c in self.clients if not c.minimized and not c.skip_taskbar]
        if visible:
            self.hidden_for_desktop = visible
            for c in visible:
                self.minimize(c)
        else:
            for c in self.hidden_for_desktop:
                if c in self.clients:
                    self.restore(c)
            if self.hidden_for_desktop:
                self.focus(self.hidden_for_desktop[0])
            self.hidden_for_desktop = []

    def close(self, c):
        if "WM_DELETE_WINDOW" in c.protocols:
            self.send_msg(c.win, "WM_PROTOCOLS", [self.A["WM_DELETE_WINDOW"], X.CurrentTime])
        else:
            try:
                c.win.kill_client()
            except xerror.XError:
                pass

    # ------------------------------------------------------------------ focus / stacking
    def raise_(self, c):
        c.frame.configure(stack_mode=X.Above)
        for o in self.clients:
            if o.above and o is not c and not o.minimized:
                o.frame.configure(stack_mode=X.Above)
        if not c.fs:
            for dw in self.docks.values():
                dw.configure(stack_mode=X.Above)

    def focus(self, c, raise_=True):
        if c is None:
            self.root.set_input_focus(X.RevertToPointerRoot, X.CurrentTime)
            old, self.active = self.active, None
            if old:
                self.draw(old)
            self.publish()
            return
        if c.minimized:
            self.restore(c)
        old = self.active
        self.active = c
        if not self.cycling:
            if c in self.mru:
                self.mru.remove(c)
            self.mru.insert(0, c)
        try:
            c.win.set_input_focus(X.RevertToPointerRoot, X.CurrentTime)
            if "WM_TAKE_FOCUS" in c.protocols:
                self.send_msg(c.win, "WM_PROTOCOLS", [self.A["WM_TAKE_FOCUS"], X.CurrentTime])
        except xerror.XError:
            pass
        if raise_:
            self.raise_(c)
        if old and old is not c:
            self.draw(old)
        self.draw(c)
        self.publish()

    def focus_next(self):
        for o in self.mru:
            if not o.minimized:
                self.focus(o)
                return
        self.focus(None)

    # ------------------------------------------------------------------ publishing to the shell
    def publish(self):
        A = self.A
        try:
            ids = [c.win.id for c in self.clients]
            self.root.change_property(A["_NET_CLIENT_LIST"], Xatom.WINDOW, 32, ids)
            self.root.change_property(A["_NET_CLIENT_LIST_STACKING"], Xatom.WINDOW, 32, ids)
            self.root.change_property(A["_NET_ACTIVE_WINDOW"], Xatom.WINDOW, 32,
                                      [self.active.win.id if self.active else 0])
            data = [{"id": c.win.id, "title": c.title, "class": c.wmclass, "minimized": c.minimized,
                     "active": c is self.active, "skip": c.skip_taskbar} for c in self.clients]
            self.root.change_property(A["_CUSTOMOS_WINDOWS"], A["UTF8_STRING"], 8,
                                      json.dumps(data, ensure_ascii=False).encode("utf-8"))
        except xerror.XError:
            pass

    # ------------------------------------------------------------------ drawing
    def button_rects(self, c):
        fw = c.cw + 2 * self.bw
        s = self.th
        return {"close": (fw - s, 0, s, s), "max": (fw - 2 * s, 0, s, s), "min": (fw - 3 * s, 0, s, s)}

    def draw(self, c):
        if c not in self.clients or not c.decorated or c.fs or c.minimized:
            return
        active = c is self.active
        bg = self.col["wm_active" if active else "wm_inactive"]
        fg = self.col["wm_fg_active" if active else "wm_fg_inactive"]
        border = self.col["wm_border_active" if active else "wm_border_inactive"]
        fw, th = c.cw + 2 * self.bw, self.th
        c.frame.change_attributes(background_pixel=px(border))
        c.frame.clear_area(0, 0, 0, 0, False)
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, fw, th)
        cr = cairo.Context(surf)
        cr.set_source_rgb(*bg[:3])
        cr.paint()
        if active:                                   # accent underline marks the focused window
            cr.set_source_rgb(*border[:3])
            cr.rectangle(0, th - 2, fw, 2)
            cr.fill()
        rects = self.button_rects(c)
        # title
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)
        layout.set_text(c.title or c.wmclass or "", -1)
        layout.set_width(max(10, fw - 3 * th - 24) * Pango.SCALE)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        _, ext = layout.get_pixel_extents()
        cr.set_source_rgb(*fg[:3])
        cr.move_to(14, (th - ext.height) / 2 - ext.y)
        PangoCairo.show_layout(cr, layout)
        # buttons
        for name, (bx, by, bwid, bh) in rects.items():
            hot = c.hover == name
            if hot:
                if name == "close":
                    cr.set_source_rgb(*self.col["danger"][:3])
                else:
                    cr.set_source_rgba(fg[0], fg[1], fg[2], 0.14)
                cr.rectangle(bx, by, bwid, bh)
                cr.fill()
            cr.set_source_rgb(*((1, 1, 1) if hot and name == "close" else fg[:3]))
            cr.set_line_width(1.4)
            cx, cy, r = bx + bwid / 2, by + bh / 2, bh * 0.17
            if name == "min":
                cr.move_to(cx - r, cy + r * 0.6)
                cr.line_to(cx + r, cy + r * 0.6)
            elif name == "max":
                if c.maxed:
                    cr.rectangle(cx - r + 2, cy - r - 1, r * 2 - 2, r * 2 - 2)
                    cr.rectangle(cx - r, cy - r + 2, r * 2 - 2, r * 2 - 2)
                else:
                    cr.rectangle(cx - r, cy - r, r * 2, r * 2)
            else:
                cr.move_to(cx - r, cy - r)
                cr.line_to(cx + r, cy + r)
                cr.move_to(cx + r, cy - r)
                cr.line_to(cx - r, cy + r)
            cr.stroke()
        self.blit(c.frame, surf, 0, 0)

    def blit(self, win, surf, x, y):
        surf.flush()
        data = bytes(surf.get_data())
        w, h, stride = surf.get_width(), surf.get_height(), surf.get_stride()
        rows = max(1, 180000 // stride)             # stay under the core-protocol request limit
        try:
            for r in range(0, h, rows):
                n = min(rows, h - r)
                win.put_image(self.gc, x, y + r, w, n, X.ZPixmap, self.depth, 0, data[r * stride:(r + n) * stride])
        except xerror.XError:
            pass

    # ------------------------------------------------------------------ hit testing / drag
    def hit(self, c, x, y):
        """-> ('btn', name) | ('title',) | ('edge', flags) | None"""
        if c.fs or not c.decorated:
            return None
        fw, fh = c.cw + 2 * self.bw, c.ch + self.th + self.bw
        if not c.maxed:
            m, corner = self.bw + 2, 16
            e = 0
            e |= L if x < m else 0
            e |= R if x >= fw - m else 0
            e |= B if y >= fh - m else 0
            e |= T if y < 3 else 0
            if y >= fh - corner and x < corner:
                e |= L | B
            if y >= fh - corner and x >= fw - corner:
                e |= R | B
            if y < 4 and x < corner:
                e |= L | T
            if y < 4 and x >= fw - corner:
                e |= R | T
            if e:
                return ("edge", e)
        if y < self.th:
            for name, (bx, by, bw_, bh) in self.button_rects(c).items():
                if bx <= x < bx + bw_:
                    return ("btn", name)
            return ("title",)
        return None

    def begin_drag(self, c, rx, ry, mode, edges=0):
        if c.fs:
            return
        if c.maxed and mode == "move":
            self.set_max(c, False)
            c.fx = rx - c.cw // 2
            c.fy = max(0, ry - self.th // 2)
            self.apply(c)
        self.drag = {"c": c, "mode": mode, "edges": edges, "sx": rx, "sy": ry,
                     "fx": c.fx, "fy": c.fy, "cw": c.cw, "ch": c.ch}
        if mode == "move":
            c.frame.change_attributes(cursor=self.cursors["move"])

    def do_drag(self, rx, ry):
        d = self.drag
        c = d["c"]
        dx, dy = rx - d["sx"], ry - d["sy"]
        if d["mode"] == "move":
            c.fx, c.fy = d["fx"] + dx, max(0, d["fy"] + dy)
            c.frame.configure(x=c.fx, y=c.fy)
            return
        e = d["edges"]
        cw, ch, fx, fy = d["cw"], d["ch"], d["fx"], d["fy"]
        if e & R:
            cw = max(c.minw, d["cw"] + dx)
        if e & L:
            cw = max(c.minw, d["cw"] - dx)
            fx = d["fx"] + d["cw"] - cw
        if e & B:
            ch = max(c.minh, d["ch"] + dy)
        if e & T:
            ch = max(c.minh, d["ch"] - dy)
            fy = d["fy"] + d["ch"] - ch
        c.fx, c.fy, c.cw, c.ch = fx, fy, cw, ch
        self.apply(c, notify=False)

    def end_drag(self):
        c = self.drag["c"]
        self.drag = None
        if c in self.clients:
            c.frame.change_attributes(cursor=self.cursors[0])
            self.notify_geometry(c)

    # ------------------------------------------------------------------ keys
    def parse_shortcut(self, spec):
        mods, key = 0, None
        for part in spec.split("+"):
            p = part.strip()
            if p.lower() in MOD_NAMES:
                mods |= MOD_NAMES[p.lower()]
            else:
                key = p
        if not key:
            return None
        ks = XK.string_to_keysym(key) or XK.string_to_keysym(key.capitalize())
        kc = self.d.keysym_to_keycode(ks) if ks else 0
        return (kc, mods) if kc else None

    def grab_keys(self):
        try:
            self.root.ungrab_key(X.AnyKey, X.AnyModifier)
        except xerror.XError:
            pass
        self.keymap = {}
        table = dict(self.cfg.get("shortcuts", {}))
        table["Alt+Tab"] = "@alt-tab"
        table["Alt+Shift+Tab"] = "@alt-tab-rev"
        for spec, action in table.items():
            ks = self.parse_shortcut(spec)
            if not ks:
                log("ignoring shortcut", spec)
                continue
            self.keymap[ks] = action
            for lk in LOCKS:
                self.root.grab_key(ks[0], ks[1] | lk, True, X.GrabModeAsync, X.GrabModeAsync)

    def run_action(self, action):
        act = self.active
        if action == "@close" and act:
            self.close(act)
        elif action == "@maximize" and act:
            self.set_max(act, not act.maxed)
        elif action == "@minimize" and act:
            self.minimize(act)
        elif action == "@show-desktop":
            self.toggle_show_desktop()
        elif action == "@launcher":
            common.send_shell("toggle-launcher")
        elif action in ("@alt-tab", "@alt-tab-rev"):
            self.alt_tab(action.endswith("rev"))
        elif not action.startswith("@"):
            common.run(action)

    def alt_tab(self, reverse):
        if not self.cycling:
            lst = [c for c in self.mru if not c.skip_taskbar]
            if len(lst) < 2:
                if lst and lst[0].minimized:
                    self.focus(lst[0])
                return
            self.cycle_list, self.cycle_idx, self.cycling = lst, 0, True
            self.root.grab_keyboard(True, X.GrabModeAsync, X.GrabModeAsync, X.CurrentTime)
        self.cycle_idx = (self.cycle_idx + (-1 if reverse else 1)) % len(self.cycle_list)
        self.focus(self.cycle_list[self.cycle_idx], raise_=True)

    def end_cycle(self):
        self.cycling = False
        try:
            self.d.ungrab_keyboard(X.CurrentTime)
        except xerror.XError:
            pass
        if self.cycle_list:
            c = self.cycle_list[self.cycle_idx]
            if c in self.clients:
                self.focus(c)
        self.cycle_list = []

    # ------------------------------------------------------------------ event handlers
    def dispatch(self, ev):
        t = ev.type
        h = {X.MapRequest: self.on_map_request, X.ConfigureRequest: self.on_configure_request,
             X.UnmapNotify: self.on_unmap, X.DestroyNotify: self.on_destroy, X.PropertyNotify: self.on_property,
             X.ClientMessage: self.on_client_message, X.ButtonPress: self.on_button_press,
             X.ButtonRelease: self.on_button_release, X.MotionNotify: self.on_motion, X.KeyPress: self.on_key_press,
             X.KeyRelease: self.on_key_release, X.Expose: self.on_expose, X.LeaveNotify: self.on_leave,
             X.ConfigureNotify: self.on_configure_notify}.get(t)
        if h:
            h(ev)

    def on_map_request(self, ev):
        c = self.by_client.get(ev.window.id)
        if c:
            self.restore(c)
            c.win.map()
        else:
            self.manage(ev.window)

    def on_configure_request(self, ev):
        c = self.by_client.get(ev.window.id)
        m = ev.value_mask
        if c:
            if c.maxed or c.fs:
                self.notify_geometry(c)
                return
            side, top, _ = c.deco(self)
            if m & X.CWWidth:
                c.cw = max(1, ev.width)
            if m & X.CWHeight:
                c.ch = max(1, ev.height)
            if m & X.CWX:
                c.fx = ev.x - side
            if m & X.CWY:
                c.fy = ev.y - top
            self.apply(c)
            return
        kw = {}
        for bit, name in ((X.CWX, "x"), (X.CWY, "y"), (X.CWWidth, "width"), (X.CWHeight, "height"),
                          (X.CWBorderWidth, "border_width"), (X.CWStackMode, "stack_mode")):
            if m & bit:
                kw[name] = getattr(ev, name)
        try:
            ev.window.configure(**kw)
        except xerror.XError:
            pass

    def on_unmap(self, ev):
        c = self.by_client.get(ev.window.id)
        if not c or ev.event.id != ev.window.id:
            return
        if c.ignore_unmap > 0:
            c.ignore_unmap -= 1
        else:
            self.unmanage(c)

    def on_destroy(self, ev):
        wid = ev.window.id
        c = self.by_client.get(wid)
        if c:
            self.unmanage(c, destroyed=True)
        if wid in self.docks:
            self.docks.pop(wid)
            self.struts.pop(wid, None)
            self.workarea_changed()
        self.desktops.pop(wid, None)

    def on_property(self, ev):
        name = self.RA.get(ev.atom)
        wid = ev.window.id
        if wid in self.docks and name in ("_NET_WM_STRUT", "_NET_WM_STRUT_PARTIAL"):
            self.read_strut(ev.window)
            self.workarea_changed()
            return
        c = self.by_client.get(wid)
        if c and (ev.atom == self.A["_NET_WM_NAME"] or ev.atom == Xatom.WM_NAME):
            title = self.get_title(c.win)
            if title != c.title:
                c.title = title
                self.draw(c)
                self.publish()
        elif c and name == "WM_PROTOCOLS":
            p = self.prop(c.win, "WM_PROTOCOLS")
            c.protocols = [self.RA.get(a, "") for a in p] if p is not None else []

    def on_client_message(self, ev):
        A, t = self.A, ev.client_type
        data = list(ev.data[1])
        c = self.by_client.get(ev.window.id)
        if t == A["_NET_ACTIVE_WINDOW"] and c:
            self.focus(c)
        elif t == A["_NET_CLOSE_WINDOW"] and c:
            self.close(c)
        elif t == A["WM_CHANGE_STATE"] and c and data[0] == ICONIC_STATE:
            self.minimize(c)
        elif t == A["_NET_WM_STATE"] and c:
            action, props = data[0], {self.RA.get(p, "") for p in data[1:3]}
            def want(cur):
                return (action == 1) or (action == 2 and not cur)
            if props & {"_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ"}:
                self.set_max(c, want(c.maxed))
            if "_NET_WM_STATE_FULLSCREEN" in props:
                self.set_fs(c, want(c.fs))
            if "_NET_WM_STATE_ABOVE" in props:
                c.above = want(c.above)
                self.update_state(c)
            if "_NET_WM_STATE_SKIP_TASKBAR" in props:
                c.skip_taskbar = want(c.skip_taskbar)
                self.update_state(c)

    def on_button_press(self, ev):
        c = self.by_client.get(ev.window.id)
        if c:                                         # passive sync grab on the client area
            self.focus(c)
            if ev.state & X.Mod1Mask and ev.detail in (1, 3):
                if ev.detail == 1:
                    self.begin_drag(c, ev.root_x, ev.root_y, "move")
                else:
                    ex = (L if ev.event_x < c.cw / 2 else R) | (T if ev.event_y < c.ch / 2 else B)
                    self.begin_drag(c, ev.root_x, ev.root_y, "resize", ex)
                self.d.allow_events(X.AsyncPointer, ev.time)
            else:
                self.d.allow_events(X.ReplayPointer, ev.time)
            return
        c = self.by_frame.get(ev.window.id)
        if not c or ev.detail != 1:
            return
        self.focus(c)
        r = self.hit(c, ev.event_x, ev.event_y)
        if not r:
            return
        if r[0] == "btn":
            self.press = (c, r[1])
        elif r[0] == "edge":
            self.begin_drag(c, ev.root_x, ev.root_y, "resize", r[1])
        else:
            if ev.time - self.last_click[0] < 400 and self.last_click[1] == c.win.id:
                self.set_max(c, not c.maxed)
                self.last_click = (0, 0)
            else:
                self.last_click = (ev.time, c.win.id)
                self.begin_drag(c, ev.root_x, ev.root_y, "move")

    def on_button_release(self, ev):
        if self.drag:
            self.end_drag()
        if self.press:
            c, name = self.press
            self.press = None
            if c in self.clients and c.frame.id == ev.window.id:
                r = self.hit(c, ev.event_x, ev.event_y)
                if r and r[0] == "btn" and r[1] == name:
                    if name == "close":
                        self.close(c)
                    elif name == "max":
                        self.set_max(c, not c.maxed)
                    else:
                        self.minimize(c)

    def on_motion(self, ev):
        if self.drag:
            self.do_drag(ev.root_x, ev.root_y)
            return
        c = self.by_frame.get(ev.window.id)
        if not c:
            return
        r = self.hit(c, ev.event_x, ev.event_y)
        key = r[1] if r and r[0] == "edge" else 0
        if key != c.cursor_key:
            c.cursor_key = key
            c.frame.change_attributes(cursor=self.cursors.get(key, self.cursors[0]))
        hover = r[1] if r and r[0] == "btn" else None
        if hover != c.hover:
            c.hover = hover
            self.draw(c)

    def on_leave(self, ev):
        c = self.by_frame.get(ev.window.id)
        if c and c.hover and not self.drag:
            c.hover = None
            self.draw(c)

    def on_expose(self, ev):
        c = self.by_frame.get(ev.window.id)
        if c and ev.count == 0:
            self.draw(c)

    def on_key_press(self, ev):
        action = self.keymap.get((ev.detail, ev.state & RELEVANT))
        if action:
            self.run_action(action)

    def on_key_release(self, ev):
        if self.cycling:
            ks = self.d.keycode_to_keysym(ev.detail, 0)
            if ks in (XK.XK_Alt_L, XK.XK_Alt_R, XK.XK_Meta_L, XK.XK_Meta_R):
                self.end_cycle()

    def on_configure_notify(self, ev):
        if ev.window.id == self.root.id and (ev.width, ev.height) != (self.sw, self.sh):
            self.sw, self.sh = ev.width, ev.height
            self.root.change_property(self.A["_NET_DESKTOP_GEOMETRY"], Xatom.CARDINAL, 32, [self.sw, self.sh])
            for dw in self.desktops.values():
                dw.configure(x=0, y=0, width=self.sw, height=self.sh)
            self.workarea_changed()

    # ------------------------------------------------------------------ lifecycle
    def reload(self):
        log("reloading config")
        self.load_theme()
        self.grab_keys()
        for c in self.clients:
            self.relayout(c) if (c.maxed or c.fs) else self.apply(c)

    def run(self):
        while self.running:
            if self.reload_flag:
                self.reload_flag = False
                try:
                    self.reload()
                except Exception:
                    traceback.print_exc()
            last_motion = None
            try:
                while self.d.pending_events():
                    ev = self.d.next_event()
                    if ev.type == X.MotionNotify:
                        last_motion = ev
                        continue
                    if last_motion is not None:
                        self.safe_dispatch(last_motion)
                        last_motion = None
                    self.safe_dispatch(ev)
                if last_motion is not None:
                    self.safe_dispatch(last_motion)
                self.d.flush()
                select.select([self.d], [], [], 1.0)
            except (InterruptedError, select.error):
                continue

    def safe_dispatch(self, ev):
        try:
            self.dispatch(ev)
        except xerror.XError:
            pass
        except Exception:
            traceback.print_exc()

    def shutdown(self):
        for c in list(self.clients):
            try:
                self.unmanage(c)
            except Exception:
                pass
        try:
            self.d.sync()
            os.remove(common.wm_pid_path())
        except Exception:
            pass


def main():
    wm = WindowManager()

    def stop(*_):
        wm.running = False

    def hup(*_):
        wm.reload_flag = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGHUP, hup)
    try:
        wm.run()
    finally:
        wm.shutdown()


if __name__ == "__main__":
    main()
