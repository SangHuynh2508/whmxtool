"""Story side of the WHMX tool: lore text, story CG, 冬谷归档, 焕彰. All read through whmx.Ctx."""
from __future__ import annotations

import base64
import collections
import csv
import html
import io
import json
import re
import struct
from pathlib import Path

safe = lambda s: re.sub(r'[\\/:*?"<>|\x00-\x1f]+', '_', s).strip() or '_'
nat = lambda s: [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', s)]


# ---------------------------------------------------------------- Odin Serializer (binary) reader

_FIXED = {15: 1, 16: 1, 17: 1, 18: 1, 19: 2, 20: 2, 21: 2, 22: 2, 23: 4, 24: 4, 25: 4, 26: 4,
          27: 8, 28: 8, 29: 8, 30: 8, 31: 4, 32: 4, 33: 8, 34: 8, 35: 16, 36: 16, 37: 2, 38: 2,
          41: 16, 42: 16, 43: 1, 44: 1}
_FMT = {1: 'b', 2: 'h', 4: 'i', 8: 'q'}


class _Odin:
    def __init__(self, b):
        self.b, self.p, self.types = b, 0, {}

    def u8(self):
        v = self.b[self.p]; self.p += 1; return v

    def i32(self):
        v = struct.unpack_from('<i', self.b, self.p)[0]; self.p += 4; return v

    def s(self):
        wide = self.u8(); n = self.i32()
        if wide:
            v = self.b[self.p:self.p + 2 * n].decode('utf-16-le'); self.p += 2 * n
        else:
            v = self.b[self.p:self.p + n].decode('latin-1'); self.p += n
        return v

    def typ(self):
        t = self.u8()
        if t == 0x2F:
            i = self.i32(); n = self.s(); self.types[i] = n; return n.split(',')[0]
        if t == 0x30:
            return self.types.get(self.i32(), '?').split(',')[0]
        if t == 0x2E:
            return None
        raise ValueError(f'type entry {t:#x} at {self.p}')

    def node(self, is_array=False):
        out = [] if is_array else {}
        while self.p < len(self.b):
            e = self.u8()
            if e in (5, 7, 49):
                return out
            named = e in (1, 3, 9, 11, 13, 50) or (15 <= e <= 45 and e % 2 == 1)
            name = self.s() if named else None
            if e in (1, 2, 3, 4):
                t = self.typ()
                if e in (1, 2):
                    self.i32()
                v = self.node(); v['$t'] = t
            elif e == 6:
                self.p += 8; v = self.node(True)
            elif e == 8:
                n = self.i32(); w = self.i32(); v = self.b[self.p:self.p + n * w]; self.p += n * w
            elif e in (9, 10, 11, 12):
                v = ('ref', self.i32())
            elif e in (13, 14):
                self.p += 16; v = ('guid',)
            elif e in (39, 40, 50, 51):
                v = self.s()
            elif e in (45, 46):
                v = None
            elif e in _FIXED:
                w = _FIXED[e]; raw = self.b[self.p:self.p + w]; self.p += w
                if e in (43, 44): v = bool(raw[0])
                elif e in (31, 32): v = struct.unpack('<f', raw)[0]
                elif e in (33, 34): v = struct.unpack('<d', raw)[0]
                elif w in _FMT: v = struct.unpack('<' + _FMT[w], raw)[0]
                else: v = raw.hex()
            else:
                raise ValueError(f'entry {e} at {self.p - 1}')
            if is_array: out.append(v)
            else: out[name if name is not None else f'_{len(out)}'] = v
        return out


def parse_mono(raw):
    """raw = MonoBehaviour bytes; the Odin blob starts right after the padded m_Name."""
    p = 28
    n = struct.unpack_from('<i', raw, p)[0]; p += 4 + n; p = (p + 3) & ~3
    p += 4  # SerializationData.SerializedFormat (0 = binary)
    ln = struct.unpack_from('<i', raw, p)[0]; p += 4
    o = _Odin(raw[p:p + ln])
    return o.node()


def _read_names(raw):
    """NovelNameText: speaker id -> (CN name, character id)."""
    p = raw.find(b'\x02\x00\x00\x00N1\x00\x00') - 4
    cnt = struct.unpack_from('<i', raw, p)[0]; p += 4
    out = {}
    for _ in range(cnt):
        vals = []
        for _ in range(4):
            n = struct.unpack_from('<i', raw, p)[0]; p += 4
            vals.append(raw[p:p + n].decode('utf-8')); p += n; p = (p + 3) & ~3
        out[vals[0]] = (vals[1], vals[3])
    return out


# ---------------------------------------------------------------- lore

def build_lore(ctx, out: Path) -> None:
    """Every script of novel.ab as one txt per chapter + Hightea + Battle_Dialogue + INDEX.tsv."""
    rows, LANG = ctx.rows, ctx.lang
    VI = ctx.vi
    CN = {}
    for r in rows('characterTable'):
        CN.setdefault(r['id'], r.get('namelanText') or r.get('name') or '')

    def who(cid):
        """'错金博山炉 · Thác Kim Bác Sơn Lư' for a playable character id, else ''."""
        return f'{CN.get(cid, "")} · {VI[cid]}'.strip(' ·') if VI.get(cid) else CN.get(cid, '')

    env = ctx.env('novel.ab')
    paths = {}
    for p in ctx.ab['novel.ab']['PathList']:
        parts = p.split('/')
        if len(parts) > 5 and parts[3] == 'config' and parts[4] in ('novel', 'lan'):
            paths[(parts[4], Path(p).stem.lower())] = parts[5] if len(parts) > 6 else ''
    lan, node, name_raw = {}, {}, None
    for o in env.objects:
        if o.type.name != 'MonoBehaviour':
            continue
        x = o.read(check_read=False)
        sc = x.m_Script.read().m_Name
        if sc == 'NovelLanAsset':
            lan[x.m_Name] = parse_mono(o.get_raw_data())['LansList']['_0']
        elif sc == 'NovelNodeAsset':
            node[x.m_Name] = parse_mono(o.get_raw_data())['NodesList']['_0']
        elif x.m_Name == 'NovelNameText':
            name_raw = o.get_raw_data()
    SPK = _read_names(name_raw)

    def speaker(sp):
        if sp in ('', 'null', None):
            return '旁白'
        if sp.lower() == 'player':
            return '玩家 [player]'
        nm, cid = SPK.get(sp, (sp, ''))
        if cid:
            vi = VI.get(cid)
            return f'{nm} ({vi}) [{cid}]' if vi else f'{nm} [{cid}]'
        return f'{nm} [{sp}]'

    # ---- chapter / event / story names, each from an explicit MasterData relation (see README.txt)
    narr = {str(r.get('script') or r.get('id')).upper(): r for r in rows('narrativeMap')}
    main_ids = {str(s).upper() for r in rows('MainPlotMap') for s in (r.get('PlotId') or [])}
    by_script = {str(r.get('id')).upper(): str(r.get('script') or r.get('id')).upper() for r in rows('narrativeMap')}
    ag = {str(r['id']): r for r in rows('activityGroup')}
    ag_key = collections.defaultdict(list)
    for r in rows('activityGroup'):
        ag_key[str(r['key']).lower()].append(r)
    aml = {r['area']: r for r in rows('activityMapList')}
    EVENT = {}
    for r in rows('ActivityPlotMap'):
        g = ag.get(str(r['ActivityType']))
        if g:
            EVENT.setdefault(r['PlotId'][:4].upper(), dict(name=g['nameLanText'], gid=g['id'], start=g.get('startTime', '')[:10]))
    PLOT_INFO = {}
    for r in rows('ActivityPlotMap'):
        PLOT_INFO.setdefault(r['PlotId'].upper(), r.get('PlotInfoLanText') or '')
    for r in rows('activityMapEventList'):
        p = str(r.get('eventParam') or '').upper()
        if not p.startswith('E'):
            continue
        m = aml.get(r['area'], {})
        e = EVENT.setdefault(p[:4], {})
        t = m.get('mapTitle') or ''
        if re.match(r'\d+\.\d', t):
            e.setdefault('version', t.split('-')[0])
        cands = ag_key.get(re.sub(r'\d+$', '', r['area']).lower(), [])
        if 'name' not in e and len(cands) == 1:
            g = cands[0]; e.update(name=g['nameLanText'], gid=g['id'], start=g.get('startTime', '')[:10])
    for r in rows('missionMap'):
        m = re.search(r'完成【(.+?)】.*剧情', r.get('NameLanText') or '')
        if not m:
            continue
        for c in r.get('condition1') or []:
            for sid_ in str(c.get('ElementID') or '').split(','):
                s_ = sid_.strip().upper()
                if s_.startswith('E') and 'name' not in EVENT.get(s_[:4], {}):
                    EVENT.setdefault(s_[:4], {}).update(name=m.group(1), gid='-', via='missionMap "完成【…】所有剧情"')
    for r in rows('activityMapEventList'):
        p = str(r.get('eventParam') or '').upper()
        e = EVENT.get(p[:4]) if p.startswith('E') else None
        if e is not None and 'name' not in e:
            n = re.sub(r'<[^>]+>', '', aml.get(r['area'], {}).get('namelanText') or '')
            n = re.sub(r'[·\-](剧情|关卡)$', '', n).strip()
            if n and n not in ('剧情', '挑战关卡'):
                e.update(name=n, gid='-', via='activityMapList.namelanText (tên bản đồ)')
    areas = {r['area']: r for r in rows('areas')}
    MAIN = {}
    for r in rows('MainPlotMap'):
        a = areas.get(r.get('MainType'), {})
        for nid in r.get('PlotId') or []:
            MAIN[by_script.get(str(nid).upper(), str(nid).upper())] = f'{a.get("mapTitlelanText", r.get("MainType"))} ({a.get("mapDes", "")})'.replace(' ()', '')
    BRANCH = {}
    for r in rows('BranchPlotMap'):
        for s in r.get('BranchPlotId') or []:
            BRANCH[str(s).upper().split('_')[0]] = r.get('BranchNameLanText', '')
    chap = {r['ID']: r.get('nameLanText', '') for r in rows('branchChapterList')}
    bstory = {r['ID']: r for r in rows('branchStoryList')}
    STORY = {}
    for r in rows('branchEventList'):
        s = bstory.get(r['storyID'], {})
        if r.get('param'):
            STORY[str(r['param']).upper()] = (s.get('namelanText', ''), chap.get(r.get('chapterID'), ''), s.get('page', ''))
    AREA_EV = {}
    for r in rows('areaEvents'):
        if r.get('eventParam'):
            AREA_EV[str(r['eventParam']).upper()] = areas.get(r.get('area'), {}).get('mapTitlelanText', r.get('area', ''))
    guide_p = ctx.snap / 'masterdata' / 'guide.json'
    GUIDE = guide_p.read_text(encoding='utf-8') if guide_p.is_file() else ''

    def names_for(sid):
        s = sid.upper(); h = []
        if s in MAIN: h.append(f'CHAPTER_CN: {MAIN[s]}')
        e = EVENT.get(s[:4]) if s.startswith('E') else None
        if e and e.get('name'):
            src = (f'activityGroup {e["gid"]}' + (f', mở {e["start"]}' if e.get('start') else '')
                   if e.get('gid') not in (None, '-') else f'nguồn: {e["via"]}')
            h.append(f'EVENT_CN: {e["name"].replace(chr(10), " ")} ({src})')
        if e and e.get('version'): h.append(f'VERSION: {e["version"]}')
        if s.split('_')[0] in BRANCH: h.append(f'BRANCH_CN: {BRANCH[s.split("_")[0]]}')
        if s in STORY:
            n, c, cid = STORY[s]
            if n: h.append(f'STORY_CN: {n}' + (f' — truyện của {who(cid)} [{cid}]' if who(cid) else ''))
            if c: h.append(f'REGION_CN: {c}')
        if s in AREA_EV: h.append(f'AREA_CN: {AREA_EV[s]}')
        if PLOT_INFO.get(s): h.append(f'PLOT_INFO_CN: {PLOT_INFO[s]}'.replace('\n', ' '))
        if f'"{s}"' in GUIDE or f'"{sid}"' in GUIDE: h.append('USED_IN: guide.json (hướng dẫn / tân thủ)')
        return h

    def group_name(sid):
        for line in names_for(sid):
            k, _, v = line.partition(': ')
            if k in ('EVENT_CN', 'CHAPTER_CN', 'BRANCH_CN', 'STORY_CN', 'AREA_CN'):
                return re.split(r' \((activityGroup|nguồn:)', v)[0]
        return ''

    def section(sid):
        s = sid.upper()
        if s in main_ids or s.startswith('M'): return 'Main_Story'
        if s.startswith('E'): return 'Events'
        if s.startswith('B'): return 'Side_Stories_B'
        if s.startswith('C'): return 'Side_Stories_C'
        return 'Other_Stories'

    def render(sid):
        L = {r['Key']: r for r in lan.get(sid, []) if r}
        used, lines, chars, last_bg = set(), [], collections.Counter(), None
        for n in filter(None, node[sid]):
            b = n.get('BackgroundData')
            if b and b.get('AssetName') and b['AssetName'] != last_bg:
                last_bg = b['AssetName']; lines.append(f'〔背景 {last_bg}〕')
            for k in ('Character1Data', 'Character2Data', 'Character3Data'):
                c = n.get(k)
                if c and c.get('CharacterId'): chars[c['CharacterId']] += 1
            for v in (n.get('SubtitleData') or {}).values():
                if isinstance(v, str) and v in L and L[v]['CN']:
                    used.add(v); lines.append(f'【字幕】{L[v]["CN"]}')
            dd = n.get('DialogData')
            if dd:
                key = dd.get('Content'); used.add(key)
                lines.append(f'{speaker(dd.get("Speaker"))}：{L.get(key, {}).get("CN", f"<{key}>")}')
            od = n.get('OptionData') or {}
            for x in ((od.get('Options') or {}).get('_0') or []):
                k, _, i = (x.get('OptionKey') or '').rpartition('_')
                if k in L:
                    used.add(k); parts = L[k]['CN'].split('_')
                    lines.append(f'  ▸ 选项：{parts[int(i)] if i.isdigit() and int(i) < len(parts) else L[k]["CN"]}')
        extras = [(k, r['CN']) for k, r in L.items() if k not in used and r.get('CN')]
        return lines, chars, extras

    def header(sid, extra=()):
        m = narr.get(sid.upper(), {})
        h = [f'SCRIPT: {sid.lower()}']
        if m.get('nameLanText'): h.append(f'TITLE_CN: {m["nameLanText"]}')
        h += names_for(sid)
        if m.get('narraDesLanText'): h.append(f'SUMMARY_CN: {m["narraDesLanText"]}'.replace('\n', ' '))
        return h + list(extra)

    index = [('SECTION', 'CATEGORY', 'GROUP_NAME_CN', 'SCRIPT_ID', 'TITLE_CN', 'LINES', 'FILE')]
    counts = collections.Counter()
    for sid in sorted(node, key=str.lower):
        cat = paths.get(('novel', sid.lower()), sid.lower()[:4])
        sec = section(sid)
        lines, chars, extras = render(sid)
        cast = [f'{who(c) or c} [{c}]' for c, _ in chars.most_common() if c in CN]
        txt = header(sid, [f'CHARACTERS: {"; ".join(cast)}'] if cast else []) + [''] + [f'{x}\n' for x in lines]
        if extras:
            txt += ['===== 未引用 / 内部记录 (ghi chú dàn dựng, dòng không node nào dùng) =====', '']
            txt += [f'[{k}] {v}' for k, v in extras]
        f = out / sec / cat / f'{sid.lower()}.txt'
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('\n'.join(txt) + '\n', encoding='utf-8')
        index.append((sec, cat, group_name(sid), sid.lower(), narr.get(sid.upper(), {}).get('nameLanText', ''),
                      str(sum(1 for x in lines if not x.startswith('〔'))), f.relative_to(out).as_posix()))
        counts[sec] += 1
    for sid in sorted(set(lan) - set(node), key=str.lower):  # text without node structure
        cat = paths.get(('lan', sid.lower()), sid.lower()[:4])
        f = out / 'LAN_Only' / cat / f'{sid.lower()}.txt'
        f.parent.mkdir(parents=True, exist_ok=True)
        recs = [r for r in lan[sid] if r and r.get('CN')]
        f.write_text('\n'.join(header(sid, ['STRUCTURE: MISSING']) + [''] + [f'[{r["Key"]}] {r["CN"]}\n' for r in recs]) + '\n', encoding='utf-8')
        index.append(('LAN_Only', cat, group_name(sid), sid.lower(), narr.get(sid.upper(), {}).get('nameLanText', ''),
                      str(len(recs)), f.relative_to(out).as_posix()))
        counts['LAN_Only'] += 1

    # ---- Hightea (phòng trà)
    teas = {str(r['id']): r.get('nameLanText', '') for r in rows('itemMap')}
    hm = {r['ID']: r for r in rows('highteaCharacterMap')}
    ask = collections.defaultdict(list)
    for r in rows('playerAskMap'):
        ask[r['CharacterId']].append(r)
    tea_names = lambda ids: ', '.join(f'{teas.get(str(i), "?")} ({i})' for i in ids or []) or '—'
    hidx = [('CHARACTER_ID', 'NAME_CN', 'NAME_VI', 'TOPICS', 'FILE')]
    HT = out / 'Hightea'
    HT.mkdir(parents=True, exist_ok=True)
    for cid in sorted(set(hm) | set(ask)):
        h = hm.get(cid, {})
        cn, vi = CN.get(cid) or cid, VI.get(cid, '')
        o = [f'CHARACTER: {cid}', f'NAME_CN: {cn}', f'NAME_VI: {vi or "(chưa có tên Việt)"}',
             *([] if CN.get(cid) else ['NOTE: không có trong characterTable']),
             f'SOURCE: highteaCharacterMap + playerAskMap ({ctx.snap.name})', '',
             f'喜爱的茶 UPTea: {tea_names(h.get("UPTea"))}', f'感兴趣的茶 InterestTea: {tea_names(h.get("InterestTea"))}',
             f'不喜欢的茶 ForbidTea: {tea_names(h.get("ForbidTea"))}', '', '== 品茶评语 (Comments) ==']
        o += [f'- {LANG.get(k, k)}' for k in h.get('Comments') or []]
        o += ['', '== 结局 ==', f'胜利 1：{h.get("VictoryEndLanText", "")}',
              f'胜利 2：{(h.get("VictoryEnd2LanText") or "").replace(chr(92) + "n", " / ")}',
              f'失败：{LANG.get("highteaLan_hightea_failEndLan_" + cid, h.get("FailEnd", ""))}', '',
              '== 话题 (ID | Trend | TopicType; follow-ups indented under the topic that opens them) ==']
        topics = {r['ID']: r for r in ask.get(cid, [])}
        child = {c for r in topics.values() for c in r.get('TopicNext') or []}

        def emit(r, depth, seen=()):
            pad = '    ' * depth
            o.append(f'{pad}[{r["ID"]} | Trend {r["Trend"]} | Type {r["TopicType"]}] 玩家：{r.get("TopicContentLanText", "")}')
            o.append(f'{pad}    {cn}{f" ({vi})" if vi else ""}：{r.get("TopicRespLanText", "")}')
            for c in r.get('TopicNext') or []:
                if c in topics and c not in seen:
                    emit(topics[c], depth + 1, (*seen, r['ID']))
        for tid in sorted(topics):
            if tid not in child:
                emit(topics[tid], 0)
        name = safe(f'{cid} {vi or cn}')
        (HT / f'{name}.txt').write_text('\n'.join(o) + '\n', encoding='utf-8')
        hidx.append((cid, cn, vi, str(len(topics)), f'Hightea/{name}.txt'))
    (HT / 'INDEX.tsv').write_text('\n'.join('\t'.join(r) for r in hidx) + '\n', encoding='utf-8-sig')

    # ---- Battle dialogue (battledrama)
    BD = out / 'Battle_Dialogue'
    BD.mkdir(parents=True, exist_ok=True)
    groups = collections.defaultdict(list)
    for r in rows('battledrama'):
        groups[r['ID'].split('_')[0]].append(r)
    for g, rs in sorted(groups.items()):
        o = [f'STAGE: {g}', 'SOURCE: battledrama', '']
        for r in sorted(rs, key=lambda r: r['ID']):
            m = re.search(r'/Character/([^/]+)/', r.get('ProfilePath') or '')
            cid = m.group(1) if m else ''
            tag = f' ({VI[cid]}) [{cid}]' if VI.get(cid) else (f' [{cid}]' if cid else '')
            o.append(f'{r.get("NameLanText", "")}{tag}：{r.get("ContentLanText", "")}\n')
        (BD / f'{g.lower()}.txt').write_text('\n'.join(o) + '\n', encoding='utf-8')

    (out / 'INDEX.tsv').write_text('\n'.join('\t'.join(r) for r in index) + '\n', encoding='utf-8-sig')
    print(f'Lore {ctx.snap.name}: {dict(counts)}, phòng trà {len(hidx) - 1}, thoại trận {len(groups)} → {out}', flush=True)


# ---------------------------------------------------------------- story CG

ORDER = ['Main_Story', 'Events', 'Side_Stories_B', 'Side_Stories_C', 'Other_Stories', 'Battle_Dialogue', 'LAN_Only']
SECTION_VI = {'Main_Story': '1_Cot_truyen_chinh', 'Events': '2_Event', 'Side_Stories_B': '3_Truyen_phu_B',
              'Side_Stories_C': '4_Truyen_phu_C', 'Other_Stories': '5_Truyen_khac', 'Battle_Dialogue': '6_Hoi_thoai_chien_dau',
              'LAN_Only': '7_Khac'}


def build_cg(ctx, lore: Path, out: Path) -> None:
    """Each CG (〔背景 cg…〕 in the lore) goes to the folder of the first script that shows it; INDEX.tsv lists every user."""
    rows = list(csv.DictReader(open(lore / 'INDEX.tsv', encoding='utf-8-sig'), delimiter='\t'))
    rows.sort(key=lambda r: (ORDER.index(r['SECTION']) if r['SECTION'] in ORDER else 99, nat(r['FILE'])))
    first, uses = {}, {}
    for r in rows:
        p = lore / r['FILE']
        if not p.exists():
            continue
        for name in re.findall(r'〔背景 ([^〕]+)〕', p.read_text(encoding='utf-8')):
            if not name.lower().startswith('cg'):
                continue
            tag = f"{r['SCRIPT_ID']} {r['TITLE_CN']}".strip()
            if tag not in uses.setdefault(name, []):
                uses[name].append(tag)
            first.setdefault(name, r)

    def variants(name):
        n = name.lower().replace('.', '_')  # scripts write cg_1_3.1 for bundle cg_1_3_1
        if n.endswith('sex'):
            return [(n + 'f', '_nu'), (n + 'm', '_nam')]  # Nhà Sưu Tầm nữ / nam
        return [(n, '')]

    wanted = [f'images_novel_background_{low}.ab' for name in first for low, _ in variants(name)]
    for i in range(0, len(wanted), 40):
        ctx.prefetch(wanted[i:i + 40])
    counter, index, missing = {}, [], []
    for name, r in first.items():
        folder = out / SECTION_VI.get(r['SECTION'], r['SECTION']) / safe(f"{r['CATEGORY']} {r['GROUP_NAME_CN']}")
        counter[folder] = counter.get(folder, 0) + 1
        for low, suffix in variants(name):
            logical = f'images_novel_background_{low}.ab'
            if logical not in ctx.ab:
                missing.append(f'{name}\t{logical}\tkhông có trong index game'); continue
            try:
                texs = [o.read() for o in ctx.env(logical).objects if o.type.name == 'Texture2D']
            except Exception as e:
                missing.append(f'{name}\t{logical}\t{e}'); continue
            if not texs:
                missing.append(f'{name}\t{logical}\tkhông có ảnh'); continue
            t = max(texs, key=lambda x: x.m_Width * x.m_Height)
            folder.mkdir(parents=True, exist_ok=True)
            f = folder / f'{counter[folder]:03d}_{safe(name)}{suffix}.png'
            t.image.save(f)
            index.append([str(f.relative_to(out)), name, f'{t.m_Width}x{t.m_Height}', ' | '.join(uses[name])])
    out.mkdir(parents=True, exist_ok=True)
    with open(out / 'INDEX.tsv', 'w', encoding='utf-8-sig', newline='') as fh:
        w = csv.writer(fh, delimiter='\t')
        w.writerow(['FILE', 'CG_NAME', 'SIZE', 'SCRIPTS (id + title)'])
        w.writerows(index)
    (out / '_THIEU.txt').write_text('\n'.join(missing) + '\n', encoding='utf-8')
    print(f'CG: {len(first)} tên trong truyện, {len(index)} ảnh, thiếu {len(missing)} → {out}', flush=True)


# ---------------------------------------------------------------- 冬谷归档 (Đông Cốc quy đáng)

CSS = '''body{margin:0;background:#1d1c1a;color:#e8e2d6;font:16px/1.75 "Noto Serif SC","Songti SC",serif}
main{max-width:900px;margin:0 auto;padding:24px 16px 80px} a{color:#d8b878} h1{font-size:26px;border-bottom:2px solid #d8b878;padding-bottom:6px;margin-top:56px}
h2{font-size:20px;margin:28px 0 8px} h2 span,.tag,.meta,.note,figcaption,.tip{color:#a39a8a;font-size:13px} article{border-top:1px solid #3a372f;padding-top:16px;margin-top:20px}
.row{display:grid;grid-template-columns:minmax(0,260px) 1fr;gap:16px} img{max-width:100%;border-radius:6px;display:block} figure{margin:8px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px} .txt{background:#26241f;border-radius:6px;padding:12px 16px;margin:10px 0}
p{margin:0 0 6px} table{border-collapse:collapse;margin-top:12px} td,th{border:1px solid #3a372f;padding:4px 10px} .miss{color:#e07a5f}
@media(max-width:640px){.row{grid-template-columns:1fr}}
@media print{body{background:#fff;color:#111;font-size:12pt} a{color:#111} h1{border-color:#999;break-before:page} main>h1:first-child{break-before:auto}
h2 span,.tag,.meta,.note,figcaption,.tip{color:#666} .txt{background:#f3f1ec} article{border-color:#ccc} td,th{border-color:#bbb}
figure,.row,tr{break-inside:avoid} h2,h3{break-after:avoid}}'''


def build_dongcoc(ctx, out: Path) -> None:
    """冬谷归档: magazines, files, event recap, dispatch stories, group photo → one html (images embedded) + images/ + raw/."""
    (out / 'raw').mkdir(parents=True, exist_ok=True)

    def rows(t):
        d = ctx.table(t)
        (out / 'raw' / f'{t}.json').write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8')
        return list(d.values()) if isinstance(d, dict) else d

    missing = []

    def img(section, tex, name, cap=None):
        t = tex.get(str(name).lower())
        if t is None:
            missing.append(f'{section}/{name}')
            return f'<p class="miss">[thiếu ảnh {html.escape(str(name))}]</p>'
        d = out / 'images' / section
        d.mkdir(parents=True, exist_ok=True)
        f = d / f'{safe(t.m_Name)}.png'
        if not f.exists():
            t.image.save(f)
        buf = io.BytesIO()
        t.image.save(buf, 'WEBP', quality=88)  # embedded copy only; images/ keeps the PNG
        return (f'<figure><img src="data:image/webp;base64,{base64.b64encode(buf.getvalue()).decode()}">'
                f'<figcaption>{html.escape(cap or t.m_Name)}.png</figcaption></figure>')

    clean = lambda s: re.sub(r'</?(size|color|b|i)[^>]*>', '', s or '')
    para = lambda s: ''.join(f'<p>{html.escape(l)}</p>' if l.strip() else '<br>' for l in clean(s).split('\n'))
    toc, body, sids = [], [], []

    def section(sid, title, note, html_):
        toc.append(f'<li><a href="#{sid}">{title}</a></li>'); sids.append(sid)
        body.append(f'<section id="{sid}"><h1>{title}</h1><div class="note">{note}</div>{html_}</section>')

    # 杂志报刊 + 档案文件 share TxtContentMap
    txt = {str(r['ID']): r for r in rows('TxtContentMap')}
    ja = ctx.textures('images_journalarchives.ab')

    def txt_block(ids, sec):
        parts = []
        for i in ids:
            r = txt.get(str(i))
            if not r:
                parts.append(img(sec, ja, i)); continue  # ContentType 2: the content is an image
            pics = ''.join(img(sec, ctx.textures(f'images_novel_background_{str(s).lower()}.ab'), s)
                           for s in dict.fromkeys(r.get('Sprite') or []))
            tip = f'<div class="tip">Mở khóa: {html.escape(r["UnlockTipLanText"])}</div>' if r.get('UnlockTipLanText') else ''
            parts.append(f'<div class="txt"><div class="tag">TxtContent {i}</div>{tip}{para(r.get("TextContentLanText") or r["TextContent"])}{pics}</div>')
        return ''.join(parts)

    labels = {str(r['Id']): r['MagazineTypeNameLanText'] for r in rows('LabelMap')}
    h = ''
    for r in rows('MagazineMap'):
        h += (f'<article><h2>{html.escape(r["MagazineNameLanText"])}</h2>{img("tap_chi", ja, r["Icon"])}'
              f'<div class="meta">Loại: {html.escape(str(labels.get(str(r["MagazineType"]), r["MagazineType"])))} · Mở khóa: {html.escape(r.get("UnlockTipLanText") or "")}</div>'
              f'{para(r.get("MagazineInfoLanText"))}{txt_block(r["TextContent"], "tap_chi")}</article>')
    section('tapchi', '杂志报刊 · Tạp chí', 'MagazineMap → TxtContentMap; icon trong images_journalarchives, ảnh minh họa trong images_novel_background_*', h)

    bg = ctx.textures('images_archivesbg.ab')
    h = ''
    for r in sorted(rows('FileMap'), key=lambda r: int(r['ID'])):
        h += (f'<article><h2><span>{r["ID"]}</span> {html.escape(clean(r["FileNameLanText"]))}</h2>{img("ho_so", ja, r["Icon"])}'
              f'<div class="meta">FileType {r["FileType"]} · {"chữ" if r["ContentType"] == 1 else "ảnh"} · khu vực {r.get("MainType") or "-"} · nền {r.get("BgRes") or "-"}'
              f'{" · Mở khóa: " + html.escape(r["UnlockTipLanText"]) if r.get("UnlockTipLanText") else ""}</div>'
              f'{txt_block(r["TextContent"], "ho_so")}</article>')
    h += ''.join(img('ho_so', bg, n) for n in bg)
    used_txt = {str(x) for r in rows('MagazineMap') + rows('FileMap') for x in r['TextContent']}
    orphans = [i for i in txt if i not in used_txt]
    if orphans:
        h += f'<h2>Bài trong TxtContentMap không tạp chí/hồ sơ nào trỏ tới ({len(orphans)})</h2>' + txt_block(orphans, 'ho_so')
    section('hoso', '档案文件 · Hồ sơ', 'FileMap → TxtContentMap (ContentType 1) hoặc ảnh images_journalarchives (ContentType 2)', h)

    # 事件回顾 / 拾遗
    nt = ctx.textures('images_novel_novelterminal.ab')
    groups = {str(r['id']): (r.get('nameLanText') or r.get('NameLanText') or r.get('name') or '') for r in rows('activityGroup')}
    by = collections.defaultdict(list)
    for r in rows('ActivityPlotMap'):
        by[r['ActivityType']].append(r)
    h = ''
    for t, rs in by.items():
        h += f'<h2>{html.escape(groups.get(str(t), str(t)))} <span>(ActivityType {t}, {len(rs)} hồi)</span></h2>'
        for r in sorted(rs, key=lambda r: int(r['Arrange'])):
            h += f'<article class="row">{img("su_kien", nt, r["Icon"])}<div><div class="tag">{r["PlotId"]}</div>{para(r.get("PlotInfoLanText"))}</div></article>'
    h += '<h2>Truyện nhánh (BranchPlotMap)</h2>'
    for r in rows('BranchPlotMap'):
        h += f'<article class="row">{img("su_kien", nt, r["Icon"])}<div><h3>{html.escape(r["BranchNameLanText"])}</h3><div class="tag">{", ".join(r["BranchPlotId"])}</div></div></article>'
    h += '<h2>Cốt truyện chính (MainPlotMap, chỉ có ảnh)</h2><div class="grid">'
    for r in rows('MainPlotMap'):
        h += f'<div>{img("su_kien", nt, r["Icon"], r["Icon"] + " · " + ", ".join(r["PlotId"]))}</div>'
    section('sukien', '事件回顾 / 时光拾遗 · Hồi tưởng sự kiện', 'ActivityPlotMap (tóm tắt từng hồi) + BranchPlotMap + MainPlotMap; ảnh trong images_novel_novelterminal', h + '</div>')

    # 通识课程-外勤见闻
    ld = ctx.textures('images_longtermdispatch.ab')
    h = ''
    for r in sorted(rows('DispatchStoryMap'), key=lambda r: int(r['ID'])):
        h += f'<article><h2><span>{r["ID"]}</span> {html.escape(r.get("NameLanText") or r["Name"])}</h2>{img("ngoai_can", ld, r["Icon"])}{para(r.get("PlotLanText") or r["Plot"])}</article>'
    section('ngoaican', '通识课程-外勤见闻 · Ngoại cần kiến văn', 'DispatchStoryMap; ảnh trong images_longtermdispatch', h)

    # 影像纪念
    gp = ctx.textures('ui_groupphotopanel.ab')
    h = img('anh_chung', gp, 'ui_dhz_bj') + '<table><tr><th>Thời đại</th><th>Khoảng thời gian</th><th>Nhóm</th></tr>'
    for r in rows('GroupPhotoTypeMaps'):
        h += f'<tr><td>{html.escape(r["ButtonTextLanText"])}</td><td>{html.escape(r["TimeInfoLanText"])}</td><td>{", ".join(r["DynastyGroup"])}</td></tr>'
    rows('GroupPhotoConfigMap')
    section('anhchung', '影像纪念 · Ảnh kỷ niệm', 'GroupPhotoTypeMaps; vị trí từng nhân vật trên ảnh chung ở raw/GroupPhotoConfigMap.json (game tự ghép lúc chạy)', h + '</table>')

    page = (f'<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>冬谷归档</title><style>{CSS}</style></head><body><main><h1 style="margin-top:0">冬谷归档</h1>'
            f'<div class="note">Dữ liệu game {ctx.snap.name}</div><ol>{"".join(toc)}</ol>{"".join(body)}</main></body></html>')
    (out / 'Dong_coc_quy_dang.html').write_text(page, encoding='utf-8')
    head = f'<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>冬谷归档</title><style>{CSS}</style></head><body><main>'
    for sid, sec in zip(sids, body):  # one file per section, for PDF printing
        (out / f'_in_{sid}.html').write_text(head + sec.replace('<section', '<section style="margin:0"', 1) + '</main></body></html>', encoding='utf-8')
    (out / '_THIEU.txt').write_text('\n'.join(missing) + '\n', encoding='utf-8')
    print(f'Đông Cốc: {len(toc)} mục, {sum(1 for _ in (out / "images").rglob("*.png"))} ảnh, thiếu {len(missing)} → {out}', flush=True)


# ---------------------------------------------------------------- 焕彰 (hoán chương)

_STAT = {"Hp": "生命", "Atk": "攻击", "Def": "物理防御", "PhysicDef": "物理防御", "MagicDef": "法术防御",
         "Speed": "速度", "Crit": "暴击", "MagicAtk": "法术攻击", "PhysicalAtk": "物理攻击"}
_untag = lambda s: re.sub(r"</?color[^>]*>", "", s or "").replace("&#10;", "\n")


def huanzhang_groups(ctx) -> dict:
    """(character, trial dungeon) -> BrilliantMap rows: one huanzhang with its lower-rarity stage rows."""
    groups: dict = {}
    for r in ctx.rows("BrilliantMap"):
        groups.setdefault((r["Id"][:5], r.get("DungeonId") or r["Id"]), []).append(r)
    return groups


def build_hoanchuong(ctx, out: Path, keys=None) -> int:
    """One txt per huanzhang: story, effect + statuses it mentions, stats per level, costs, trial stage.
    keys: only these huanzhang_groups keys (update), default all."""
    from datetime import datetime, timedelta, timezone
    bj = timezone(timedelta(hours=8))
    date = lambda t: datetime.fromtimestamp(t, bj).strftime("%d/%m/%Y %H:%M") if isinstance(t, int) and t else "?"
    L = ctx.lang
    chars = {str(r["id"]): r for r in ctx.rows("characterTable")}
    talents = {r["id"]: r for r in ctx.rows("talentBankMap")}
    passive = {(r["GroupId"], r["Level"]): r for r in ctx.rows("characterPassiveSkillMap")}
    dungeons = {r["id"]: r for r in ctx.rows("dungeonMap")}
    up = {r["Id"]: r for r in ctx.rows("BrilliantUpMap")}
    items = {}
    for t in ("itemMap", "ItemMap", "itemTable"):
        try:
            items.update({str(r.get("id") or r.get("Id")): _untag(r.get("nameLanText") or r.get("NameLanText") or r.get("name") or "")
                          for r in ctx.rows(t)})
        except Exception:
            pass
    bname = {k.removeprefix("SkillLan_Buff_NameLan_"): _untag(v) for k, v in L.items() if k.startswith("SkillLan_Buff_NameLan_")}
    bdesc = {k.removeprefix("SkillLan_Buff_DescriptionLan_"): _untag(v) for k, v in L.items() if k.startswith("SkillLan_Buff_DescriptionLan_")}

    def stat(p):
        k, _, v = str(p).partition(",")
        base, _, kind = k.rpartition("_") if k.endswith(("_FIX", "_PERCENT")) else (k, "", "")
        return f"{_STAT.get(base, base)} +{v}{'%' if kind == 'PERCENT' else ''}" if v else str(p)

    cost = lambda lst: ", ".join(f"{items.get(str(c['id']), c['id'])} ×{c['count']}" for c in lst or [])

    def glossary(cid, text):
        res = []
        for name in dict.fromkeys(re.findall(r"<color=#ff6724>(.*?)</color>", text)):
            ks = sorted((k for k, v in bname.items() if v == name), key=lambda k: cid not in k)
            d = next((bdesc[k] for k in ks if bdesc.get(k)), "")
            if d:
                res.append(f"  【{name}】 " + re.sub(r"\[EffectParam,\d+\]", "?", d))  # no \ in f-string (py<3.12)
        return res

    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for key, rows in sorted(huanzhang_groups(ctx).items()):
        if keys is not None and key not in keys:
            continue
        cid = key[0]
        rows = sorted(rows, key=lambda r: r["Id"])
        main = next((r for r in rows if r.get("IconName")), rows[-1])
        c = chars.get(cid, {})
        title = _untag(main.get("IconNameLanText") or main.get("IconName"))
        lines = [f"{cid} {c.get('namelanText') or c.get('name', '')} ({ctx.vi.get(cid, '')})",
                 f"焕彰：{title}   icon: {main.get('Icon')}.png",
                 f"Mở {date(main.get('UnlockDate'))} → đóng {date(main.get('ClosingDate'))} (giờ Bắc Kinh)"]
        d = dungeons.get(main.get("DungeonId"), {})
        if d:
            lines.append(f"Màn thử: {d.get('NameLanText')} ({main['DungeonId']})")
        lines += ["", "== 故事 (truyện) ==", _untag(main.get("IconInfoLanText") or main.get("IconInfo")), ""]
        for bid in main.get("Buff") or []:
            p = passive.get((bid, 1), {})
            raw = p.get("DescriptionLanText", "")
            lines += [f"== 效果 (hiệu ứng): {_untag(p.get('NameLanText'))} ==",
                      re.sub(r"\{(Buff_[^}]+)\}", lambda m: f"\n[{bname.get(m[1], m[1])}] {bdesc.get(m[1], '')}", _untag(raw)), ""]
            g = glossary(cid, raw)
            if g:
                lines += ["Trạng thái nhắc tới:", *g, ""]
        for r in rows:
            lines += [f"== {r['Id']} (độ hiếm {r.get('Rare')}) ==",
                      f"Mở khoá: {cost(r.get('RequireCoin'))}; {cost(r.get('ItemCost1'))}"]
            for i, lv in enumerate(r.get("PropertyUp") or [], 1):
                lines.append(f"  Cấp {i}: " + ", ".join(stat(p) for t in lv for p in talents.get(t, {}).get("talentParam1", [])))
            u = up.get(r["Id"])
            if u:
                lines.append(f"  Nâng cấp: {items.get('3', 'coin')} ×{u['Coin']}; {cost(u.get('Res'))}")
            lines.append("")
        (out / f"{cid}_{safe(title or main['Id'])}.txt").write_text("\n".join(lines), encoding="utf-8")
        n += 1
    print(f"Hoán chương: {n} file → {out}", flush=True)
    return n
