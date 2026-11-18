# AI Custom OS

A bootable Debian 12 (`minbase` + raw Xorg) live ISO whose **entire desktop is custom Python + GTK3 + CSS**:
window manager, desktop shell, taskbar, launcher and four default apps. No GNOME/KDE/XFCE/LXQt, no Openbox/i3,
no Tint2/Polybar/Thunar/Rofi.

## Build

* **GitHub Actions:** push to `main` → *Actions → Build ISO* → download the `ai-custom-os-iso` artifact.
* **Locally (Debian/Ubuntu x86_64, root):** `sudo ./build.sh` → `ai-custom-os.iso` + `.sha256`.
  The script fails if the ISO exceeds 750 MB. `tools/test-qemu.sh` boots it in QEMU.
* **Check the sources without building:** `./tools/validate.sh` (py_compile, bash -n, JSON, YAML, unit tests).

Boot: UEFI (GRUB standalone EFI image) and Legacy BIOS (isolinux) from one hybrid ISO; write it to USB with
`dd` or any ISO writer. Secure Boot must be off (no signed shim). Login is automatic as `user` (password `live`,
passwordless `sudo`).

## Layout (`rootfs/` is copied onto the image)

| Path | Role |
|---|---|
| `opt/custom-os/config/os_config.json` | single source of truth: panel position/height, icon size, wallpaper, pinned apps, shortcuts, startup commands |
| `opt/custom-os/config/theme.css` | GTK3 stylesheet; the `@define-color` block is managed by Settings, the rest is hand-editable |
| `opt/custom-os/core/wm.py` | window manager (python-xlib, cairo/Pango titlebars) |
| `opt/custom-os/core/desktop_shell.py` | desktop canvas, taskbar, tray, launcher, control socket |
| `opt/custom-os/apps/` | `file_manager.py` (Folder), `settings.py`, `terminal.py`, `editor.py` (Notes) |
| `etc/skel/.xinitrc`, `.bash_profile` | tty1 auto-login → `startx` → WM → shell |

Live reload: Settings and Notes (when saving the config files) call `common.reload_all()`, which sends `SIGHUP`
to the WM and `reload` over the shell's UNIX socket (`$XDG_RUNTIME_DIR/custom-os-shell-<uid>.sock`).

Default shortcuts (editable in `os_config.json`): `Super+Space` launcher, `Super+Return` terminal, `Super+E` files,
`Super+I` settings, `Super+D` show desktop, `Super+Up/Down` maximise/minimise, `Alt+Tab`, `Alt+F4`,
`Alt+drag` move, `Alt+right-drag` resize.

## Notes

* Third-party *assets* (not desktop components) installed: fonts (Inter, JetBrains Mono, DejaVu, Noto Color Emoji),
  Adwaita icon theme for GTK's stock dialogs, Wi-Fi/GPU firmware.
* `nm-applet` is optional: the tray talks to NetworkManager itself, so it is started only if installed.
* Panel transparency in the CSS only shows with a compositor; there is none by design.
