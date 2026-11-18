#!/usr/bin/env bash
# Boot the ISO in QEMU (UEFI if OVMF is installed, otherwise BIOS).  Usage: tools/test-qemu.sh [ai-custom-os.iso]
set -euo pipefail
ISO="${1:-ai-custom-os.iso}"
ARGS=(-m 2048 -smp 2 -cdrom "$ISO" -boot d -vga virtio -device intel-hda -device hda-duplex -nic user,model=virtio-net-pci)
[ -w /dev/kvm ] && ARGS+=(-enable-kvm -cpu host)
OVMF=/usr/share/ovmf/OVMF.fd
[ -f "$OVMF" ] && ARGS+=(-bios "$OVMF")
exec qemu-system-x86_64 "${ARGS[@]}"
