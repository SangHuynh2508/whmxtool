"""Chibi (battle Spine 3.8 skeleton) -> one PNG of the setup pose. Pure Python: numpy + Pillow, no Spine runtime.
Reads the .skel binary up to the skins (animations are not needed for the setup pose), poses the bones,
then draws every region / mesh attachment in slot order with bilinear texture sampling."""
from __future__ import annotations

import math
import struct

import numpy as np
from PIL import Image


class _Reader:
    def __init__(self, b: bytes):
        self.b, self.p = b, 0

    def byte(self) -> int:
        self.p += 1
        return self.b[self.p - 1]

    def bool(self) -> bool:
        return self.byte() != 0

    def int(self) -> int:
        self.p += 4
        return struct.unpack_from(">I", self.b, self.p - 4)[0]

    def var(self) -> int:
        r = shift = 0
        for _ in range(5):
            b = self.byte()
            r |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                break
        r &= 0xFFFFFFFF
        return r - (1 << 32) if r & 0x80000000 else r  # Java int: draw-order offsets can be negative

    def str(self):
        n = self.var()
        if n == 0:
            return None
        self.p += n - 1
        return self.b[self.p - n + 1:self.p].decode("utf-8", "replace")

    def floats(self, n: int) -> list:
        self.p += 4 * n
        return list(struct.unpack_from(f">{n}f", self.b, self.p - 4 * n))

    def shorts(self) -> list:
        n = self.var()
        self.p += 2 * n
        return list(struct.unpack_from(f">{n}H", self.b, self.p - 2 * n))


def load_skel(data: bytes, anim: str = "idle") -> dict:
    """Spine 3.8 binary -> bones, slots, skins {name: {(slot, key): attachment}}, pose = `anim` at time 0."""
    r = _Reader(data)
    r.str()
    version = r.str() or ""
    if not version.startswith("3.8"):
        raise ValueError(f"spine {version}: chỉ hỗ trợ 3.8")
    r.floats(4)
    nonessential = r.bool()
    if nonessential:
        r.floats(1)
        r.str()
        r.str()
    strings = [r.str() for _ in range(r.var())]

    def ref():
        i = r.var()
        return None if i == 0 else strings[i - 1]

    bones = []
    for i in range(r.var()):
        name = r.str()
        parent = None if i == 0 else r.var()
        rot, x, y, sx, sy, shx, shy, _length = r.floats(8)
        mode = r.var()
        r.bool()  # skin required
        if nonessential:
            r.int()
        bones.append(dict(name=name, parent=parent, rot=rot, x=x, y=y, sx=sx, sy=sy, shx=shx, shy=shy, mode=mode))
    slots = []
    for _ in range(r.var()):
        name, bone, color = r.str(), r.var(), r.int()
        r.int()  # dark color
        slots.append(dict(name=name, bone=bone, color=color, att=ref(), blend=r.var()))
    for _ in range(r.var()):  # IK: ignored, setup pose already satisfies them
        r.str(); r.var(); r.bool(); [r.var() for _ in range(r.var())]; r.var(); r.floats(2); r.byte(); r.bool(); r.bool(); r.bool()
    for _ in range(r.var()):  # transform constraints
        r.str(); r.var(); r.bool(); [r.var() for _ in range(r.var())]; r.var(); r.bool(); r.bool(); r.floats(10)
    for _ in range(r.var()):  # path constraints
        r.str(); r.var(); r.bool(); [r.var() for _ in range(r.var())]; r.var(); r.var(); r.var(); r.var(); r.floats(5)

    def verts(n):
        if not r.bool():
            return ("plain", r.floats(n * 2))
        return ("weighted", [[(r.var(), *r.floats(3)) for _ in range(r.var())] for _ in range(n)])

    def attachment(slot, key):
        name = ref() or key
        t = r.byte()
        if t == 0:  # region
            path = ref() or name
            rot, x, y, sx, sy, w, h = r.floats(7)
            return dict(type="region", path=path, rot=rot, x=x, y=y, sx=sx, sy=sy, w=w, h=h, color=r.int())
        if t == 2:  # mesh
            path, color, n = ref() or name, r.int(), r.var()
            uvs, tris, v = r.floats(n * 2), r.shorts(), verts(n)
            r.var()  # hull
            if nonessential:
                r.shorts()
                r.floats(2)
            return dict(type="mesh", path=path, color=color, uvs=uvs, tris=tris, verts=v)
        if t == 3:  # linked mesh
            path, color, skin, parent = ref() or name, r.int(), ref(), ref()
            r.bool()
            if nonessential:
                r.floats(2)
            return dict(type="linked", path=path, color=color, skin=skin, parent=parent, slot=slot)
        if t in (1, 6):  # bounding box, clipping (ponytail: clipping masks are not applied)
            if t == 6:
                r.var()
            verts(r.var())
        elif t == 4:  # path
            r.bool(); r.bool()
            n = r.var()
            verts(n)
            r.floats(n // 3)
        elif t == 5:  # point
            r.floats(3)
        if nonessential:
            r.int()
        return None

    def skin(default: bool):
        if default:
            name = "default"
        else:
            name = ref()
            for _ in range(4):  # bones, ik, transform, path
                [r.var() for _ in range(r.var())]
        atts = {}
        n = r.var()
        for _ in range(n):
            si = r.var()
            for _ in range(r.var()):
                key = ref()
                a = attachment(si, key)
                if a:
                    atts[(si, key)] = a
        return name, atts, n

    name, atts, n = skin(True)
    skins, order = {name: atts}, [name] if n else []  # order = skin indices used by deform timelines
    for _ in range(r.var()):
        k, v, _n = skin(False)
        skins[k] = v
        order.append(k)
    for atts in skins.values():
        for k, a in list(atts.items()):
            if a["type"] == "linked":
                p = skins.get(a["skin"] or "default", {}).get((a["slot"], a["parent"]))
                if p and p["type"] == "mesh":
                    atts[k] = dict(p, path=a["path"], color=a["color"])
                else:
                    del atts[k]
    sk = dict(bones=bones, slots=slots, skins=skins, pose=None)
    try:
        sk["pose"] = _first_frame(r, ref, sk, order, anim)
    except (struct.error, IndexError):  # ponytail: odd animation data -> fall back to the setup pose
        pass
    return sk


def _first_frame(r: _Reader, ref, sk: dict, skin_order: list, anim: str):
    """Walk events + animations until `anim`; return its values at time 0 (missing: the first animation's)."""
    audio = []
    for _ in range(r.var()):  # events: name, int, float, string, audio path (+ volume, balance)
        ref(); r.var(); r.floats(1); r.str()
        audio.append(r.str() is not None)
        if audio[-1]:
            r.floats(2)
    curve = lambda: r.floats(4) if r.byte() == 2 else None
    fallback = None
    for _ in range(r.var()):
        name = r.str()
        pose = dict(bones={}, attach={}, color={}, deform={}, order=None)
        first = lambda t: t <= 0.0001
        for _ in range(r.var()):  # slot timelines
            si = r.var()
            for _ in range(r.var()):
                kind, frames = r.byte(), r.var()
                for f in range(frames):
                    t = r.floats(1)[0]
                    if kind == 0:
                        v = ref()
                        if f == 0 and first(t):
                            pose["attach"][si] = v
                        continue
                    c = r.int()
                    if kind == 2:
                        r.int()
                    if f == 0 and first(t):
                        pose["color"][si] = c
                    if f < frames - 1:
                        curve()
        for _ in range(r.var()):  # bone timelines
            bi = r.var()
            for _ in range(r.var()):
                kind, frames = r.byte(), r.var()
                for f in range(frames):
                    t, *v = r.floats(2 if kind == 0 else 3)
                    if f == 0 and first(t):
                        pose["bones"].setdefault(bi, {})[kind] = v
                    if f < frames - 1:
                        curve()
        for _ in range(r.var()):  # ik
            r.var()
            frames = r.var()
            for f in range(frames):
                r.floats(3); r.byte(); r.bool(); r.bool()
                if f < frames - 1:
                    curve()
        for _ in range(r.var()):  # transform constraints
            r.var()
            frames = r.var()
            for f in range(frames):
                r.floats(5)
                if f < frames - 1:
                    curve()
        for _ in range(r.var()):  # path constraints
            r.var()
            for _ in range(r.var()):
                kind, frames = r.byte(), r.var()
                for f in range(frames):
                    r.floats(3 if kind == 2 else 2)
                    if f < frames - 1:
                        curve()
        for _ in range(r.var()):  # deform
            skin = sk["skins"].get(skin_order[r.var()], {})
            for _ in range(r.var()):
                si = r.var()
                for _ in range(r.var()):
                    key = ref()
                    att = skin.get((si, key))
                    frames = r.var()
                    for f in range(frames):
                        t = r.floats(1)[0]
                        end = r.var()
                        d = None
                        if end:
                            start = r.var()
                            d = (start, r.floats(end))
                        if f == 0 and first(t) and att is not None:
                            pose["deform"][(si, key)] = d
                        if f < frames - 1:
                            curve()
        for f in range(r.var()):  # draw order
            t = r.floats(1)[0]
            offs = [(r.var(), r.var()) for _ in range(r.var())]
            if f == 0 and first(t):
                pose["order"] = offs
        for _ in range(r.var()):  # event keys
            r.floats(1)
            ei = r.var()
            r.var(); r.floats(1)
            if r.bool():
                r.str()
            if audio[ei]:
                r.floats(2)
        if name == anim:
            return pose
        fallback = fallback or pose
    return fallback


def load_atlas(text: str) -> dict:
    """Spine 3.8 .atlas -> region name -> dict(page, u, v, u2, v2, w, h, ow, oh, ox, oy, deg, pw, ph)."""
    regions, page, cur, size = {}, None, None, {}
    for line in text.splitlines():
        if not line.strip():
            page = None
            continue
        if page is None:
            page, cur = line.strip(), {}
            size[page] = cur
        elif ":" in line:
            k, v = line.split(":", 1)
            cur[k.strip()] = v.strip()
        else:
            cur = regions[line.strip()] = {"page": page}
    out = {}
    for name, d in regions.items():
        pw, ph = (int(t) for t in size[d["page"]]["size"].split(","))
        x, y = (int(t) for t in d["xy"].split(","))
        w, h = (int(t) for t in d["size"].split(","))
        ow, oh = (int(t) for t in d.get("orig", d["size"]).split(","))
        ox, oy = (int(t) for t in d.get("offset", "0,0").split(","))
        rot = d.get("rotate", "false")
        deg = 90 if rot == "true" else 0 if rot == "false" else int(rot)
        out[name] = dict(page=d["page"], pw=pw, ph=ph, w=w, h=h, ow=ow, oh=oh, ox=ox, oy=oy, deg=deg,
                         u=x / pw, v=y / ph, u2=(x + (h if deg == 90 else w)) / pw, v2=(y + (w if deg == 90 else h)) / ph)
    return out


def _world(bones: list) -> list:
    """Setup-pose world transforms (a, b, c, d, x, y) per bone, as Bone.updateWorldTransform in 3.8."""
    cos = lambda d: math.cos(math.radians(d))
    sin = lambda d: math.sin(math.radians(d))
    out = []
    for bd in bones:
        rot, sx, sy, shx, shy = bd["rot"], bd["sx"], bd["sy"], bd["shx"], bd["shy"]
        if bd["parent"] is None:
            ry = rot + 90 + shy
            out.append((cos(rot + shx) * sx, cos(ry) * sy, sin(rot + shx) * sx, sin(ry) * sy, bd["x"], bd["y"]))
            continue
        pa, pb, pc, pd, px, py = out[bd["parent"]]
        wx, wy = pa * bd["x"] + pb * bd["y"] + px, pc * bd["x"] + pd * bd["y"] + py
        mode = bd["mode"]
        if mode == 0:  # normal
            ry = rot + 90 + shy
            la, lb, lc, ld = cos(rot + shx) * sx, cos(ry) * sy, sin(rot + shx) * sx, sin(ry) * sy
            out.append((pa * la + pb * lc, pa * lb + pb * ld, pc * la + pd * lc, pc * lb + pd * ld, wx, wy))
        elif mode == 1:  # only translation
            ry = rot + 90 + shy
            out.append((cos(rot + shx) * sx, cos(ry) * sy, sin(rot + shx) * sx, sin(ry) * sy, wx, wy))
        elif mode == 2:  # no rotation or reflection
            s = pa * pa + pc * pc
            if s > 0.0001:
                s = abs(pa * pd - pb * pc) / s
                pb, pd = pc * s, pa * s
                prx = math.degrees(math.atan2(pc, pa))
            else:
                pa = pc = 0
                prx = 90 - math.degrees(math.atan2(pd, pb))
            rx, ry = rot + shx - prx, rot + shy - prx + 90
            la, lb, lc, ld = cos(rx) * sx, cos(ry) * sy, sin(rx) * sx, sin(ry) * sy
            out.append((pa * la - pb * lc, pa * lb - pb * ld, pc * la + pd * lc, pc * lb + pd * ld, wx, wy))
        else:  # 3 no scale, 4 no scale or reflection
            za, zc = pa * cos(rot) + pb * sin(rot), pc * cos(rot) + pd * sin(rot)
            s = math.hypot(za, zc)
            if s > 0.00001:
                za, zc = za / s, zc / s
            s = math.hypot(za, zc)
            if mode == 3 and (pa * pd - pb * pc < 0):
                s = -s
            r = math.pi / 2 + math.atan2(zc, za)
            zb, zd = math.cos(r) * s, math.sin(r) * s
            la, lb, lc, ld = cos(shx) * sx, cos(90 + shy) * sy, sin(shx) * sx, sin(90 + shy) * sy
            out.append((za * la + zb * lc, za * lb + zb * ld, zc * la + zd * lc, zc * lb + zd * ld, wx, wy))
    return out


def _rgba(c: int) -> np.ndarray:
    return np.array([(c >> 24) & 255, (c >> 16) & 255, (c >> 8) & 255, c & 255], np.float32) / 255


def _geometry(att: dict, reg: dict, bone, world: list, deform=None):
    """World vertices (n,2), texture uvs (n,2) in 0..1, triangles (m,3). deform = (start, offsets) from a timeline."""
    a, b, c, d, wx, wy = bone
    if att["type"] == "region":
        sx = att["w"] / reg["ow"] * att["sx"]
        sy = att["h"] / reg["oh"] * att["sy"]
        x1 = -att["w"] / 2 * att["sx"] + reg["ox"] * sx
        y1 = -att["h"] / 2 * att["sy"] + reg["oy"] * sy
        x2, y2 = x1 + reg["w"] * sx, y1 + reg["h"] * sy
        co, si = math.cos(math.radians(att["rot"])), math.sin(math.radians(att["rot"]))
        local = [(x1, y1), (x1, y2), (x2, y2), (x2, y1)]  # BL UL UR BR
        local = [(x * co - y * si + att["x"], x * si + y * co + att["y"]) for x, y in local]
        verts = np.array([(x * a + y * b + wx, x * c + y * d + wy) for x, y in local])
        u, v, u2, v2 = reg["u"], reg["v"], reg["u2"], reg["v2"]
        uvs = [(u2, v2), (u, v2), (u, v), (u2, v)] if reg["deg"] == 90 else [(u, v2), (u, v), (u2, v), (u2, v2)]
        return verts, np.array(uvs), np.array([(0, 1, 2), (2, 3, 0)])
    kind, vs = att["verts"]
    delta = None
    if deform:
        n = len(vs) * 2 if kind == "plain" else sum(len(i) for i in vs) * 2
        delta = np.zeros(n)
        delta[deform[0]:deform[0] + len(deform[1])] = deform[1][:max(0, n - deform[0])]
    if kind == "plain":
        p = np.array(vs).reshape(-1, 2) + (delta.reshape(-1, 2) if delta is not None else 0)
        verts = np.stack([p[:, 0] * a + p[:, 1] * b + wx, p[:, 0] * c + p[:, 1] * d + wy], 1)
    else:
        verts = np.zeros((len(vs), 2))
        k = 0
        for i, infl in enumerate(vs):
            for bi, x, y, w in infl:
                if delta is not None:
                    x, y = x + delta[k], y + delta[k + 1]
                k += 2
                ba, bb, bc, bd_, bx, by = world[bi]
                verts[i] += (w * (x * ba + y * bb + bx), w * (x * bc + y * bd_ + by))
    ruv = np.array(att["uvs"]).reshape(-1, 2)
    pw, ph = reg["pw"], reg["ph"]
    if reg["deg"] == 90:
        u = reg["u"] - (reg["oh"] - reg["oy"] - reg["h"]) / pw
        v = reg["v"] - (reg["ow"] - reg["ox"] - reg["w"]) / ph
        uvs = np.stack([u + ruv[:, 1] * reg["oh"] / pw, v + (1 - ruv[:, 0]) * reg["ow"] / ph], 1)
    else:
        u = reg["u"] - reg["ox"] / pw
        v = reg["v"] - (reg["oh"] - reg["oy"] - reg["h"]) / ph
        uvs = np.stack([u + ruv[:, 0] * reg["ow"] / pw, v + ruv[:, 1] * reg["oh"] / ph], 1)
    return verts, uvs, np.array(att["tris"]).reshape(-1, 3)


def render(skel: bytes, atlas_text: str, pages: dict[str, Image.Image], scale: float = 1.5, anim: str = "idle", pma: bool = False) -> Image.Image:
    """First frame of `anim` (setup pose if missing) -> RGBA image cropped to the character.
    pages: atlas page name -> texture (any size; resized to the page size the atlas declares)."""
    sk, atlas = load_skel(skel, anim), load_atlas(atlas_text)
    pose = sk["pose"] or dict(bones={}, attach={}, color={}, deform={}, order=None)
    bones = [dict(b) for b in sk["bones"]]
    for bi, tl in pose["bones"].items():
        b = bones[bi]
        if 0 in tl:
            b["rot"] += tl[0][0]
        if 1 in tl:
            b["x"] += tl[1][0]; b["y"] += tl[1][1]
        if 2 in tl:
            b["sx"] *= tl[2][0]; b["sy"] *= tl[2][1]
        if 3 in tl:
            b["shx"] += tl[3][0]; b["shy"] += tl[3][1]
    world = _world(bones)
    order = list(range(len(sk["slots"])))
    if pose["order"]:
        order, rest, cur = [-1] * len(order), [], 0
        for si, off in pose["order"]:
            while cur != si:
                rest.append(cur)
                cur += 1
            order[cur + off] = cur
            cur += 1
        rest += range(cur, len(order))
        for i in range(len(order) - 1, -1, -1):
            if order[i] == -1:
                order[i] = rest.pop()
    tex = {}
    for name, im in pages.items():
        reg = next((r for r in atlas.values() if r["page"] == name), None)
        if reg:
            im = im.convert("RGBA")
            if im.size != (reg["pw"], reg["ph"]):
                im = im.resize((reg["pw"], reg["ph"]), Image.LANCZOS)
            tex[name] = (np.asarray(im, np.float32) / 255, pma)  # game textures are straight alpha
    draws = []
    for si in order:
        slot = sk["slots"][si]
        key = pose["attach"].get(si, slot["att"])
        att = next((s[(si, key)] for s in sk["skins"].values() if (si, key) in s), None) if key else None
        reg = atlas.get(att["path"]) if att else None
        if reg is None or reg["page"] not in tex:
            continue
        v, uv, tris = _geometry(att, reg, world[slot["bone"]], world, pose["deform"].get((si, key)))
        color = _rgba(pose["color"].get(si, slot["color"])) * _rgba(att["color"])
        if color[3] > 0:
            draws.append((v, uv, tris, tex[reg["page"]], color, slot["blend"]))
    if not draws:
        raise ValueError("không có mảnh nào để vẽ")
    allv = np.concatenate([d[0] for d in draws])
    (x0, y0), (x1, y1) = allv.min(0), allv.max(0)
    m = 4
    W, H = int((x1 - x0) * scale) + 2 * m, int((y1 - y0) * scale) + 2 * m
    canvas = np.zeros((H, W, 4), np.float32)  # premultiplied
    for v, uv, tris, t, tint, blend in draws:
        p = np.stack([(v[:, 0] - x0) * scale + m, (y1 - v[:, 1]) * scale + m], 1)  # y up -> down
        t, is_pma = t
        th, tw = t.shape[:2]
        tp = uv * (tw, th) - 0.5
        layer_a = np.zeros((H, W), bool)  # each pixel once per attachment: no seams on shared edges
        for i0, i1, i2 in tris:
            P0, P1, P2 = p[i0], p[i1], p[i2]
            area = (P1[0] - P0[0]) * (P2[1] - P0[1]) - (P1[1] - P0[1]) * (P2[0] - P0[0])
            if abs(area) < 1e-6:
                continue
            bx0, by0 = max(int(min(P0[0], P1[0], P2[0])), 0), max(int(min(P0[1], P1[1], P2[1])), 0)
            bx1, by1 = min(int(max(P0[0], P1[0], P2[0])) + 1, W), min(int(max(P0[1], P1[1], P2[1])) + 1, H)
            if bx0 >= bx1 or by0 >= by1:
                continue
            ys, xs = np.mgrid[by0:by1, bx0:bx1] + 0.5
            w0 = ((P1[0] - xs) * (P2[1] - ys) - (P1[1] - ys) * (P2[0] - xs)) / area
            w1 = ((P2[0] - xs) * (P0[1] - ys) - (P2[1] - ys) * (P0[0] - xs)) / area
            w2 = 1 - w0 - w1
            inside = (w0 >= -1e-4) & (w1 >= -1e-4) & (w2 >= -1e-4) & ~layer_a[by0:by1, bx0:bx1]
            if not inside.any():
                continue
            layer_a[by0:by1, bx0:bx1] |= inside
            iy, ix = np.nonzero(inside)
            a0, a1, a2 = w0[inside], w1[inside], w2[inside]
            tx = a0 * tp[i0, 0] + a1 * tp[i1, 0] + a2 * tp[i2, 0]
            ty = a0 * tp[i0, 1] + a1 * tp[i1, 1] + a2 * tp[i2, 1]
            fx, fy = np.floor(tx), np.floor(ty)
            dx, dy = (tx - fx)[:, None], (ty - fy)[:, None]
            xa, ya = np.clip(fx.astype(int), 0, tw - 1), np.clip(fy.astype(int), 0, th - 1)
            xb, yb = np.clip(xa + 1, 0, tw - 1), np.clip(ya + 1, 0, th - 1)
            # bilinear on premultiplied texels so transparent neighbours don't bleed colour
            q = (lambda yy, xx: t[yy, xx]) if is_pma else                 (lambda yy, xx: np.concatenate([t[yy, xx, :3] * t[yy, xx, 3:], t[yy, xx, 3:]], 1))
            s = (q(ya, xa) * (1 - dx) * (1 - dy) + q(ya, xb) * dx * (1 - dy)
                 + q(yb, xa) * (1 - dx) * dy + q(yb, xb) * dx * dy) * tint
            gy, gx = iy + by0, ix + bx0
            dst = canvas[gy, gx]
            if blend == 1:  # additive
                dst[:, :3] += s[:, :3]
            else:  # normal (multiply/screen drawn as normal)
                dst = s + dst * (1 - s[:, 3:])
            canvas[gy, gx] = dst
    a = canvas[..., 3:]
    rgb = np.where(a > 0, canvas[..., :3] / np.maximum(a, 1e-6), 0)
    img = Image.fromarray((np.clip(np.concatenate([rgb, a], 2), 0, 1) * 255 + 0.5).astype(np.uint8), "RGBA")
    box = img.getchannel("A").point(lambda x: 255 if x > 2 else 0).getbbox()
    return img.crop((max(box[0] - m, 0), max(box[1] - m, 0), min(box[2] + m, W), min(box[3] + m, H))) if box else img
