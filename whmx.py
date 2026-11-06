#!/usr/bin/env python3
"""WHMX tool: lấy ảnh / audio / video / spine / lore / CG / Đông Cốc / bản cập nhật của 物华弥新
thẳng từ game trong MuMu qua adb. Cách dùng: xem README.txt hoặc `python whmx.py -h`."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import shutil
import string
import struct
import subprocess
import sys
import wave
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAPS = HERE / "PhienBan"          # one folder per game version: index + MasterData + text + novel.ab
OUT = HERE / "Xuat"                 # default output
TMP = HERE / "_tam"                 # pulled bundles, deleted after use
NAMES_FILE = HERE / "ten_nhan_vat.json"

# Keys from CryptoTool.cctor of the client (unchanged from the bili build to com.cipaishe.laigu 3.4.9).
AES_KEY = bytes.fromhex("202011092152e1d5d3dab5eedcdf02de")
UNITY_VERSION = "2018.4.36f1"
XOR_RANGE = 73
SNAP_FILES = ("data.dat", "data_config", "lang_config", "novel.ab")
MIN_FREE = 2 * 1024**3
BATCH = 40

GROUPS = {"character": "NhanVat", "images": "HinhAnh", "uiatlas": "UI_Atlas",
          "spriteatlas": "SpriteAtlas", "font": "Font", "ui": "UI", "scenes": "BoiCanh_Scene",
          "weapon": "VuKhi", "audio": "Audio", "guobao": "QuocBao", "rhythmgame": "RhythmGame"}
BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def say(*a) -> None:
    print(*a, flush=True)


# ---------------------------------------------------------------- crypto / formats

def aes_dec(data: bytes) -> bytes:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    return unpad(AES.new(AES_KEY, AES.MODE_ECB).decrypt(data), 16)


def load_index(path: Path) -> dict:
    raw = path.read_bytes()
    return json.loads(raw if raw[:1] in (b"{", b"[") else aes_dec(raw))


def _unityfs(d: bytes):
    """(blocks-info offset, csize, usize, data offset, compression) of a UnityFS header, else None."""
    if not d.startswith(b"UnityFS\x00") or len(d) < 50:
        return None
    try:
        p = d.index(b"\0", d.index(b"\0", 12) + 1) + 1
    except ValueError:
        return None
    size, ci, ui, flags = struct.unpack_from(">qIII", d, p)
    p += 20
    if struct.unpack_from(">I", d, 8)[0] >= 7:
        p = (p + 15) & ~15
    boff, doff = (size - ci, p) if flags & 0x80 else (p, p + ci)
    return boff, ci, ui, doff, flags & 0x3F


def _blocks_ok(d: bytes, h) -> bool:
    import lz4.block
    boff, ci, ui, doff, comp = h
    if comp not in (2, 3):
        return True
    try:
        u = lz4.block.decompress(d[boff:boff + ci], uncompressed_size=ui)
        us, cs, bf = struct.unpack_from(">IIH", u, 20)
        if bf & 0x3F in (2, 3):
            return len(lz4.block.decompress(d[doff:doff + cs], uncompressed_size=us)) == us
        return len(u) == ui
    except Exception:
        return False


def decrypt_ab(d: bytes) -> bytes:
    """Undo the XOR the client puts over the first 73 bytes of the UnityFS blocks info (key = 2nd byte)."""
    h = _unityfs(d)
    if h is None or _blocks_ok(d, h):
        return d
    b = bytearray(d)
    off = h[0]
    key = b[off + 1]
    for i in range(min(XOR_RANGE, len(b) - off)):
        b[off + i] ^= key
    if not _blocks_ok(bytes(b), h):
        raise ValueError("không giải được bundle (game đổi kiểu mã hoá?)")
    return bytes(b)


def fix_video(data: bytes, entry: dict) -> bytes:
    """VideoList .ab -> mp4: write entry Size (u32be) back at entry Position.
    Covers zeroed mvhd size (32, 108), blanked 'mvhd' name (32, 0x6D766864), zeroed mdat size (40, n)."""
    pos, val = int(entry["Position"]), int(entry["Size"])
    if data[4:8] != b"ftyp" or pos + 4 > len(data):
        raise ValueError("không phải mp4 của game")
    return data[:pos] + struct.pack(">I", val) + data[pos + 4:]


def parse_cfc(data: bytes) -> list[tuple[str, bytes]]:
    """data_config: u32be name length + name + u32be size + AES(gzip(json))."""
    out, p = [], 0
    while p + 8 <= len(data):
        nl = int.from_bytes(data[p:p + 4], "big")
        if not 0 < nl <= 256:
            break
        name = data[p + 4:p + 4 + nl].decode("utf-8")
        p += 4 + nl
        sz = int.from_bytes(data[p:p + 4], "big")
        out.append((name, data[p + 4:p + 4 + sz]))
        p += 4 + sz
    return out


def parse_lang(data: bytes) -> dict[str, str]:
    """lang_config (after AES + gzip): i16be key count, (u8 len + key)*, i32be text len + utf8 text."""
    out, p, n = {}, 0, len(data)
    while p + 2 <= n:
        cnt = int.from_bytes(data[p:p + 2], "big", signed=True)
        p += 2
        if cnt < 1:
            break
        keys = []
        for _ in range(cnt):
            kl = data[p]
            keys.append(data[p + 1:p + 1 + kl].decode("utf-8"))
            p += 1 + kl
        cl = int.from_bytes(data[p:p + 4], "big", signed=True)
        p += 4
        if cl < 0 or p + cl > n:
            break
        text = data[p:p + cl].decode("utf-8")
        p += cl
        for k in keys:
            out[k] = text
    return out


_BRACKET = re.compile(r"^\[([^\[\]{}]*)\]$")
_INT = re.compile(r"^-?\d+$")
_FLOAT = re.compile(r"^-?\d+\.\d+$")


def deserialize(v):
    """Strings holding JSON or '[a;b;c]' become real lists/dicts."""
    if isinstance(v, dict):
        return {k: deserialize(x) for k, x in v.items()}
    if isinstance(v, list):
        return [deserialize(x) for x in v]
    if not isinstance(v, str) or not v.strip():
        return v
    t = v.strip()
    if (t[0] == "{" and t[-1] == "}") or (t[0] == "[" and ('"' in t or t[:2] in ("[{", "[["))):
        try:
            return deserialize(json.loads(t))
        except json.JSONDecodeError:
            pass
    m = _BRACKET.fullmatch(t)
    if m:
        return [int(x) if _INT.fullmatch(x) else float(x) if _FLOAT.fullmatch(x) else x
                for x in (s.strip() for s in m.group(1).split(";"))] if m.group(1) else []
    return v


def resolve_lang(v, lang: dict[str, str]):
    """Keep every field; next to a field whose value is a text key add <field>Text with the text."""
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            out[k] = resolve_lang(x, lang)
            if isinstance(x, str) and x in lang:
                out[f"{k}Text"] = lang[x]
        return out
    if isinstance(v, list):
        return [resolve_lang(x, lang) for x in v]
    return v


def md5_file(p: Path) -> str:
    h = hashlib.md5()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- adb

def find_adb(given: str = "") -> str:
    if given:
        return given
    if shutil.which("adb"):
        return shutil.which("adb")
    for drive in string.ascii_uppercase[2:8]:
        for base in (f"{drive}:/Program Files/Netease", f"{drive}:/Program Files (x86)/Netease", f"{drive}:/Netease"):
            hits = sorted(Path(base).glob("MuMu*/**/adb.exe")) if Path(base).is_dir() else []
            if hits:
                return str(hits[0])
    raise SystemExit("Không tìm thấy adb.exe. Cài MuMu, hoặc chạy kèm --adb \"đường\\dẫn\\adb.exe\"")


class Device:
    def __init__(self, adb: str, serial: str, pkg: str):
        self.adb, self.serial, self.pkg = adb, serial, pkg
        self.files = f"/data/user/0/{pkg}/files"

    def run(self, *a: str) -> subprocess.CompletedProcess:
        return subprocess.run([self.adb, "-s", self.serial, *a], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")

    def sh(self, cmd: str) -> str:
        return self.run("shell", cmd).stdout

    def pull(self, remotes: list[str], dst: Path) -> None:
        dst.mkdir(parents=True, exist_ok=True)
        for i in range(0, len(remotes), BATCH):
            self.run("pull", *remotes[i:i + BATCH], str(dst))

    def updating(self) -> bool:
        return bool(self.sh(f"ls {self.files}/temps 2>/dev/null").strip())


def adb_devices(adb: str) -> list[str]:
    out = subprocess.run([adb, "devices"], capture_output=True, text=True).stdout
    return [p[0] for p in (ln.split() for ln in out.splitlines()[1:]) if len(p) == 2 and p[1] == "device"]


def connect(args) -> Device:
    adb = find_adb(args.adb)
    devs = adb_devices(adb)
    if not devs:  # MuMu 12+ listens here; older builds use 7555
        for port in ("127.0.0.1:16384", "127.0.0.1:7555"):
            subprocess.run([adb, "connect", port], capture_output=True)
        devs = adb_devices(adb)
    if not devs:
        raise SystemExit("adb không thấy giả lập nào. Mở MuMu (bật ADB trong cài đặt MuMu) rồi chạy lại.")
    serial = args.device or next((d for d in devs if d.startswith("emulator-")), devs[0])
    subprocess.run([adb, "-s", serial, "root"], capture_output=True)
    subprocess.run([adb, "-s", serial, "wait-for-device"], capture_output=True, timeout=60)
    pkg = args.package
    if not pkg:
        d = Device(adb, serial, "")
        cands = re.findall(r"package:(\S*cipaishe\S*)", d.sh("pm list packages"))
        pkg = next((c for c in cands if d.sh(f"ls /data/user/0/{c}/files/data.dat 2>/dev/null").strip()), "")
        if not pkg:
            raise SystemExit(f"Không thấy game đã tải dữ liệu (package tìm được: {cands or 'không có'}). "
                             "Mở game một lần cho tải xong update rồi chạy lại.")
    if "root" not in subprocess.run([adb, "-s", serial, "shell", "id"], capture_output=True, text=True).stdout:
        raise SystemExit("adb chưa có quyền root. Trong MuMu: Cài đặt > Khác > bật Root, khởi động lại MuMu.")
    return Device(adb, serial, pkg)


# ---------------------------------------------------------------- version snapshots

def snapshots() -> list[Path]:
    """Complete snapshots, oldest first."""
    found = [(json.loads((p / "info.json").read_text(encoding="utf-8"))["created"], p)
             for p in SNAPS.glob("*") if (p / "info.json").is_file()]
    return [p for _, p in sorted(found)]


def snapshot(dev: Device) -> Path:
    """Pull index + MasterData + text + novel.ab; reuse the snapshot when nothing changed."""
    if dev.updating():
        raise SystemExit("Game đang tải update (thư mục temps còn file). Đợi game tải xong rồi chạy lại.")
    new = TMP / "snap"
    shutil.rmtree(new, ignore_errors=True)
    dev.pull([f"{dev.files}/{f}" for f in SNAP_FILES[:3]], new)
    idx = load_index(new / "data.dat")
    novel = next(x for x in idx["ABList"] if x["Name"] == "novel.ab")
    dev.pull([f"{dev.files}/bundles/{novel['MD5Name']}"], new)
    (new / novel["MD5Name"]).replace(new / "novel.ab")
    md5 = {f: md5_file(new / f) for f in SNAP_FILES}
    if md5["novel.ab"] != novel["FileMD5"].lower():
        raise SystemExit("novel.ab trên máy không khớp index: game chưa cập nhật xong?")
    for p in snapshots():
        if json.loads((p / "info.json").read_text(encoding="utf-8"))["md5"] == md5:
            shutil.rmtree(new)
            return p
    name, i = idx["Version"], 1
    while (SNAPS / name / "info.json").exists():
        i += 1
        name = f"{idx['Version']}_{i}"
    dst = SNAPS / name
    shutil.rmtree(dst, ignore_errors=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(new), str(dst))
    say(f"Bản mới {name}: giải mã MasterData + text…")
    lang = parse_lang(gzip.decompress(aes_dec((dst / "lang_config").read_bytes())))
    (dst / "lang.json").write_text(json.dumps(lang, ensure_ascii=False, indent=0), encoding="utf-8")
    md = dst / "masterdata"
    md.mkdir()
    tables = parse_cfc((dst / "data_config").read_bytes())
    for tname, payload in tables:
        obj = resolve_lang(deserialize(json.loads(gzip.decompress(aes_dec(payload)))), lang)
        (md / f"{tname}.json").write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    info = {"version": idx["Version"], "created": datetime.now().isoformat(timespec="seconds"),
            "package": dev.pkg, "bundles": len(idx["ABList"]), "videos": len(idx["VideoList"]),
            "tables": len(tables), "texts": len(lang), "md5": md5}
    (dst / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    say(f"Đã lưu {dst}  ({len(idx['ABList'])} bundle, {len(tables)} bảng, {len(lang)} dòng text)")
    return dst


class Ctx:
    """One game version: its index / MasterData / text, plus bundle access through the device."""

    def __init__(self, dev: Device | None, snap: Path):
        self.dev, self.snap = dev, snap
        self.index = load_index(snap / "data.dat")
        self.ab = {x["Name"]: x for x in self.index["ABList"]}
        self._lang = None
        self.vi = json.loads(NAMES_FILE.read_text(encoding="utf-8")) if NAMES_FILE.is_file() else {}

    @property
    def lang(self) -> dict[str, str]:
        if self._lang is None:
            self._lang = json.loads((self.snap / "lang.json").read_text(encoding="utf-8"))
        return self._lang

    def table(self, t: str):
        p = self.snap / "masterdata" / f"{t}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}

    def rows(self, t: str) -> list:
        d = self.table(t)
        return list(d.values()) if isinstance(d, dict) else d

    def prefetch(self, logicals: list[str]) -> None:
        todo = [self.ab[n]["MD5Name"] for n in logicals if n in self.ab and not (TMP / "b" / self.ab[n]["MD5Name"]).exists()]
        if todo and self.dev:
            self.dev.pull([f"{self.dev.files}/bundles/{m}" for m in todo], TMP / "b")

    def bundle(self, logical: str) -> bytes:
        e = self.ab[logical]
        p = self.snap / "novel.ab" if logical == "novel.ab" else TMP / "b" / e["MD5Name"]
        if not p.exists():
            self.prefetch([logical])
        data = p.read_bytes() if p.exists() else b""
        if hashlib.md5(data).hexdigest() != e["FileMD5"].lower():
            raise RuntimeError(f"{logical}: bundle trên máy không khớp index (game chưa cập nhật xong?)")
        return decrypt_ab(data)

    def env(self, logical: str):
        import UnityPy
        UnityPy.config.FALLBACK_UNITY_VERSION = UNITY_VERSION
        return UnityPy.load(self.bundle(logical))

    def textures(self, logical: str) -> dict:
        if logical not in self.ab:
            return {}
        return {x.m_Name.lower(): x for x in (o.read() for o in self.env(logical).objects if o.type.name == "Texture2D")}


# ---------------------------------------------------------------- image / audio export (worker processes)

_names: dict[str, str] = {}


def _init_worker(names: dict[str, str]) -> None:
    global _names
    _names = {k.lower(): BAD.sub("", v) for k, v in names.items() if v}


def map_dir(container: str) -> Path:
    """assets/_bundleresources/images/itemicons/x.png -> HinhAnh/itemicons"""
    p = container.lower()
    cn = p.startswith("assets/cn/")
    parts = p.split("_bundleresources/", 1)[-1].split("/")[:-1] if "_bundleresources/" in p else ["khac", *p.split("/")[:-1]]
    head = GROUPS.get(parts[0], "Khac")
    rest = parts[1:] if head != "Khac" else parts
    if head == "NhanVat" and rest:
        cid, base = rest[0], rest[0][:5]  # a0001001 = skin 001 of a0001
        rest = [f"{base.upper()}_{_names[base]}" if _names.get(base) else base.upper(),
                *([cid.upper()] if cid != base else []), *rest[1:]]
    return Path("CoChu_" + head if cn else head, *rest)


def _unique(path: Path, used: set) -> Path:
    stem, suffix, i = path.with_suffix(""), path.suffix, 1
    while str(path).lower() in used or path.exists():  # ponytail: never overwrite; a re-export gets _2
        i += 1
        path = Path(f"{stem}_{i}{suffix}")
    used.add(str(path).lower())
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _want(target: Path, out_p: Path, h: str, cpath, keep, old, hashes: dict) -> bool:
    """Record the asset hash; say whether to write it. old = hashes of the previous bundle (compare content),
    keep = container paths new in this bundle (used when the old content is unknown), both None = everything."""
    key = n = str(target.relative_to(out_p)).replace("\\", "/").lower()
    i = 1
    while key in hashes:
        i += 1
        key = f"{n}#{i}"
    hashes[key] = h
    if old is not None:
        return old.get(key) != h
    return keep is None or cpath in keep


def _img_hash(img) -> str:
    return hashlib.md5(img.tobytes() + f"{img.mode}{img.size}".encode()).hexdigest()[:16]


def export_images(src: str, logical: str, paths: list[str], out: str, keep=None, old=None) -> tuple:
    import UnityPy
    UnityPy.config.FALLBACK_UNITY_VERSION = UNITY_VERSION
    env = UnityPy.load(decrypt_ab(Path(src).read_bytes()))
    out_p = Path(out)
    head, _, tail = logical.removesuffix(".ab").partition("_")
    fallback = out_p / (map_dir(paths[0]) if paths else Path(GROUPS.get(head, "Khac"), tail or head))
    atlases = [p for p in paths if p.endswith(".spriteatlas")]
    by_pid = {obj.path_id: path for path, obj in env.container.items()}
    sprite_atlas: dict[int, str] = {}
    sprites, textures = [], {}
    for obj in env.objects:
        t = obj.type.name
        if t == "SpriteAtlas" and obj.path_id in by_pid:
            try:
                for pp in obj.read().m_PackedSprites:
                    sprite_atlas[pp.path_id] = by_pid[obj.path_id]
            except Exception:
                pass
        elif t == "Sprite":
            sprites.append(obj)
        elif t == "Texture2D":
            textures[obj.path_id] = obj
    count = size = 0
    used: set = set()
    errs: list[str] = []
    per_tex: dict[int, int] = {}
    sprite_data = []
    hashes: dict[str, str] = {}
    for obj in sprites:
        try:
            s = obj.read()
            tex = getattr(getattr(s, "m_RD", None), "texture", None)
            tid = tex.path_id if tex is not None else 0
            per_tex[tid] = per_tex.get(tid, 0) + 1
            sprite_data.append((obj, s, tid))
        except Exception as e:
            errs.append(f"sprite {obj.path_id}: {e}")
    skip_tex = set(textures) if atlases else set()  # atlas sheets: the cut sprites are what players see
    for obj, s, tid in sprite_data:
        tex_name = ""
        if tid in textures:
            try:
                tex_name = textures[tid].peek_name()
            except Exception:
                pass
        if not atlases and per_tex.get(tid, 0) == 1 and s.m_Name.lower() == tex_name.lower():
            continue  # same pixels as the Texture2D export
        apath = sprite_atlas.get(obj.path_id) or (atlases[0] if atlases else "")
        if apath:
            folder = out_p / map_dir(apath) / Path(apath).stem
        elif tid in by_pid:
            folder = out_p / map_dir(by_pid[tid]) / (Path(by_pid[tid]).stem + "_cat")
        else:
            folder = fallback
        try:
            img = s.image
            target = folder / f"{BAD.sub('_', s.m_Name)}.png"
            if img is not None and img.width and img.height and \
                    _want(target, out_p, _img_hash(img), apath or by_pid.get(tid), keep, old, hashes):
                p = _unique(target, used)
                img.save(p, "PNG")
                size += p.stat().st_size
                count += 1
        except Exception as e:
            errs.append(f"sprite {s.m_Name}: {e}")
    for pid, obj in textures.items():
        if pid in skip_tex:
            continue
        try:
            tex = obj.read()
            img = tex.image
            if img is None or not img.width:
                continue
            folder = out_p / map_dir(by_pid[pid]) if pid in by_pid else fallback
            target = folder / f"{BAD.sub('_', tex.m_Name)}.png"
            if not _want(target, out_p, _img_hash(img), by_pid.get(pid), keep, old, hashes):
                continue
            p = _unique(target, used)
            img.save(p, "PNG")
            size += p.stat().st_size
            count += 1
        except Exception as e:
            errs.append(f"tex {pid}: {e}")
    return count, size, "; ".join(errs), hashes


def export_audio(src: str, logical: str, paths: list[str], out: str, keep=None, old=None) -> tuple:
    """AudioClip -> mp3 (lameenc). Folder: Audio/<group> or Audio/NhanVat/<char>/audio."""
    import UnityPy
    import lameenc
    UnityPy.config.FALLBACK_UNITY_VERSION = UNITY_VERSION
    env = UnityPy.load(decrypt_ab(Path(src).read_bytes()))
    by_pid = {obj.path_id: path for path, obj in env.container.items()}
    count = size = 0
    used: set = set()
    errs: list[str] = []
    hashes: dict[str, str] = {}
    for obj in env.objects:
        if obj.type.name != "AudioClip":
            continue
        try:
            clip = obj.read()
            rel = map_dir(by_pid[obj.path_id]) if obj.path_id in by_pid else Path("Khac", logical.removesuffix(".ab"))
            folder = Path(out) / (rel if rel.parts[0] == "Audio" else Path("Audio", rel))
            for name, wav in clip.samples.items():
                with wave.open(io.BytesIO(wav)) as w:
                    ch, rate, width, pcm = w.getnchannels(), w.getframerate(), w.getsampwidth(), w.readframes(w.getnframes())
                path = folder / f"{BAD.sub('_', Path(name).stem)}.mp3"
                if not _want(path, Path(out), hashlib.md5(wav).hexdigest()[:16], by_pid.get(obj.path_id),
                             keep, old, hashes):
                    continue
                if width == 2:
                    enc = lameenc.Encoder()
                    enc.set_bit_rate(160)
                    enc.set_in_sample_rate(rate)
                    enc.set_channels(ch)
                    enc.set_quality(2)
                    blob = enc.encode(pcm) + enc.flush()
                else:  # ponytail: non-16-bit PCM is kept as wav rather than resampled
                    path, blob = path.with_suffix(".wav"), wav
                path = _unique(path, used)
                path.write_bytes(blob)
                size += len(blob)
                count += 1
        except Exception as e:
            errs.append(f"audio {obj.path_id}: {e}")
    return count, size, "; ".join(errs), hashes


def _hash_file(audio: bool) -> Path:
    return SNAPS / ("_hash_audio.json" if audio else "_hash_anh.json")


def load_hashes(audio: bool) -> dict:
    """bundle FileMD5 -> {asset: content hash}; filled by every images/audio/update run."""
    f = _hash_file(audio)
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}


def update_filters(old_index: dict, entries: list[dict], audio: bool) -> tuple[dict, list[str]]:
    """Changed bundle -> (keep, old) for export_*: compare each asset with the old bundle's hashes when the
    old bundle was exported before, else export only container paths that are new. Also returns the
    changed bundles that can't be compared at all (no old hashes, no new path)."""
    cache = load_hashes(audio)
    oe = {x["Name"]: x for x in old_index["ABList"]}
    filters, blind = {}, []
    for x in entries:
        o = oe.get(x["Name"])
        if o is None:
            continue  # new bundle: everything
        hs = cache.get(o["FileMD5"].lower())
        if hs is not None:
            filters[x["Name"]] = (None, hs)
        else:
            keep = set(x["PathList"]) - set(o["PathList"])
            filters[x["Name"]] = (keep, None)
            if not keep:
                blind.append(x["Name"])
    return filters, blind


def run_export(ctx: Ctx, entries: list[dict], out: Path, audio: bool, workers: int, filters=None) -> int:
    """Pull bundles in batches, export in worker processes, delete each bundle after. Resumable.
    filters: bundle name -> (keep, old), see update_filters."""
    out.mkdir(parents=True, exist_ok=True)
    filters = filters or {}
    cache = load_hashes(audio)
    prog = out / ("_xong_audio.txt" if audio else "_xong_anh.txt")
    done = set(prog.read_text(encoding="utf-8").split()) if prog.exists() else set()
    todo = [x for x in entries if x["FileMD5"].lower() not in done]
    say(f"{len(entries)} bundle, đã xong {len(entries) - len(todo)}, còn {len(todo)}")
    if not todo:
        return 0
    tmp = TMP / "x"
    fn = export_audio if audio else export_images
    total = 0
    with ProcessPoolExecutor(workers, initializer=_init_worker, initargs=(ctx.vi,)) as pool, \
            prog.open("a", encoding="utf-8") as pf, (out / "_loi.txt").open("a", encoding="utf-8") as ef:
        for i in range(0, len(todo), BATCH):
            if shutil.disk_usage(out).free < MIN_FREE:
                say("DỪNG: ổ đĩa còn dưới 2 GB")
                return 2
            batch = todo[i:i + BATCH]
            ctx.dev.pull([f"{ctx.dev.files}/bundles/{x['MD5Name']}" for x in batch], tmp)
            futs = {}
            for x in batch:
                src = tmp / x["MD5Name"]
                if src.is_file():
                    futs[pool.submit(fn, str(src), x["Name"], x["PathList"], str(out),
                                     *filters.get(x["Name"], (None, None)))] = (x, src)
                else:
                    ef.write(f"THIẾU {x['MD5Name']} {x['Name']}\n")
            for f in as_completed(futs):
                x, src = futs[f]
                try:
                    c, _s, err, hs = f.result()
                    cache[x["FileMD5"].lower()] = hs
                    total += c
                    if err:
                        ef.write(f"{x['Name']}: {err}\n")
                    pf.write(x["FileMD5"].lower() + "\n")
                except Exception as e:
                    ef.write(f"LỖI {x['Name']}: {e}\n")
                src.unlink(missing_ok=True)
            pf.flush()
            ef.flush()
            SNAPS.mkdir(exist_ok=True)
            _hash_file(audio).write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")
            say(f"[{min(i + BATCH, len(todo))}/{len(todo)}] {total} file {'audio' if audio else 'ảnh'}")
    return 0


def image_entries(ctx: Ctx, only: str = "") -> list[dict]:
    return [x for x in ctx.index["ABList"] if not x["Name"].startswith("audio_") and only in x["Name"]]


def audio_entries(ctx: Ctx, only: str = "") -> list[dict]:
    return [x for x in ctx.index["ABList"]
            if any(p.endswith((".mp3", ".wav")) for p in x["PathList"]) and only in x["Name"]]


# ---------------------------------------------------------------- video / spine

def video_names(ctx: Ctx) -> dict[str, str]:
    """Video .ab names are md5(stem); recover stems by hashing words from MasterData + lua."""
    word = re.compile(r"[A-Za-z0-9_\-]{3,80}")
    words: set[str] = set()
    for p in (ctx.snap / "masterdata").glob("*.json"):
        words |= set(word.findall(p.read_text(encoding="utf-8")))
    for obj in ctx.env("lua.ab").objects:
        if obj.type.name == "TextAsset":
            s = obj.read().m_Script
            words |= set(word.findall(s if isinstance(s, str) else s.decode("utf-8", "ignore")))
    return {hashlib.md5(w.encode()).hexdigest() + ".ab": w for w in words}


def export_videos(ctx: Ctx, entries: list[dict], folder: Path) -> int:
    names = video_names(ctx)
    folder.mkdir(parents=True, exist_ok=True)
    ok = 0
    for v in entries:
        dst = folder / f"{names.get(v['Name'], v['Name'][:-3])}.mp4"
        src = TMP / "v" / v["Name"]
        if not dst.is_file():
            ctx.dev.pull([f"{ctx.dev.files}/bundles/{v['Name']}"], src.parent)
            try:
                data = src.read_bytes()
                if hashlib.md5(data).hexdigest() != v["FileMd5"].lower():  # bili build had 9 such, still playable
                    say(f"CHÚ Ý {v['Name']}: MD5 khác index, vẫn xuất")
                dst.write_bytes(fix_video(data, v))
            except Exception as e:
                say(f"LỖI {v['Name']}: {e}")
                continue
            finally:
                src.unlink(missing_ok=True)
        ok += 1
        say(f"[{ok}/{len(entries)}] {dst.name}")
    return 0 if ok == len(entries) else 1


def export_spine(ctx: Ctx, only: str, out: Path) -> int:
    """character_drawingspine_*: skel/atlas + textures, resized to the page size the .atlas declares."""
    from PIL import Image
    names = [n for n in ctx.ab if n.startswith("character_drawingspine_") and only in n]
    say(f"{len(names)} bộ spine")
    for i in range(0, len(names), BATCH):
        ctx.prefetch(names[i:i + BATCH])
        for b in names[i:i + BATCH]:
            sid = b.removesuffix(".ab").split("_")[-1].upper()
            vi = ctx.vi.get(sid[:5], "")
            d = out / BAD.sub("", f"{sid}_{vi}" if vi else sid)
            d.mkdir(parents=True, exist_ok=True)
            try:
                for o in ctx.env(b).objects:
                    if o.type.name == "TextAsset":
                        x = o.read()
                        data = x.m_Script.encode("utf-8", "surrogateescape") if isinstance(x.m_Script, str) else bytes(x.m_Script)
                        (d / (x.m_Name if "." in x.m_Name else x.m_Name + ".bytes")).write_bytes(data)
                    elif o.type.name == "Texture2D":
                        x = o.read()
                        x.image.save(d / f"{BAD.sub('_', x.m_Name)}.png")
                for atlas in d.glob("*.atlas*"):
                    text = atlas.read_text(encoding="utf-8", errors="replace")
                    for page, w, h in re.findall(r"^(\S+\.png)\s*\nsize:\s*(\d+),\s*(\d+)", text, re.M):
                        p = d / page
                        if p.exists() and Image.open(p).size != (int(w), int(h)):
                            Image.open(p).resize((int(w), int(h)), Image.LANCZOS).save(p)
            except Exception as e:
                say(f"LỖI {b}: {e}")
            (TMP / "b" / ctx.ab[b]["MD5Name"]).unlink(missing_ok=True)
        say(f"[{min(i + BATCH, len(names))}/{len(names)}]")
    return 0


CHIBI = re.compile(r"^character_[a-z]\d{7}\.ab$")  # battle chibi of one skin: character_s0130003.ab


def chibi_bundles(ctx: Ctx, only: str = "") -> list[str]:
    return sorted(n for n in ctx.ab if CHIBI.match(n) and only.lower() in n)


def export_chibi(ctx: Ctx, names: list[str], out: Path, flat: bool = False) -> int:
    """Battle chibi (spine) -> one PNG per skin, first frame of idle. out/<char>/<SKIN>_chibi.png
    (flat=True: out/<char>/<SKIN>.png)."""
    import whmx_chibi
    from PIL import Image
    say(f"{len(names)} chibi")
    n = errs = 0
    for i in range(0, len(names), BATCH):
        ctx.prefetch(names[i:i + BATCH])
        for b in names[i:i + BATCH]:
            sid = b.removesuffix(".ab").split("_")[-1].upper()
            vi = ctx.vi.get(sid[:5], "")
            try:
                skels, atlases, pages = {}, {}, {}
                for o in ctx.env(b).objects:
                    if o.type.name == "TextAsset":
                        x = o.read()
                        data = x.m_Script.encode("utf-8", "surrogateescape") if isinstance(x.m_Script, str) else bytes(x.m_Script)
                        if x.m_Name.endswith(".skel"):
                            skels[x.m_Name.removesuffix(".skel")] = data
                        elif ".atlas" in x.m_Name:
                            atlases[x.m_Name.split(".atlas")[0]] = data.decode("utf-8", "replace")
                    elif o.type.name == "Texture2D":
                        x = o.read()
                        pages[x.m_Name + ".png"] = x.image
            except Exception as e:
                errs += 1
                say(f"LỖI chibi {b}: {e}")
                skels = {}
            d = out / BAD.sub("", f"{sid[:5]}_{vi}" if vi else sid[:5])
            for stem, skel in sorted(skels.items()):  # the skin itself + summons/pets (A0084001_shizi, ..._cat01)
                if stem not in atlases:
                    continue
                main = stem.upper() == sid
                try:
                    img = whmx_chibi.render(skel, atlases[stem], pages)
                    d.mkdir(parents=True, exist_ok=True)
                    img.save(d / (f"{sid}.png" if flat and main else f"{sid if main else stem}{'' if flat else '_chibi'}.png"))
                    n += 1
                except Exception as e:
                    errs += 1
                    say(f"LỖI chibi {b} {stem}: {e}")
            (TMP / "b" / ctx.ab[b]["MD5Name"]).unlink(missing_ok=True)
        say(f"[{min(i + BATCH, len(names))}/{len(names)}] {n} ảnh chibi")
    return 1 if errs else 0


# ---------------------------------------------------------------- update (diff two versions)

def _keyed(d) -> dict:
    if isinstance(d, dict):
        return d
    key = next((k for k in ("id", "ID", "Id", "key", "Key") if d and isinstance(d[0], dict) and k in d[0]), None)
    return {str(r.get(key)) if key else str(i): r for i, r in enumerate(d)}


def diff_masterdata(old: Ctx, new: Ctx, out: Path) -> list[str]:
    """Per changed table: rows added / removed / changed (changed fields only, [old, new])."""
    out.mkdir(parents=True, exist_ok=True)
    summary = []
    tables = sorted({p.stem for p in (new.snap / "masterdata").glob("*.json")} | {p.stem for p in (old.snap / "masterdata").glob("*.json")})
    for t in tables:
        a, b = _keyed(old.table(t)), _keyed(new.table(t))
        add = {k: b[k] for k in b.keys() - a.keys()}
        rem = sorted(a.keys() - b.keys())
        mod = {}
        for k in a.keys() & b.keys():
            if a[k] != b[k]:
                ra, rb = a[k], b[k]
                mod[k] = ({f: [ra.get(f), rb.get(f)] for f in ra.keys() | rb.keys() if ra.get(f) != rb.get(f)}
                          if isinstance(ra, dict) and isinstance(rb, dict) else [ra, rb])
        if add or rem or mod:
            (out / f"{t}.json").write_text(json.dumps({"them": add, "sua": mod, "xoa": rem}, ensure_ascii=False, indent=1), encoding="utf-8")
            summary.append(f"  {t}: +{len(add)} mới, {len(mod)} sửa, -{len(rem)} xoá")
    return summary


def diff_text(old: Ctx, new: Ctx, out: Path) -> tuple[int, int]:
    a, b = old.lang, new.lang
    add = sorted(b.keys() - a.keys())
    mod = sorted(k for k in a.keys() & b.keys() if a[k] != b[k])
    lines = [f"== DÒNG TEXT MỚI ({len(add)}) ==", ""] + [f"[{k}]\n{b[k]}\n" for k in add]
    lines += ["", f"== DÒNG TEXT ĐÃ SỬA ({len(mod)}) ==", ""] + [f"[{k}]\n  cũ: {a[k]}\n  mới: {b[k]}\n" for k in mod]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(add), len(mod)


def diff_dirs(old_dir: Path, new_dir: Path, out: Path) -> tuple[int, int]:
    """Copy files of new_dir that are new or differ from old_dir."""
    added = changed = 0
    for f in new_dir.rglob("*.txt"):
        rel = f.relative_to(new_dir)
        o = old_dir / rel
        if o.exists() and o.read_bytes() == f.read_bytes():
            continue
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, out / rel)
        added, changed = (added + 1, changed) if not o.exists() else (added, changed + 1)
    return added, changed


def cmd_update(ctx: Ctx, args) -> int:
    import whmx_story as story
    snaps = snapshots()
    if args.tu:
        old_snap = SNAPS / args.tu
        if not (old_snap / "info.json").is_file():
            raise SystemExit(f"Không có bản {args.tu}. Các bản đang có: {[p.name for p in snaps]}")
    else:
        older = [p for p in snaps if p != ctx.snap]
        older = older[:snaps.index(ctx.snap)] if ctx.snap in snaps else older
        if not older:
            say(f"Mới có một bản ({ctx.snap.name}), chưa có gì để so. Khi game ra update: mở game cho tải xong, "
                "tắt game, rồi chạy lại `python whmx.py update`.")
            return 0
        old_snap = older[-1]
    old = Ctx(None, old_snap)
    out = Path(args.out) / f"CapNhat_{old_snap.name}_den_{ctx.snap.name}"
    out.mkdir(parents=True, exist_ok=True)
    say(f"So {old_snap.name} → {ctx.snap.name}, kết quả ở {out}")

    a = {x["Name"]: x["FileMD5"] for x in old.index["ABList"]}
    added = [x for x in ctx.index["ABList"] if x["Name"] not in a]
    changed = [x for x in ctx.index["ABList"] if x["Name"] in a and a[x["Name"]] != x["FileMD5"]]
    ov = {v["Name"]: v["FileMd5"] for v in old.index["VideoList"]}
    vids = [v for v in ctx.index["VideoList"] if ov.get(v["Name"]) != v["FileMd5"]]
    (out / "DanhSach_bundle.txt").write_text(
        "\n".join([f"== MỚI ({len(added)}) =="] + [x["Name"] for x in added]
                  + ["", f"== ĐÃ SỬA ({len(changed)}) =="] + [x["Name"] for x in changed]
                  + ["", f"== VIDEO MỚI/SỬA ({len(vids)}) =="] + [v["Name"] for v in vids]) + "\n", encoding="utf-8")

    say("So MasterData + text…")
    md_sum = diff_masterdata(old, ctx, out / "MasterData_thaydoi")
    t_add, t_mod = diff_text(old, ctx, out / "Text_thaydoi.txt")
    new_chars = [r for r in ctx.rows("characterTable") if str(r.get("id")) not in {str(o.get("id")) for o in old.rows("characterTable")}]

    say("Dựng lore của hai bản để so…")
    lo, ln = TMP / "lore_cu", TMP / "lore_moi"
    for d in (lo, ln):
        shutil.rmtree(d, ignore_errors=True)
    story.build_lore(old, lo)
    story.build_lore(ctx, ln)
    l_add, l_mod = diff_dirs(lo, ln, out / "Lore_thaydoi")
    shutil.rmtree(lo, ignore_errors=True)
    shutil.rmtree(ln, ignore_errors=True)
    og = story.huanzhang_groups(old)
    hz = {k for k, v in story.huanzhang_groups(ctx).items() if og.get(k) != v}
    if hz:
        story.build_hoanchuong(ctx, out / "HoanChuong_thaydoi", hz)

    changed_all = added + changed
    imgs = [x for x in changed_all if not x["Name"].startswith("audio_")]
    auds = [x for x in changed_all if any(p.endswith((".mp3", ".wav")) for p in x["PathList"])]
    fi, bi = update_filters(old.index, imgs, False)
    fa, ba = update_filters(old.index, auds, True)
    blind = sorted(set(bi) | set(ba))
    with (out / "DanhSach_bundle.txt").open("a", encoding="utf-8") as f:
        f.write(f"\n== ĐÃ SỬA NHƯNG KHÔNG SO ĐƯỢC TỪNG ẢNH/ÂM, KHÔNG XUẤT ({len(blind)}) ==\n"
                "(bản cũ chưa từng chạy images/audio nên không biết ảnh nào đổi, và bundle không có đường dẫn mới)\n"
                + "".join(n + "\n" for n in blind))
    say(f"Xuất ảnh mới/đổi trong {len(imgs)} bundle…")
    run_export(ctx, imgs, out / "Anh", False, args.workers, fi)
    say(f"Xuất audio mới/đổi trong {len(auds)} bundle…")
    run_export(ctx, auds, out, True, args.workers, fa)
    if vids:
        export_videos(ctx, vids, out / "Video")
    chibis = [x["Name"] for x in changed_all if CHIBI.match(x["Name"])]
    if chibis:
        export_chibi(ctx, chibis, out / "Anh" / "NhanVat")

    summary = [f"CẬP NHẬT {old_snap.name} → {ctx.snap.name}  ({datetime.now():%Y-%m-%d %H:%M})", "",
               f"Bundle: {len(added)} mới, {len(changed)} sửa (danh sách: DanhSach_bundle.txt)",
               f"Video mới/sửa: {len(vids)}",
               f"Bundle sửa không so được từng ảnh/âm (không xuất): {len(blind)}",
               f"Text: {t_add} dòng mới, {t_mod} dòng sửa (Text_thaydoi.txt)",
               f"Lore: {l_add} file mới, {l_mod} file sửa (Lore_thaydoi/)",
               f"Hoán chương mới/sửa: {len(hz)} (HoanChuong_thaydoi/)",
               f"Chibi mới/sửa: {len(chibis)} (Anh/NhanVat/<nhân vật>/<skin>_chibi.png)", "",
               f"Nhân vật mới ({len(new_chars)}):"]
    summary += [f"  {r.get('id')} {r.get('namelanText') or r.get('name') or ''}" for r in new_chars]
    summary += ["", f"MasterData đổi ({len(md_sum)} bảng, chi tiết MasterData_thaydoi/<bảng>.json):", *md_sum]
    (out / "TOM_TAT.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    say("\n".join(summary[:8]))
    return 0


# ---------------------------------------------------------------- doctor / CLI

def cmd_doctor(args) -> int:
    ok = True

    def check(cond: bool, good: str, bad: str) -> bool:
        nonlocal ok
        say(("[OK]   " if cond else "[LỖI] ") + (good if cond else bad))
        ok &= cond
        return cond

    check(sys.version_info >= (3, 10), f"Python {sys.version.split()[0]}", "Cần Python 3.10 trở lên")
    for mod, pipname in (("UnityPy", "UnityPy"), ("lz4", "lz4"), ("Crypto", "pycryptodome"), ("PIL", "Pillow"), ("lameenc", "lameenc")):
        try:
            __import__(mod)
            check(True, f"thư viện {pipname}", "")
        except ImportError:
            check(False, "", f"thiếu thư viện {pipname}: chạy  pip install -r requirements.txt")
    try:
        dev = connect(args)
    except SystemExit as e:
        check(False, "", str(e))
        return 1
    check(True, f"adb: {dev.adb}", "")
    check(True, f"thiết bị {dev.serial} (root)", "")
    check(True, f"game: {dev.pkg}", "")
    n = dev.sh(f"ls {dev.files}/bundles | wc -l").strip()
    check(n.isdigit() and int(n) > 1000, f"{n} bundle trong máy", f"mới có {n} bundle: mở game cho tải hết dữ liệu")
    check(not dev.updating(), "game không đang tải update", "game đang tải update: đợi xong rồi chạy lại")
    if dev.sh(f"pidof {dev.pkg}").strip():
        say("[CHÚ Ý] game đang mở. Nên tắt game khi chạy tool để dữ liệu không đổi giữa chừng.")
    free = shutil.disk_usage(HERE).free / 2**30
    check(free > 20, f"ổ đĩa còn {free:.0f} GB", f"ổ đĩa chỉ còn {free:.0f} GB (lệnh all cần khoảng 20 GB)")
    check(NAMES_FILE.is_file(), "có ten_nhan_vat.json (tên Việt)", "thiếu ten_nhan_vat.json: thư mục sẽ chỉ có mã nhân vật")
    say("Mọi thứ sẵn sàng." if ok else "Sửa các dòng [LỖI] ở trên rồi chạy lại doctor.")
    return 0 if ok else 1


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="WHMX: lấy dữ liệu 物华弥新 từ MuMu. Xem README.txt.")
    ap.add_argument("--adb", default="", help="đường dẫn adb.exe (mặc định tự tìm)")
    ap.add_argument("--device", default="", help="mã thiết bị adb (mặc định tự chọn)")
    ap.add_argument("--package", default="", help="package game (mặc định tự tìm)")
    ap.add_argument("--out", default=str(OUT), help=f"thư mục xuất (mặc định {OUT})")
    ap.add_argument("--workers", type=int, default=4, help="số tiến trình xuất ảnh/audio song song")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="kiểm tra Python, thư viện, adb, root, game")
    sub.add_parser("sync", help="lưu index + MasterData + text của bản hiện tại vào PhienBan/")
    p = sub.add_parser("images", help="xuất toàn bộ ảnh")
    p.add_argument("--only", default="", help="chỉ bundle có tên chứa chuỗi này, vd images_itemicons")
    p.add_argument("--nhanvat", action="store_true", help="chỉ ảnh nhân vật (character_*)")
    p = sub.add_parser("audio", help="xuất toàn bộ audio ra mp3")
    p.add_argument("--only", default="")
    sub.add_parser("video", help="xuất video mp4 (đã sửa)")
    p = sub.add_parser("spine", help="xuất spine nhân vật (skel/atlas/png)")
    p.add_argument("--only", default="", help="vd a0142")
    sub.add_parser("lore", help="toàn bộ cốt truyện, phòng trà, thoại trận (txt)")
    sub.add_parser("cg", help="ảnh CG theo từng truyện")
    sub.add_parser("dongcoc", help="冬谷归档: tạp chí, hồ sơ, hồi tưởng, ngoại cần, ảnh kỷ niệm (html + ảnh)")
    sub.add_parser("hoanchuong", help="焕彰: truyện, hiệu ứng, chỉ số, nguyên liệu của mọi hoán chương (txt)")
    p = sub.add_parser("chibi", help="ghép chibi chiến đấu của mọi skin ra ảnh png nền trong suốt")
    p.add_argument("--only", default="", help="vd s0130 (một nhân vật) hoặc s0130003 (một skin)")
    p = sub.add_parser("update", help="so bản hiện tại với bản trước: ảnh/audio/video/text/lore mới")
    p.add_argument("--tu", default="", help="so từ bản này (tên thư mục trong PhienBan/), mặc định bản trước đó")
    sub.add_parser("all", help="images + audio + video + spine + chibi + lore + cg + dongcoc + hoanchuong")
    args = ap.parse_args()

    if args.cmd == "doctor":
        return cmd_doctor(args)
    import whmx_story as story
    dev = connect(args)
    ctx = Ctx(dev, snapshot(dev))
    out = Path(args.out)
    say(f"Bản game: {ctx.snap.name}  ({dev.pkg} trên {dev.serial})")
    cmds = ["images", "audio", "video", "spine", "chibi", "lore", "cg", "dongcoc", "hoanchuong"] if args.cmd == "all" else [args.cmd]
    rc = 0
    try:
        for c in cmds:
            say(f"===== {c} =====")
            if c == "images":
                only = "character_" if getattr(args, "nhanvat", False) else getattr(args, "only", "")
                rc |= run_export(ctx, image_entries(ctx, only), out / "Anh", False, args.workers)
            elif c == "audio":
                rc |= run_export(ctx, audio_entries(ctx, getattr(args, "only", "")), out, True, args.workers)
            elif c == "video":
                rc |= export_videos(ctx, ctx.index["VideoList"], out / "Video")
            elif c == "spine":
                rc |= export_spine(ctx, getattr(args, "only", ""), out / "Spine")
            elif c == "chibi":
                rc |= export_chibi(ctx, chibi_bundles(ctx, getattr(args, "only", "")), out / "Chibi", flat=True)
            elif c == "lore":
                story.build_lore(ctx, out / f"Lore_{ctx.snap.name}")
            elif c == "cg":
                lore = out / f"Lore_{ctx.snap.name}"
                if not (lore / "INDEX.tsv").is_file():
                    story.build_lore(ctx, lore)
                story.build_cg(ctx, lore, out / "CG_TheoTruyen")
            elif c == "dongcoc":
                story.build_dongcoc(ctx, out / "DongCoc_QuyDang")
            elif c == "hoanchuong":
                story.build_hoanchuong(ctx, out / f"HoanChuong_{ctx.snap.name}")
            elif c == "update":
                rc |= cmd_update(ctx, args)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    say("Xong." if not rc else "Xong, có lỗi: xem _loi.txt trong thư mục xuất.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
