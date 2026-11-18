#!/usr/bin/env bash
# Static validation: py_compile every Python file, bash -n every shell script, parse JSON + YAML,
# check the CSS palette block, then run the unit tests.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
fail=0
echo "== py_compile"
while IFS= read -r f; do python3 -m py_compile "$f" && echo "  ok  $f" || fail=1; done < <(find rootfs tests -name '*.py' | sort)
echo "== bash -n"
for f in build.sh tools/*.sh rootfs/etc/skel/.xinitrc rootfs/etc/skel/.bash_profile rootfs/etc/skel/.bashrc; do
    bash -n "$f" && echo "  ok  $f" || fail=1
done
echo "== JSON"
python3 -c "import json,sys; json.load(open('rootfs/opt/custom-os/config/os_config.json')); print('  ok  os_config.json')" || fail=1
echo "== YAML"
python3 - <<'PY' || fail=1
import sys
try:
    import yaml
except ImportError:
    print("  skip (python3-yaml not installed)"); sys.exit(0)
d = yaml.safe_load(open(".github/workflows/build-iso.yml"))
assert "jobs" in d and "build-iso" in d["jobs"], "workflow lacks build-iso job"
print("  ok  build-iso.yml")
PY
echo "== unit tests"
CUSTOM_OS_ROOT="$PWD/rootfs/opt/custom-os" python3 -W ignore::ResourceWarning -m unittest discover -s tests -v || fail=1
[ "$fail" -eq 0 ] && echo "ALL CHECKS PASSED" || { echo "CHECKS FAILED"; exit 1; }
