"""Tiny GTK dialog helpers shared by the shell and apps."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


def _dialog(parent, title, ok_label, danger=False):
    d = Gtk.Dialog(title=title, transient_for=parent, modal=True)
    d.set_keep_above(True)
    d.set_default_size(380, -1)
    d.add_button("Cancel", Gtk.ResponseType.CANCEL)
    b = d.add_button(ok_label, Gtk.ResponseType.OK)
    b.get_style_context().add_class("destructive" if danger else "suggested")
    d.set_default_response(Gtk.ResponseType.OK)
    box = d.get_content_area()
    box.set_spacing(10)
    box.set_border_width(16)
    return d, box


def ask_text(parent, title, label, default="", ok_label="OK"):
    d, box = _dialog(parent, title, ok_label)
    box.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
    e = Gtk.Entry(text=default, activates_default=True)
    box.pack_start(e, False, False, 0)
    d.show_all()
    e.grab_focus()
    if default:
        stem = default.rsplit(".", 1)[0] if "." in default.strip(".") else default
        e.select_region(0, len(stem))
    r = d.run()
    text = e.get_text().strip()
    d.destroy()
    return text if r == Gtk.ResponseType.OK and text else None


def confirm(parent, title, text, ok_label="Confirm", danger=False):
    d, box = _dialog(parent, title, ok_label, danger)
    lab = Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=48)
    box.pack_start(lab, False, False, 0)
    d.show_all()
    r = d.run()
    d.destroy()
    return r == Gtk.ResponseType.OK


def notify(parent, title, text):
    d = Gtk.Dialog(title=title, transient_for=parent, modal=True)
    d.set_keep_above(True)
    d.add_button("OK", Gtk.ResponseType.OK).get_style_context().add_class("suggested")
    box = d.get_content_area()
    box.set_border_width(16)
    box.pack_start(Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=56, selectable=True), False, False, 0)
    d.show_all()
    d.run()
    d.destroy()


def ask_password(parent, ssid):
    d, box = _dialog(parent, "Connect to " + ssid, "Connect")
    box.pack_start(Gtk.Label(label="Password for “%s”" % ssid, xalign=0), False, False, 0)
    e = Gtk.Entry(visibility=False, activates_default=True)
    box.pack_start(e, False, False, 0)
    d.show_all()
    r = d.run()
    pw = e.get_text()
    d.destroy()
    return pw if r == Gtk.ResponseType.OK and pw else None
