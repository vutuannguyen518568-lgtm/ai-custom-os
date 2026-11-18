# Auto-start the custom desktop on tty1.
export LANG=C.UTF-8
export NO_AT_BRIDGE=1
[ -f ~/.bashrc ] && . ~/.bashrc
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    mkdir -p ~/.cache/custom-os
    exec startx -- -nolisten tcp vt1 >~/.cache/custom-os/xorg.log 2>&1
fi
