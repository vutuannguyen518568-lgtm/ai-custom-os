import os, sys, tempfile, unittest, zipfile, tarfile, json
ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rootfs", "opt", "custom-os")
sys.path.insert(0, ROOT)
os.environ["CUSTOM_OS_ROOT"] = ROOT
from core import common, fileops


class Config(unittest.TestCase):
    def test_config_loads_and_has_required_keys(self):
        cfg = common.load_config()
        for k in ("panel_position", "panel_height", "icon_size", "wallpaper_path"):
            self.assertIn(k, cfg["desktop"])
        self.assertIn(cfg["desktop"]["panel_position"], ("top", "bottom"))
        self.assertTrue(cfg["pinned_apps"] and cfg["shortcuts"] and cfg["startup_commands"])
        names = {a["name"] for a in cfg["apps"]}
        self.assertTrue(set(cfg["pinned_apps"]) <= names)

    def test_every_exec_points_to_existing_script(self):
        for a in common.load_config()["apps"]:
            script = common.expand(a["exec"]).split()[1]
            self.assertTrue(os.path.exists(script), script)

    def test_parse_color(self):
        self.assertEqual(common.parse_color("#ff0000"), (1.0, 0.0, 0.0, 1.0))
        r, g, b, a = common.parse_color("rgba(14,17,24,0.94)")
        self.assertAlmostEqual(a, 0.94)
        with self.assertRaises(ValueError):
            common.parse_color("blue")


class Theme(unittest.TestCase):
    def test_palette_block_roundtrip(self):
        cfg = common.load_config()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "theme.css")
            with open(common.THEME_PATH) as f:
                css = f.read()
            open(p, "w").write(css)
            cfg["desktop"]["mode"], cfg["desktop"]["accent"] = "light", "#e0a030"
            common.write_theme_vars(cfg, p)
            out = open(p).read()
            cols = common.parse_theme_colors(p)
            self.assertAlmostEqual(cols["accent"][0], 0xe0 / 255, places=3)
            self.assertAlmostEqual(cols["win_bg"][0], 0xee / 255, places=3)
            self.assertIn("button {", out)                       # hand-written rules untouched
            self.assertEqual(out.count("@define-color accent "), 1)

    def test_shipped_theme_matches_default_palette(self):
        cols = common.parse_theme_colors()
        for k in common.PALETTE_ORDER:
            self.assertIn(k, cols, k)

    def test_accent_foreground_contrast(self):
        self.assertEqual(common.palette({"desktop": {"mode": "dark", "accent": "#f2d34a"}})["accent_fg"], "#06201f")
        self.assertEqual(common.palette({"desktop": {"mode": "dark", "accent": "#1a237e"}})["accent_fg"], "#ffffff")


class FileOps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        fileops.TRASH = os.path.join(self.d, "trash")
        fileops.TRASH_FILES = os.path.join(fileops.TRASH, "files")
        fileops.TRASH_INFO = os.path.join(fileops.TRASH, "info")

    def tearDown(self):
        self.tmp.cleanup()

    def w(self, name, text="x"):
        p = os.path.join(self.d, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(text)
        return p

    def test_unique_and_copy_move(self):
        a = self.w("a.txt")
        sub = os.path.join(self.d, "sub"); os.mkdir(sub)
        c1 = fileops.copy_item(a, self.d)
        self.assertTrue(c1.endswith("a (copy).txt"))
        c2 = fileops.copy_item(a, self.d)
        self.assertTrue(c2.endswith("a (copy 2).txt"))
        m = fileops.move_item(a, sub)
        self.assertTrue(os.path.exists(m) and not os.path.exists(a))
        with self.assertRaises(ValueError):
            fileops.copy_item(sub, os.path.join(sub))

    def test_rename_new_delete(self):
        f = fileops.new_file(self.d, "n.txt")
        self.assertEqual(fileops.new_file(self.d, "n.txt"), os.path.join(self.d, "n (copy).txt"))
        r = fileops.rename_item(f, "z.txt")
        self.assertTrue(os.path.exists(r))
        with self.assertRaises(ValueError):
            fileops.rename_item(r, "../evil")
        fileops.delete_item(r)
        self.assertFalse(os.path.exists(r))

    def test_trash_restore_purge(self):
        p = self.w("doc dir/hello world.txt", "hi")
        t = fileops.trash_item(p)
        self.assertFalse(os.path.exists(p))
        self.assertEqual(fileops.trash_original(t), p)
        back = fileops.restore_item(t)
        self.assertEqual(back, p)
        self.assertEqual(open(back).read(), "hi")
        t2 = fileops.trash_item(back)
        fileops.empty_trash()
        self.assertFalse(os.path.exists(t2))

    def test_zip_and_targz_roundtrip(self):
        self.w("pack/one.txt", "1"); self.w("pack/inner/two.txt", "2")
        for kind in ("zip", "tar.gz"):
            out = fileops.compress([os.path.join(self.d, "pack")], kind)
            self.assertTrue(fileops.is_archive(out))
            dest = fileops.extract(out)
            self.assertEqual(open(os.path.join(dest, "pack", "inner", "two.txt")).read(), "2")

    def test_extract_rejects_path_traversal(self):
        z = os.path.join(self.d, "bad.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("../escape.txt", "boom")
        with self.assertRaises(ValueError):
            fileops.extract(z)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.d), "escape.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.d, "bad")))     # partial output cleaned up
        t = os.path.join(self.d, "bad.tar")
        with tarfile.open(t, "w") as tf:
            info = tarfile.TarInfo("../../escape2.txt"); info.size = 1
            import io
            tf.addfile(info, io.BytesIO(b"x"))
        with self.assertRaises(ValueError):
            fileops.extract(t)


class Classify(unittest.TestCase):
    def test_classify(self):
        with tempfile.TemporaryDirectory() as d:
            t = os.path.join(d, "a.py"); open(t, "w").write("print(1)")
            b = os.path.join(d, "blob.bin"); open(b, "wb").write(b"\0\1\2")
            self.assertEqual(common.classify(d), "dir")
            self.assertEqual(common.classify(t), "text")
            self.assertEqual(common.classify(b), "binary")
            self.assertEqual(common.classify(os.path.join(d, "x.tar.gz")), "archive")
            self.assertEqual(common.classify(os.path.join(d, "x.PNG")), "image")


class Lsblk(unittest.TestCase):
    def test_device_filter(self):
        sys.modules.setdefault("gi", None)      # file_manager needs GTK; test only the pure filter via source slicing
        src = open(os.path.join(ROOT, "apps", "file_manager.py")).read()
        start = src.index("def list_devices")
        end = src.index("class Folder")
        ns = {"subprocess": __import__("subprocess"), "json": json}
        exec(src[start:end], ns)
        sample = {"blockdevices": [
            {"name": "sda", "path": "/dev/sda", "type": "disk", "rm": False, "hotplug": True, "fstype": None,
             "children": [{"name": "sda1", "path": "/dev/sda1", "type": "part", "fstype": "iso9660",
                           "mountpoint": "/run/live/medium", "mountpoints": ["/run/live/medium"]}]},
            {"name": "sdb", "path": "/dev/sdb", "type": "disk", "rm": "1", "hotplug": "1", "fstype": None, "size": "8G",
             "children": [{"name": "sdb1", "path": "/dev/sdb1", "label": "STICK", "size": "8G", "type": "part",
                           "fstype": "vfat", "mountpoint": None, "mountpoints": [None]}]},
            {"name": "sdc", "path": "/dev/sdc", "type": "disk", "rm": False, "hotplug": False, "fstype": None,
             "children": [{"name": "sdc1", "path": "/dev/sdc1", "type": "part", "fstype": "swap", "mountpoint": "[SWAP]"}]}]}
        class R: stdout = json.dumps(sample)
        ns["subprocess"] = type("S", (), {"run": staticmethod(lambda *a, **k: R()), "SubprocessError": Exception})
        devs = ns["list_devices"]()
        self.assertEqual([d["path"] for d in devs], ["/dev/sdb1"])
        self.assertTrue(devs[0]["removable"] and devs[0]["mount"] is None)


if __name__ == "__main__":
    unittest.main()
