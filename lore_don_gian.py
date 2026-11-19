"""物华弥新 剧情文本导出（精简版）/ Trích lore (bản đơn giản)

只需要 novel.ab 一个文件，不需要密钥、不需要数据表。
Chỉ cần đúng file novel.ab, không cần key AES hay bảng dữ liệu.

    pip install UnityPy lz4
    python lore_don_gian.py <novel.ab> <输出文件夹>

novel.ab 在手机/模拟器里是 /data/user/0/com.cipaishe.laigu/files/bundles/d638d193eb29ac87c0950ab36b80e0e8.ab
（文件名 = md5("novel")）。每个剧情脚本输出一个 txt，按首字母分文件夹：M 主线, E 活动, B 支线, C 个人剧情。
"""
import struct
import sys
from pathlib import Path

import lz4.block
import UnityPy

UnityPy.config.FALLBACK_UNITY_VERSION = "2018.4.36f1"   # 内部版本字符串是坏的


# ---- 1. bundle: blocks info 前 73 字节 XOR，密钥 = 第 2 个字节
def decrypt(d: bytes) -> bytes:
    p = d.index(b"\0", d.index(b"\0", 12) + 1) + 1
    size, ci, ui, flags = struct.unpack_from(">qIII", d, p)
    p += 20
    if struct.unpack_from(">I", d, 8)[0] >= 7:
        p = (p + 15) & ~15
    off = size - ci if flags & 0x80 else p
    try:
        lz4.block.decompress(d[off:off + ci], uncompressed_size=ui)
        return d                                   # 没加密
    except Exception:
        pass
    b = bytearray(d)
    key = b[off + 1]
    for i in range(min(73, len(b) - off)):
        b[off + i] ^= key
    return bytes(b)


# ---- 2. Odin Serializer 二进制 (Sirenix BinaryDataReader)
_FIXED = {15: 1, 16: 1, 17: 1, 18: 1, 19: 2, 20: 2, 21: 2, 22: 2, 23: 4, 24: 4, 25: 4, 26: 4,
          27: 8, 28: 8, 29: 8, 30: 8, 31: 4, 32: 4, 33: 8, 34: 8, 35: 16, 36: 16, 37: 2, 38: 2,
          41: 16, 42: 16, 43: 1, 44: 1}


class Odin:
    def __init__(self, b):
        self.b, self.p, self.types = b, 0, {}

    def u8(self):
        self.p += 1
        return self.b[self.p - 1]

    def i32(self):
        self.p += 4
        return struct.unpack_from("<i", self.b, self.p - 4)[0]

    def s(self):
        wide, n = self.u8(), self.i32()
        w = 2 if wide else 1
        v = self.b[self.p:self.p + n * w].decode("utf-16-le" if wide else "latin-1")
        self.p += n * w
        return v

    def typ(self):
        t = self.u8()
        if t == 0x2F:
            i, n = self.i32(), self.s()
            self.types[i] = n
        elif t == 0x30:
            self.i32()
        elif t != 0x2E:
            raise ValueError(f"type entry {t:#x}")

    def node(self, is_array=False):
        out = [] if is_array else {}
        while self.p < len(self.b):
            e = self.u8()
            if e in (5, 7, 49):                    # end of node / array / stream
                return out
            named = e in (1, 3, 9, 11, 13, 50) or (15 <= e <= 45 and e % 2 == 1)
            name = self.s() if named else None
            if e in (1, 2, 3, 4):                  # 引用/值 node
                self.typ()
                if e in (1, 2):
                    self.i32()
                v = self.node()
            elif e == 6:                           # array
                self.p += 8
                v = self.node(True)
            elif e == 8:                           # primitive array
                n, w = self.i32(), self.i32()
                v = None
                self.p += n * w
            elif e in (9, 10, 11, 12):
                v = self.i32()
            elif e in (13, 14):
                self.p += 16
                v = None
            elif e in (39, 40, 50, 51):            # string
                v = self.s()
            elif e in (45, 46):                    # null
                v = None
            elif e in _FIXED:
                w = _FIXED[e]
                raw = self.b[self.p:self.p + w]
                self.p += w
                v = bool(raw[0]) if e in (43, 44) else int.from_bytes(raw, "little", signed=True) if w <= 8 else None
            else:
                raise ValueError(f"entry {e} at {self.p - 1}")
            if is_array:
                out.append(v)
            else:
                out[name if name is not None else f"_{len(out)}"] = v
        return out


def odin(raw: bytes):
    """raw = MonoBehaviour 原始字节: 28 字节头 → m_Name(4 字节对齐) → SerializedFormat → 长度 + Odin 数据"""
    p = 28
    n = struct.unpack_from("<i", raw, p)[0]
    p = (p + 4 + n + 3) & ~3
    p += 4
    ln = struct.unpack_from("<i", raw, p)[0]
    return Odin(raw[p + 4:p + 4 + ln]).node()


def names(raw: bytes) -> dict:
    """NovelNameText（普通 Unity 序列化）: 说话人 ID → 中文名"""
    p = raw.find(b"\x02\x00\x00\x00N1\x00\x00") - 4
    cnt = struct.unpack_from("<i", raw, p)[0]
    p += 4
    out = {}
    for _ in range(cnt):
        vals = []
        for _ in range(4):
            n = struct.unpack_from("<i", raw, p)[0]
            vals.append(raw[p + 4:p + 4 + n].decode("utf-8"))
            p = (p + 4 + n + 3) & ~3
        out[vals[0]] = vals[1]
    return out


# ---- 3. 导出
def main(src, out):
    env = UnityPy.load(decrypt(Path(src).read_bytes()))
    lan, node, spk = {}, {}, {}
    for o in env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        x = o.read(check_read=False)
        sc = x.m_Script.read().m_Name
        if sc == "NovelLanAsset":                  # 台词: Key → CN
            lan[x.m_Name] = {r["Key"]: r.get("CN", "") for r in odin(o.get_raw_data())["LansList"]["_0"] if r}
        elif sc == "NovelNodeAsset":               # 剧情节点（顺序、说话人、背景、选项）
            node[x.m_Name] = odin(o.get_raw_data())["NodesList"]["_0"]
        elif x.m_Name == "NovelNameText":
            spk = names(o.get_raw_data())

    def who(sp):
        if sp in ("", "null", None):
            return "旁白"
        return "玩家" if sp.lower() == "player" else spk.get(sp, sp)

    out = Path(out)
    for sid, nodes in sorted(node.items()):
        L = lan.get(sid, {})
        lines, bg = [], None
        for n in filter(None, nodes):
            b = n.get("BackgroundData") or {}
            if b.get("AssetName") and b["AssetName"] != bg:
                bg = b["AssetName"]
                lines.append(f"〔背景 {bg}〕")
            for v in (n.get("SubtitleData") or {}).values():
                if isinstance(v, str) and L.get(v):
                    lines.append(f"【字幕】{L[v]}")
            dd = n.get("DialogData")
            if dd:
                lines.append(f"{who(dd.get('Speaker'))}：{L.get(dd.get('Content'), '')}")
            for opt in (((n.get("OptionData") or {}).get("Options") or {}).get("_0") or []):
                k, _, i = (opt.get("OptionKey") or "").rpartition("_")
                parts = L.get(k, "").split("_")
                if k in L:
                    lines.append(f"  ▸ 选项：{parts[int(i)] if i.isdigit() and int(i) < len(parts) else L[k]}")
        if not lines:
            continue
        f = out / sid[0].upper() / f"{sid.lower()}.txt"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(node)} 个脚本 -> {out}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
