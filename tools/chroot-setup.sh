#!/bin/bash
# Runs INSIDE the Debian 12 chroot.  Usage: chroot-setup.sh packages | configure
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive LC_ALL=C LANG=C
APT=(apt-get -y --no-install-recommends -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)

phase_packages() {
    apt-get update
    # live-boot must exist before the kernel so its initramfs hooks are included
    "${APT[@]}" install initramfs-tools live-boot

    local base=(linux-image-amd64 systemd systemd-sysv udev dbus dbus-user-session dbus-x11 libpam-systemd
                policykit-1 sudo ca-certificates tzdata kmod procps psmisc iproute2 iputils-ping curl less
                bash-completion)
    local xorg=(xserver-xorg-core xserver-xorg-legacy xinit x11-xserver-utils xserver-xorg-input-libinput
                xserver-xorg-video-all libgl1-mesa-dri)
    local net=(network-manager wpasupplicant wireless-regdb iw rfkill)
    local firmware=(firmware-iwlwifi firmware-realtek firmware-atheros firmware-brcm80211 firmware-amd-graphics)
    local audio=(pipewire-audio pulseaudio-utils alsa-utils)
    local storage=(udisks2 ntfs-3g exfatprogs dosfstools)
    # python bindings only: every desktop component is our own Python + GTK3 + CSS
    local py=(python3 python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-vte-2.91 python3-xlib python3-psutil
              gir1.2-pango-1.0 gir1.2-gdkpixbuf-2.0)
    # assets used by GTK itself (fonts, icon theme for stock dialogs, mime database)
    local assets=(fonts-dejavu-core fonts-inter fonts-jetbrains-mono fonts-noto-color-emoji adwaita-icon-theme
                  librsvg2-common shared-mime-info)

    "${APT[@]}" install "${base[@]}" "${xorg[@]}" "${net[@]}" "${firmware[@]}" "${audio[@]}" "${storage[@]}" \
                        "${py[@]}" "${assets[@]}"
}

phase_configure() {
    echo ai-custom-os > /etc/hostname
    printf '127.0.0.1 localhost\n127.0.1.1 ai-custom-os\n' > /etc/hosts
    echo 'LANG=C.UTF-8' > /etc/default/locale
    ln -sf /usr/share/zoneinfo/Etc/UTC /etc/localtime
    echo Etc/UTC > /etc/timezone

    for g in sudo audio video netdev plugdev input render cdrom; do
        getent group "$g" >/dev/null || groupadd -r "$g"
    done
    id user >/dev/null 2>&1 || useradd -m -s /bin/bash -G sudo,audio,video,netdev,plugdev,input,render,cdrom user
    echo 'user:live' | chpasswd
    chmod 0440 /etc/sudoers.d/90-live-user
    install -d -o user -g user /home/user/Desktop /home/user/Documents /home/user/Downloads

    chmod +x /opt/custom-os/core/*.py /opt/custom-os/apps/*.py /opt/custom-os/tools/*.py
    python3 /opt/custom-os/tools/gen_wallpapers.py /opt/custom-os/wallpapers
    chown -R user:user /opt/custom-os /home/user
    chmod +x /home/user/.xinitrc
    python3 -m compileall -q /opt/custom-os || true
    fc-cache -f >/dev/null 2>&1 || true

    systemctl enable NetworkManager.service
    systemctl mask NetworkManager-wait-online.service systemd-networkd-wait-online.service
    systemctl set-default multi-user.target
    update-initramfs -u -k all
}

phase_clean() {
    apt-get clean
    rm -rf /var/lib/apt/lists/* /var/cache/apt/*.bin /var/cache/debconf/*-old /var/lib/dpkg/*-old /tmp/* /root/.cache
    find /var/log -type f -delete
    : > /etc/machine-id
    rm -f /var/lib/dbus/machine-id /etc/resolv.conf /usr/sbin/policy-rc.d
    : > /etc/resolv.conf
}

case "${1:-}" in
    packages)  phase_packages ;;
    configure) phase_configure ;;
    clean)     phase_clean ;;
    *) echo "usage: $0 packages|configure|clean" >&2; exit 2 ;;
esac
