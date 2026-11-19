import tempfile
import unittest
from pathlib import Path

import whmx_gallery as g

ROWS = {
    "collectCGs": [{"id": "CG61001", "pathCG": "hotelyard_bg.png", "nameCGLanText": "一尊江月", "sourceTipLanText": "通过活动获取"},
                   {"id": "CG61099", "pathCG": "", "nameCGLanText": "空"}],
    "BGSwitchMap": [{"Id": "1001", "Image": "ui_home_bj_di", "NameLanText": "博行派遣"}],
    "TravelPostcardMap": [{"Id": "1", "SetContent1": "ui_lx_lxwl_mxp7", "SetContent2LanText": "枫桥依旧，佳话成诗，;静行听钟声。",
                           "SetContent4LanText": "寒山寺"}],
}


class TableEntriesTest(unittest.TestCase):
    def setUp(self):
        self.items = {(e["kind"], e["id"]): e for e in g.table_entries(ROWS, ["ui_kv3401.ab", "ui_kvhuazhao.ab"])}

    def test_loading_cg(self):
        e = self.items[("loading", "CG61001")]
        self.assertEqual(e["bundles"], ["images_novel_background_hotelyard_bg.ab"])
        self.assertEqual((e["tex"], e["name_cn"], e["note_cn"]), ("hotelyard_bg", "一尊江月", "通过活动获取"))

    def test_loading_double_extension(self):
        e = g.table_entries({"collectCGs": [{"id": "Boss06", "pathCG": "collectCG_Boss06.png.png"}]}, [])[0]
        self.assertEqual(e["bundles"], ["images_novel_background_collectcg_boss06.ab"])

    def test_home_background_falls_back_to_shared_home_bundles(self):
        self.assertEqual(self.items[("home", "1001")]["bundles"][1:], ["images_home.ab", "images_hometheme.ab"])

    def test_loading_without_path_is_skipped(self):
        self.assertNotIn(("loading", "CG61099"), self.items)

    def test_home_background(self):
        e = self.items[("home", "1001")]
        self.assertEqual(e["bundles"][0], "ui_ui_home_bj_di.ab")
        self.assertEqual((e["tex"], e["name_cn"]), ("ui_home_bj_di", "博行派遣"))

    def test_postcard_keeps_poem_line_breaks(self):
        e = self.items[("postcard", "1")]
        self.assertEqual((e["place_cn"], e["note_cn"]), ("寒山寺", "枫桥依旧，佳话成诗，\n静行听钟声。"))
        self.assertIn("images_travelcards.ab", e["bundles"])

    def test_kv_needs_its_named_texture(self):
        e = self.items[("kv", "3401")]
        self.assertEqual((e["tex"], e["single"]), ("kv3401", False))  # animated KVs only hold 2048² part sheets

    def test_one_loading_item_per_image_prefers_collectible_row(self):
        rows = {"collectCGs": [
            {"id": "CG61035", "pathCG": "collectCG_1021.png", "collectOFF": 1, "headPathCG": "", "sourceTipLanText": "不开启"},
            {"id": "HD10302", "pathCG": "collectCG_1021.png", "collectOFF": 0, "headPathCG": "h.png", "sourceTipLanText": "活动"},
            {"id": "HD10902", "pathCG": "CollectCG_1021.png", "collectOFF": 0, "headPathCG": "h.png", "sourceTipLanText": "别的"}]}
        items = g.table_entries(rows, [])
        self.assertEqual([(e["id"], e["note_cn"]) for e in items], [("HD10302", "活动")])

    def test_kv_labels_only_numeric_codes(self):
        self.assertEqual(self.items[("kv", "3401")]["name_cn"], "3.4上")
        self.assertEqual(self.items[("kv", "huazhao")]["name_cn"], "")
        self.assertEqual(g.kv_label("3402"), "3.4下")
        self.assertEqual(g.kv_label("19.2"), "")


def make_lore(root: Path):
    (root / "Events/e001").mkdir(parents=True)
    (root / "Main_Story/m001").mkdir(parents=True)
    (root / "INDEX.tsv").write_text(
        "SECTION\tCATEGORY\tGROUP_NAME_CN\tSCRIPT_ID\tTITLE_CN\tLINES\tFILE\n"
        "Events\te001\t花朝昔时\te001002\t第二幕\t1\tEvents/e001/e001002.txt\n"
        "Events\te001\t花朝昔时\te001001\t第一幕\t1\tEvents/e001/e001001.txt\n"
        "Main_Story\tm001\t来古 (原洛阳)\tm001001\t序\t1\tMain_Story/m001/m001001.txt\n", encoding="utf-8")
    (root / "Events/e001/e001001.txt").write_text("〔背景 cg_1_3.1〕〔背景 bg_room〕", encoding="utf-8")
    (root / "Events/e001/e001002.txt").write_text("〔背景 cg_1_3.1〕〔背景 CG_1_3_1〕", encoding="utf-8")  # same image, other spelling
    (root / "Main_Story/m001/m001001.txt").write_text("〔背景 cg_0_1sex〕", encoding="utf-8")


class StoryEntriesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        make_lore(Path(self.tmp.name))
        self.list = g.story_entries(Path(self.tmp.name))
        self.items = {e["id"]: e for e in self.list}

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_cg_backgrounds_and_first_script_wins(self):
        self.assertEqual(set(self.items), {"cg_1_3_1", "cg_0_1sexf", "cg_0_1sexm"})
        e = self.items["cg_1_3_1"]
        self.assertEqual((e["script_id"], e["group_cn"], e["section"]), ("e001001", "花朝昔时", "Events"))
        self.assertEqual(e["bundles"], ["images_novel_background_cg_1_3_1.ab"])

    def test_spellings_of_one_image_are_one_item(self):
        self.assertEqual(len(self.list), len(self.items))

    def test_sex_variants_are_two_items(self):
        self.assertEqual(self.items["cg_0_1sexf"]["category"], "m001")
        self.assertEqual(self.items["cg_0_1sexm"]["bundles"], ["images_novel_background_cg_0_1sexm.ab"])


class FakeTex:
    def __init__(self, name, w, h):
        self.m_Name, self.m_Width, self.m_Height = name, w, h


class PickTextureTest(unittest.TestCase):
    def test_exact_name_wins_case_insensitive(self):
        objs = [FakeTex("big_other", 4000, 4000), FakeTex("UI_LX_LXWL_MXP7", 800, 500)]
        self.assertEqual(g.pick_texture(objs, "ui_lx_lxwl_mxp7").m_Name, "UI_LX_LXWL_MXP7")

    def test_single_image_bundle_falls_back_to_largest(self):
        objs = [FakeTex("a", 10, 10), FakeTex("b", 1920, 1080)]
        self.assertEqual(g.pick_texture(objs, "hotelyard_bg", single=True).m_Name, "b")

    def test_shared_bundle_without_name_is_none(self):
        self.assertIsNone(g.pick_texture([FakeTex("x", 10, 10)], "ui_lx_lxwl_mxp7"))


class BrokenImage:
    m_Name, m_Width, m_Height = "kv3401", 1680, 720

    @property
    def image(self):
        raise ValueError("sprite atlas is in another bundle")


class ExportTest(unittest.TestCase):
    def test_bad_image_is_listed_not_fatal(self):
        class Obj:
            class type:
                name = "Texture2D"
            def read(self): return BrokenImage()
        class Env:
            objects = [Obj()]
        class Ctx:
            snap = Path("r9999")
            ab = {"ui_kv3401.ab": {}}
            def rows(self, t): return []
            def prefetch(self, names): pass
            def env(self, b): return Env()
        with tempfile.TemporaryDirectory() as d:
            lore = Path(d) / "lore"; make_lore(lore)
            doc = g.export_gallery(Ctx(), lore, Path(d) / "out")
            self.assertIsNone(next(i for i in doc["items"] if i["kind"] == "kv")["png"])
            self.assertIn("kv\t3401\tui_kv3401.ab: sprite atlas", (Path(d) / "out" / "_THIEU.txt").read_text(encoding="utf-8"))


class SquareSheet:  # kv002, kvhh…: the named texture is a 2048² sheet of animation parts, not the art
    m_Name, m_Width, m_Height = "kv3401", 2048, 2048
    class image:
        width = height = 2048
        def save(f): raise AssertionError("part sheet must not be saved")


class KvSheetTest(unittest.TestCase):
    def test_square_kv_is_a_part_sheet_not_saved(self):
        class Obj:
            class type:
                name = "Texture2D"
            def read(self): return SquareSheet()
        class Env:
            objects = [Obj()]
        class Ctx:
            snap = Path("r9999")
            ab = {"ui_kv3401.ab": {}}
            def rows(self, t): return []
            def prefetch(self, names): pass
            def env(self, b): return Env()
        with tempfile.TemporaryDirectory() as d:
            lore = Path(d) / "lore"; make_lore(lore)
            doc = g.export_gallery(Ctx(), lore, Path(d) / "out")
            self.assertIsNone(next(i for i in doc["items"] if i["kind"] == "kv")["png"])
            self.assertIn("kv\t3401\tui_kv3401.ab: KV động", (Path(d) / "out" / "_THIEU.txt").read_text(encoding="utf-8"))


class DryRunTest(unittest.TestCase):
    def test_dry_run_writes_index_without_png(self):
        class Ctx:
            snap = Path("r9999")
            ab = {"images_novel_background_hotelyard_bg.ab": {}}
            def rows(self, t): return ROWS.get(t, [])
        with tempfile.TemporaryDirectory() as d:
            lore = Path(d) / "lore"; make_lore(lore)
            doc = g.export_gallery(Ctx(), lore, Path(d) / "out", dry=True)
            self.assertEqual(doc["version"], "r9999")
            self.assertTrue(all(i["png"] is None for i in doc["items"]))
            self.assertTrue((Path(d) / "out" / "INDEX.json").is_file())
            thieu = (Path(d) / "out" / "_THIEU.txt").read_text(encoding="utf-8")
            self.assertIn("home\t1001\tkhông có trong index game", thieu)
            self.assertNotIn("CG61001", thieu)


if __name__ == "__main__":
    unittest.main()
