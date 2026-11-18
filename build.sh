#!/usr/bin/env bash
# Build ai-custom-os.iso : Debian 12 minbase + raw Xorg + a fully custom Python/GTK3 desktop.
# Usage:  sudo ./build.sh            (env: MIRROR, WORK, ISO, SQUASH_LEVEL, KEEP_WORK=1)
set -Eeuo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${WORK:-$SRC/build}"
CHROOT="$WORK/chroot"
STAGE="$WORK/staging"
ISO="${ISO:-$SRC/ai-custom-os.iso}"
MIRROR="${MIRROR:-http://deb.debian.org/debian}"
SUITE=bookworm
LABEL=AI_CUSTOM_OS
MAX_MB=750
SQUASH_LEVEL="${SQUASH_LEVEL:-15}"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------------ chroot mount handling
unmount_chroot() {
    local m
    for m in dev/pts dev proc sys run; do
        if mountpoint -q "$CHROOT/$m" 2>/dev/null; then umount -lf "$CHROOT/$m" 2>/dev/null || true; fi
    done
}
mount_chroot() {
    mount -t proc proc "$CHROOT/proc"
    mount -t sysfs sys "$CHROOT/sys"
    mount --bind /dev "$CHROOT/dev"
    mount --bind /dev/pts "$CHROOT/dev/pts"
    mount -t tmpfs tmpfs "$CHROOT/run"
}
cleanup() {
    local rc=$?
    set +e
    unmount_chroot
    if [ "$rc" -ne 0 ]; then printf '\033[1;31mBuild failed (exit %s); chroot mounts released.\033[0m\n' "$rc" >&2; fi
    exit "$rc"
}
trap cleanup EXIT
trap 'die "interrupted"' INT TERM

in_chroot() { chroot "$CHROOT" /usr/bin/env -i HOME=/root TERM="${TERM:-dumb}" PATH=/usr/sbin:/usr/bin:/sbin:/bin "$@"; }

# ------------------------------------------------------------------ 0. preflight
[ "$(id -u)" -eq 0 ] || die "run as root:  sudo ./build.sh"
[ "$(uname -m)" = x86_64 ] || die "this build targets an x86_64 host"

log "Checking host tools"
HOST_PKGS=(debootstrap squashfs-tools xorriso mtools dosfstools syslinux-common isolinux syslinux-utils
           grub-pc-bin grub-efi-amd64-bin grub-common debian-archive-keyring)
missing=()
for p in "${HOST_PKGS[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
if [ "${#missing[@]}" -gt 0 ]; then
    command -v apt-get >/dev/null || die "missing packages: ${missing[*]}"
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing[@]}"
fi
for f in /usr/lib/ISOLINUX/isolinux.bin /usr/lib/ISOLINUX/isohdpfx.bin \
         /usr/lib/syslinux/modules/bios/ldlinux.c32 /usr/lib/syslinux/modules/bios/libcom32.c32 \
         /usr/lib/syslinux/modules/bios/libutil.c32 /usr/lib/syslinux/modules/bios/vesamenu.c32 \
         /usr/lib/grub/i386-pc /usr/lib/grub/x86_64-efi; do
    [ -e "$f" ] || die "host is missing $f"
done

# ------------------------------------------------------------------ 1. debootstrap
unmount_chroot
if [ ! -f "$CHROOT/.bootstrap-done" ]; then
    log "debootstrap --variant=minbase $SUITE"
    rm -rf "$CHROOT" "$STAGE"
    mkdir -p "$CHROOT" "$STAGE"
    debootstrap --variant=minbase --arch=amd64 --components=main,non-free-firmware "$SUITE" "$CHROOT" "$MIRROR"
    touch "$CHROOT/.bootstrap-done"
else
    log "Re-using existing bootstrap in $CHROOT (delete $WORK to start clean)"
fi

# ------------------------------------------------------------------ 2. packages
log "Preparing chroot"
cat > "$CHROOT/etc/apt/sources.list" <<APT
deb $MIRROR $SUITE main non-free-firmware
deb $MIRROR $SUITE-updates main non-free-firmware
deb http://deb.debian.org/debian-security $SUITE-security main non-free-firmware
APT
mkdir -p "$CHROOT/etc/dpkg/dpkg.cfg.d"
cat > "$CHROOT/etc/dpkg/dpkg.cfg.d/01-nodoc" <<'DPKG'
path-exclude /usr/share/doc/*
path-include /usr/share/doc/*/copyright
path-exclude /usr/share/man/*
path-exclude /usr/share/info/*
path-exclude /usr/share/locale/*
path-include /usr/share/locale/locale.alias
DPKG
printf 'nameserver 1.1.1.1\nnameserver 9.9.9.9\n' > "$CHROOT/etc/resolv.conf"
printf '#!/bin/sh\nexit 101\n' > "$CHROOT/usr/sbin/policy-rc.d"
chmod +x "$CHROOT/usr/sbin/policy-rc.d"
install -m 0755 "$SRC/tools/chroot-setup.sh" "$CHROOT/root/chroot-setup.sh"

mount_chroot
log "Installing packages (kernel, live-boot, Xorg, NetworkManager, PipeWire, Python/GTK bindings)"
in_chroot /root/chroot-setup.sh packages

# ------------------------------------------------------------------ 3. our OS userland
log "Installing the custom OS tree and configuring the live user"
cp -a --no-preserve=ownership "$SRC/rootfs/." "$CHROOT/"
in_chroot /root/chroot-setup.sh configure
in_chroot /root/chroot-setup.sh clean
rm -f "$CHROOT/root/chroot-setup.sh"
unmount_chroot

# ------------------------------------------------------------------ 4. kernel, initrd, squashfs
log "Staging ISO tree"
rm -rf "$STAGE"
mkdir -p "$STAGE"/{live,isolinux,boot/grub,EFI/BOOT}
KERNEL="$(ls "$CHROOT"/boot/vmlinuz-* | sort -V | tail -n1)"
INITRD="$(ls "$CHROOT"/boot/initrd.img-* | sort -V | tail -n1)"
[ -f "$KERNEL" ] && [ -f "$INITRD" ] || die "kernel/initrd not found in chroot"
cp "$KERNEL" "$STAGE/live/vmlinuz"
cp "$INITRD" "$STAGE/live/initrd.img"

log "mksquashfs (zstd level $SQUASH_LEVEL)"
mksquashfs "$CHROOT" "$STAGE/live/filesystem.squashfs" -comp zstd -Xcompression-level "$SQUASH_LEVEL" \
    -b 1M -noappend -no-progress -e boot -e .bootstrap-done
echo "$LABEL" > "$STAGE/$LABEL"

# ------------------------------------------------------------------ 5. BIOS (isolinux) + UEFI (grub) boot files
log "Boot loaders (isolinux + grub-efi)"
SYS=/usr/lib/syslinux/modules/bios
cp /usr/lib/ISOLINUX/isolinux.bin "$SYS/ldlinux.c32" "$SYS/libcom32.c32" "$SYS/libutil.c32" "$SYS/vesamenu.c32" \
   "$STAGE/isolinux/"
cat > "$STAGE/isolinux/isolinux.cfg" <<'CFG'
UI vesamenu.c32
DEFAULT live
PROMPT 0
TIMEOUT 50
MENU TITLE AI Custom OS
MENU COLOR border 0 #00000000 #00000000 none
MENU COLOR title  1 #ff2fb7b0 #00000000 none
MENU COLOR sel    7 #ff06201f #ff2fb7b0 none

LABEL live
  MENU LABEL AI Custom OS (live)
  KERNEL /live/vmlinuz
  APPEND initrd=/live/initrd.img boot=live components quiet

LABEL toram
  MENU LABEL AI Custom OS (copy to RAM, then remove the USB stick)
  KERNEL /live/vmlinuz
  APPEND initrd=/live/initrd.img boot=live components toram quiet

LABEL safe
  MENU LABEL AI Custom OS (safe graphics: nomodeset)
  KERNEL /live/vmlinuz
  APPEND initrd=/live/initrd.img boot=live components nomodeset
CFG

cat > "$STAGE/boot/grub/grub.cfg" <<'CFG'
set default=0
set timeout=5
insmod all_video
menuentry "AI Custom OS (live)" {
    linux /live/vmlinuz boot=live components quiet
    initrd /live/initrd.img
}
menuentry "AI Custom OS (copy to RAM)" {
    linux /live/vmlinuz boot=live components toram quiet
    initrd /live/initrd.img
}
menuentry "AI Custom OS (safe graphics)" {
    linux /live/vmlinuz boot=live components nomodeset
    initrd /live/initrd.img
}
CFG
cat > "$WORK/grub-embed.cfg" <<CFG
search --no-floppy --set=root --file /$LABEL
set prefix=(\$root)/boot/grub
configfile (\$root)/boot/grub/grub.cfg
CFG
grub-mkstandalone --format=x86_64-efi --output="$WORK/BOOTx64.EFI" --locales="" --fonts="" \
    --modules="part_gpt part_msdos fat iso9660 search search_fs_file normal linux configfile echo all_video" \
    "boot/grub/grub.cfg=$WORK/grub-embed.cfg"
cp "$WORK/BOOTx64.EFI" "$STAGE/EFI/BOOT/BOOTx64.EFI"
EFI_IMG="$STAGE/boot/grub/efi.img"
truncate -s 16M "$EFI_IMG"
mkfs.vfat -n AICUSTOMOS "$EFI_IMG" >/dev/null
mmd -i "$EFI_IMG" ::EFI ::EFI/BOOT
mcopy -i "$EFI_IMG" "$WORK/BOOTx64.EFI" ::EFI/BOOT/BOOTx64.EFI

# ------------------------------------------------------------------ 6. hybrid ISO
log "xorriso -> $ISO"
rm -f "$ISO"
xorriso -as mkisofs -iso-level 3 -full-iso9660-filenames -volid "$LABEL" -output "$ISO" \
    -isohybrid-mbr /usr/lib/ISOLINUX/isohdpfx.bin \
    -eltorito-boot isolinux/isolinux.bin -eltorito-catalog isolinux/boot.cat \
    -no-emul-boot -boot-load-size 4 -boot-info-table \
    -eltorito-alt-boot -e boot/grub/efi.img -no-emul-boot -isohybrid-gpt-basdat \
    "$STAGE"

# ------------------------------------------------------------------ 7. verify
log "Verifying"
SIZE_MB=$(( $(stat -c %s "$ISO") / 1024 / 1024 ))
xorriso -indev "$ISO" -report_el_torito plain 2>/dev/null | grep -E 'El Torito|BIOS|UEFI' || true
LISTING="$(xorriso -indev "$ISO" -find / -type f 2>/dev/null)"
for f in /live/vmlinuz /live/initrd.img /live/filesystem.squashfs /isolinux/isolinux.bin /isolinux/ldlinux.c32 \
         /isolinux/libcom32.c32 /isolinux/libutil.c32 /isolinux/vesamenu.c32 /boot/grub/efi.img /EFI/BOOT/BOOTx64.EFI; do
    grep -qF "$f" <<<"$LISTING" || die "ISO is missing $f"
done
( cd "$(dirname "$ISO")" && sha256sum "$(basename "$ISO")" > "$ISO.sha256" )
echo "ISO size: ${SIZE_MB} MB (limit ${MAX_MB} MB)"
[ "$SIZE_MB" -le "$MAX_MB" ] || die "ISO is ${SIZE_MB} MB, over the ${MAX_MB} MB budget"
[ "${KEEP_WORK:-0}" = 1 ] || log "Tip: rm -rf $WORK to reclaim disk space"
log "Done: $ISO"
