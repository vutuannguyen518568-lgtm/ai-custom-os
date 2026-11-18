#!/bin/sh
# Session script: window manager first, then the shell (supervised), then background services.
ROOT=/opt/custom-os
LOG=$HOME/.cache/custom-os
mkdir -p "$LOG" ~/Desktop ~/Documents ~/Downloads
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export GTK_THEME_VARIANT=dark NO_AT_BRIDGE=1 LANG=C.UTF-8
export XDG_CURRENT_DESKTOP=CustomOS

# a session bus is required by NetworkManager/udisks/pipewire clients
if [ -z "$DBUS_SESSION_BUS_ADDRESS" ] && [ ! -S "$XDG_RUNTIME_DIR/bus" ] && command -v dbus-launch >/dev/null; then
    eval "$(dbus-launch --sh-syntax --exit-with-session)"
fi

xset s off -dpms 2>/dev/null
xsetroot -solid '#12151d' -cursor_name left_ptr 2>/dev/null

python3 $ROOT/core/wm.py >"$LOG/wm.log" 2>&1 &
WM=$!
i=0
while [ ! -s "$XDG_RUNTIME_DIR/custom-os-wm-$(id -u).pid" ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done

( while kill -0 $WM 2>/dev/null; do python3 $ROOT/core/desktop_shell.py >>"$LOG/shell.log" 2>&1; sleep 1; done ) &
python3 $ROOT/core/startup.py >"$LOG/startup.log" 2>&1 &

wait $WM
