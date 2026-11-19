"""Thư viện ảnh: which images the web gallery shows, by the game table that uses them (spec WhmxCalc 2026-11-17)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from whmx_story import cg_first_use, cg_variants

HOME_BUNDLES = ["images_home.ab", "images_hometheme.ab"]  # the base backgrounds have no bundle of their own
POSTCARD_BUNDLES = ["images_travelcards.ab", "spriteatlas_travel.ab", "ui_travelcardspanel.ab", "ui_travelletterpanel.ab"]


def kv_label(code: str) -> str:
    m = re.fullmatch(r"(\d)(\d)0([12])", code)
    return f"{m[1]}.{m[2]}{'上' if m[3] == '1' else '下'}" if m else ""


def _e(kind, id_, bundles, tex, name="", note="", place="", single=None):
    """single: a bundle made for this one image, so its largest picture may stand in when no name matches."""
    return {"kind": kind, "id": str(id_), "bundles": bundles, "tex": tex.lower(), "name_cn": name or "",
            "note_cn": note or "", "place_cn": place or "", "single": len(bundles) == 1 if single is None else single}


def _collectible(r: dict) -> bool:
    return not int(r.get("collectOFF") or 0) and bool(r.get("headPathCG"))


def table_entries(rows: dict[str, list[dict]], kv_bundles: list[str]) -> list[dict]:
    out, by_image = [], {}
    for r in rows.get("collectCGs", []):  # several rows can share one image: keep the one players collect
        stem = re.sub(r"(\.png)+$", "", r.get("pathCG") or "", flags=re.I)  # Boss06 is "….png.png"
        k = stem.lower()
        if stem and (k not in by_image or _collectible(r) and not _collectible(by_image[k][0])):
            by_image[k] = (r, stem)
    for r, stem in by_image.values():
        out.append(_e("loading", r["id"], [f"images_novel_background_{stem.lower()}.ab"], stem,
                      r.get("nameCGLanText"), r.get("sourceTipLanText")))
    for r in rows.get("BGSwitchMap", []):
        img = r.get("Image") or ""
        if img:
            out.append(_e("home", r["Id"], [f"ui_{img.lower()}.ab", *HOME_BUNDLES], img, r.get("NameLanText")))
    for r in rows.get("TravelPostcardMap", []):
        img = r.get("SetContent1") or ""
        if img:
            poem = (r.get("SetContent2LanText") or r.get("SetContent2") or "").replace(";", "\n")
            out.append(_e("postcard", r["Id"], POSTCARD_BUNDLES, img, note=poem,
                          place=r.get("SetContent4LanText") or r.get("SetContent4")))
    for b in kv_bundles:
        code = b[len("ui_kv"):-len(".ab")]
        out.append(_e("kv", code, [b], f"kv{code}", kv_label(code), single=False))  # animated KVs only hold part sheets
    return out


def story_entries(lore: Path) -> list[dict]:
    first, _ = cg_first_use(lore)
    out, seen = [], set()
    for name, r in first.items():  # first-use order, so a later spelling of the same image (CG_4_15, cg_4.15) is dropped
        for low, _suffix in cg_variants(name):
            if low in seen:
                continue
            seen.add(low)
            e = _e("story", low, [f"images_novel_background_{low}.ab"], low)
            e.update(section=r["SECTION"], category=r["CATEGORY"], group_cn=r["GROUP_NAME_CN"],
                     script_id=r["SCRIPT_ID"], title_cn=r["TITLE_CN"])
            out.append(e)
    return out


def pick_texture(objs: list, tex: str, single: bool = False):
    named = [o for o in objs if o.m_Name.lower() == tex.lower()]
    if named:
        return named[0]
    if single and objs:
        return max(objs, key=lambda o: o.m_Width * o.m_Height)
    return None


def _images(ctx, logical: str) -> list:
    out = []
    for o in ctx.env(logical).objects:
        if o.type.name in ("Texture2D", "Sprite"):
            x = o.read()
            if o.type.name == "Sprite":  # atlas sprite: size comes from its rect, .image is already cropped
                x.m_Width, x.m_Height = int(x.m_Rect.width), int(x.m_Rect.height)
            out.append(x)
    return out


def export_gallery(ctx, lore: Path, out: Path, dry: bool = False) -> dict:
    """Write <kind>/<id>.png + INDEX.json (+ _THIEU.txt). dry: list only, no bundle is opened."""
    kv = sorted(n for n in ctx.ab if re.fullmatch(r"ui_kv[^/]+\.ab", n))
    rows = {t: ctx.rows(t) for t in ("collectCGs", "BGSwitchMap", "TravelPostcardMap")}
    items, missing = table_entries(rows, kv) + story_entries(lore), []
    if not dry:
        ctx.prefetch(sorted({b for e in items for b in e["bundles"] if b in ctx.ab}))
    for e in items:
        e.update(png=None, w=None, h=None)
        have = [b for b in e["bundles"] if b in ctx.ab]
        if not have:
            missing.append(f"{e['kind']}\t{e['id']}\tkhông có trong index game")
            continue
        if dry:
            continue
        for b in have:
            try:
                t = pick_texture(_images(ctx, b), e["tex"], single=e["single"])
                img = t.image if t is not None else None
                if img is not None and e["kind"] == "kv" and img.width <= img.height:
                    raise ValueError("KV động, chỉ có tấm ghép mảnh")  # real KV art is 1680×720
            except Exception as ex:  # noqa: BLE001 - one bad bundle/image must not stop the export
                missing.append(f"{e['kind']}\t{e['id']}\t{b}: {ex}")
                img = None
            if img is not None:
                f = out / e["kind"] / f"{e['id']}.png"
                f.parent.mkdir(parents=True, exist_ok=True)
                img.save(f)
                e.update(png=f"{e['kind']}/{e['id']}.png", w=img.width, h=img.height)
                break
        else:
            missing.append(f"{e['kind']}\t{e['id']}\tkhông thấy ảnh {e['tex']}")
    doc = {"version": ctx.snap.name, "items": items}
    out.mkdir(parents=True, exist_ok=True)
    (out / "INDEX.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "_THIEU.txt").write_text("\n".join(missing) + "\n", encoding="utf-8")
    print(f"Gallery: {len(items)} mục, có ảnh {sum(1 for i in items if i['png'])}, thiếu {len(missing)} → {out}", flush=True)
    return doc
