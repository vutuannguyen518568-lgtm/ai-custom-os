"""Pure-Python file operations used by the file manager (no GTK, unit-testable)."""
import os, shutil, tarfile, time, zipfile, urllib.parse

HOME = os.path.expanduser("~")
TRASH = os.path.join(HOME, ".local", "share", "Trash")
TRASH_FILES = os.path.join(TRASH, "files")
TRASH_INFO = os.path.join(TRASH, "info")
TAR_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz")


def split_ext(path):
    for s in TAR_SUFFIXES:
        if path.lower().endswith(s):
            return path[:-len(s)], path[-len(s):]
    return os.path.splitext(path)


def unique_path(path):
    if not os.path.lexists(path):
        return path
    base, ext = split_ext(path)
    n = 1
    while True:
        cand = "%s (copy%s)%s" % (base, "" if n == 1 else " %d" % n, ext)
        if not os.path.lexists(cand):
            return cand
        n += 1


def _inside(child, parent):
    child, parent = os.path.realpath(child), os.path.realpath(parent)
    return child == parent or child.startswith(parent + os.sep)


def copy_item(src, dst_dir):
    dst = unique_path(os.path.join(dst_dir, os.path.basename(src.rstrip("/"))))
    if os.path.isdir(src) and not os.path.islink(src):
        if _inside(dst_dir, src):
            raise ValueError("Cannot copy a folder into itself.")
        shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copy2(src, dst, follow_symlinks=False)
    return dst


def move_item(src, dst_dir):
    if os.path.realpath(os.path.dirname(src)) == os.path.realpath(dst_dir):
        return src
    if os.path.isdir(src) and _inside(dst_dir, src):
        raise ValueError("Cannot move a folder into itself.")
    dst = unique_path(os.path.join(dst_dir, os.path.basename(src.rstrip("/"))))
    shutil.move(src, dst)
    return dst


def delete_item(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def new_folder(parent, name="New Folder"):
    p = unique_path(os.path.join(parent, name))
    os.makedirs(p)
    return p


def new_file(parent, name="New File.txt"):
    p = unique_path(os.path.join(parent, name))
    open(p, "x").close()
    return p


def rename_item(path, new_name):
    if "/" in new_name or new_name in (".", ".."):
        raise ValueError("Invalid name.")
    dst = os.path.join(os.path.dirname(path), new_name)
    if os.path.lexists(dst):
        raise FileExistsError("“%s” already exists." % new_name)
    os.rename(path, dst)
    return dst


# ------------------------------------------------------------------ trash (freedesktop layout)
def trash_item(path):
    os.makedirs(TRASH_FILES, exist_ok=True)
    os.makedirs(TRASH_INFO, exist_ok=True)
    path = os.path.abspath(path)
    name = os.path.basename(path)
    dst = unique_path(os.path.join(TRASH_FILES, name))
    name = os.path.basename(dst)
    with open(os.path.join(TRASH_INFO, name + ".trashinfo"), "w", encoding="utf-8") as f:
        f.write("[Trash Info]\nPath=%s\nDeletionDate=%s\n" %
                (urllib.parse.quote(path), time.strftime("%Y-%m-%dT%H:%M:%S")))
    shutil.move(path, dst)
    return dst


def trash_original(trashed_path):
    info = os.path.join(TRASH_INFO, os.path.basename(trashed_path) + ".trashinfo")
    try:
        with open(info, encoding="utf-8") as f:
            for line in f:
                if line.startswith("Path="):
                    return urllib.parse.unquote(line[5:].strip())
    except OSError:
        pass
    return None


def restore_item(trashed_path):
    orig = trash_original(trashed_path) or os.path.join(HOME, os.path.basename(trashed_path))
    os.makedirs(os.path.dirname(orig), exist_ok=True)
    dst = unique_path(orig)
    shutil.move(trashed_path, dst)
    try:
        os.remove(os.path.join(TRASH_INFO, os.path.basename(trashed_path) + ".trashinfo"))
    except OSError:
        pass
    return dst


def purge_trashed(trashed_path):
    delete_item(trashed_path)
    try:
        os.remove(os.path.join(TRASH_INFO, os.path.basename(trashed_path) + ".trashinfo"))
    except OSError:
        pass


def empty_trash():
    for d in (TRASH_FILES, TRASH_INFO):
        if os.path.isdir(d):
            for n in os.listdir(d):
                delete_item(os.path.join(d, n))


# ------------------------------------------------------------------ archives
def is_archive(path):
    return path.lower().endswith((".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz"))


def extract(path, dest_parent=None):
    """Extract into a new folder next to the archive; rejects path-traversal members."""
    parent = dest_parent or os.path.dirname(os.path.abspath(path))
    stem = os.path.basename(path)
    for s in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz", ".tar", ".zip"):
        if stem.lower().endswith(s):
            stem = stem[:-len(s)]
            break
    dest = unique_path(os.path.join(parent, stem or "extracted"))
    os.makedirs(dest)
    try:
        if path.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                for m in zf.infolist():
                    if not _inside(os.path.join(dest, m.filename), dest):
                        raise ValueError("Unsafe path in archive: " + m.filename)
                zf.extractall(dest)
        else:
            with tarfile.open(path) as tf:
                safe = []
                for m in tf.getmembers():
                    if not _inside(os.path.join(dest, m.name), dest):
                        raise ValueError("Unsafe path in archive: " + m.name)
                    if (m.issym() or m.islnk()) and not _inside(
                            os.path.join(dest, os.path.dirname(m.name), m.linkname), dest):
                        continue
                    if m.isdev():
                        continue
                    safe.append(m)
                try:
                    tf.extractall(dest, members=safe, filter="data")
                except TypeError:
                    tf.extractall(dest, members=safe)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return dest


def compress(paths, kind="zip", out_dir=None):
    first = paths[0].rstrip("/")
    out_dir = out_dir or os.path.dirname(first)
    base = os.path.basename(first) if len(paths) == 1 else "Archive"
    ext = ".zip" if kind == "zip" else ".tar.gz"
    out = unique_path(os.path.join(out_dir, os.path.splitext(base)[0] + ext if os.path.isfile(first) and len(paths) == 1 else base + ext))
    if kind == "zip":
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in paths:
                p = p.rstrip("/")
                if os.path.isdir(p) and not os.path.islink(p):
                    zf.write(p, os.path.basename(p))
                    for root, dirs, files in os.walk(p):
                        for n in dirs + files:
                            full = os.path.join(root, n)
                            zf.write(full, os.path.join(os.path.basename(p), os.path.relpath(full, p)))
                else:
                    zf.write(p, os.path.basename(p))
    else:
        with tarfile.open(out, "w:gz") as tf:
            for p in paths:
                tf.add(p.rstrip("/"), arcname=os.path.basename(p.rstrip("/")))
    return out


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
