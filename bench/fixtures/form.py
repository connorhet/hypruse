"""The benchmark's GTK 3 form. Every change is written to the state file given as the first
argument, so a task is judged by what the app itself holds, not by what hypruse reports.

Run with the system Python, which has PyGObject: /usr/bin/python3 form.py STATE [agree]
"""

import json
import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

STATE = sys.argv[1]
COLOURS = ("Red", "Green", "Blue")
TABS = ("General", "Advanced", "About")

w = Gtk.Window(title="Bench form")
w.set_default_size(460, 460)
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=10)
w.add(box)

volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
volume.set_value(20)
volume.get_accessible().set_name("Volume")
count = Gtk.SpinButton.new_with_range(0, 50, 1)
count.set_value(5)
count.get_accessible().set_name("Count")
colour = Gtk.ComboBoxText()
for c in COLOURS:
    colour.append_text(c)
colour.set_active(0)
colour.get_accessible().set_name("Colour")
agree = Gtk.CheckButton(label="Agree")
agree.set_active(len(sys.argv) > 2 and sys.argv[2] == "agree")
title = Gtk.Entry()
title.set_text("Untitled")
title.get_accessible().set_name("Title")
tabs = Gtk.Notebook()
for t in TABS:
    tabs.append_page(Gtk.Label(label=f"{t} settings"), Gtk.Label(label=t))

for label, widget in (("Volume", volume), ("Count", count), ("Colour", colour), (None, agree),
                      ("Title", title), (None, tabs)):
    if label:
        box.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
    box.pack_start(widget, False, False, 0)


def save(*_args):
    state = {"volume": volume.get_value(), "count": count.get_value_as_int(),
             "colour": colour.get_active_text(), "agree": agree.get_active(),
             "title": title.get_text(), "tab": TABS[tabs.get_current_page()]}
    with open(STATE, "w") as f:
        json.dump(state, f)


volume.connect("value-changed", save)
count.connect("value-changed", save)
colour.connect("changed", save)
agree.connect("toggled", save)
title.connect("changed", save)
tabs.connect_after("switch-page", save)  # after: the new page is current by then
w.connect("destroy", Gtk.main_quit)
w.show_all()
save()
Gtk.main()
