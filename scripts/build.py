"""Reproduce the Korean C2 project and static site from checksum-locked inputs."""
from __future__ import annotations

import copy
import csv
import hashlib
import html
import io
import json
import math
import re
import shutil
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'source'
DIST = ROOT / 'dist'
FONT_IDS = {'BattleFont': 47, 'SansFont': 48, 'DefaultFont': 49, 'DamageFont': 50}
CELLS = {'BattleFont': (18, 18), 'SansFont': (18, 18), 'DefaultFont': (16, 16), 'DamageFont': (33, 32)}
REFS = {
    'fileIndex': ('FileIndex', [23, 'FileIndex']),
    'attackCount': ('AttackLoader.Width', [20, 60, 41, False, None]),
    'stage': ('(loopindex + 1)', [4, [19, 102], [0, 1]]),
    'attack': ('AttackLoader.At(loopindex)', [20, 60, 103, False, None, [[19, 102]]]),
    'page': ('(floor(MenuStack.Back / 4) + 1)', [4, [19, 81, [[7, [20, 61, 108, False, None], [0, 4]]]], [0, 1]]),
    'itemId': ('ItemID', [23, 'ItemID']),
    'heal': ('ItemDB.At(ItemID, 1)', [20, 58, 103, False, None, [[23, 'ItemID'], [0, 1]]]),
    'line': ('Line', [23, 'Line']),
    'label': ('Function.Param(0)', [20, 3, 38, False, None, [[0, 0]]]),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')


def arrays(value):
    if isinstance(value, list):
        yield value
        for child in value:
            yield from arrays(child)
    elif isinstance(value, dict):
        for child in value.values():
            yield from arrays(child)


def expression(spec):
    """Emit equivalent C2 editor expressions and exported C2 expression trees."""
    if isinstance(spec, str):
        require(unicodedata.normalize('NFC', spec) == spec, 'Translations must use NFC Hangul')
        return ' & newline & '.join('"' + s.replace('"', '""') + '"' for s in spec.split('\n')), [2, spec]
    if 'ref' in spec:
        source, node = REFS[spec['ref']]
        return source, copy.deepcopy(node)
    if 'concat' in spec:
        pieces = [expression(s) for s in spec['concat']]
        node = pieces[0][1]
        for _, part in pieces[1:]:
            node = [10, node, part]
        return ' & '.join(s for s, _ in pieces), node
    if 'select' in spec:
        select = spec['select']
        ref_source, ref_node = expression({'ref': select['ref']})
        source, node = expression(select['fallback'])
        for key, value in reversed(list(select['choices'].items())):
            val_source, val_node = expression(value)
            if select['ref'] == 'itemId':
                key_source, key_node = str(int(key)), [0, int(key)]
            else:
                key_source, key_node = expression(key)
            source = f'({ref_source} = {key_source} ? {val_source} : {source})'
            node = [18, [12, copy.deepcopy(ref_node), key_node], val_node, node]
        return source, node
    raise ValueError(f'Unknown translation expression: {spec}')


def localized_strings(spec):
    if isinstance(spec, str):
        yield spec
    elif 'concat' in spec:
        for v in spec['concat']:
            yield from localized_strings(v)
    elif 'select' in spec:
        for v in spec['select']['choices'].values():
            yield from localized_strings(v)
        yield from localized_strings(spec['select']['fallback'])


class Build:
    def __init__(self):
        self.catalog = read_json(ROOT / 'localization/ko.json')
        self.lock = read_json(ROOT / 'upstream.lock.json')
        self.original = read_json(ROOT / 'upstream/web/data.js')
        self.data = copy.deepcopy(self.original)
        self.actions = {str(v[3]): v for v in arrays(self.data) if len(v) == 6 and isinstance(v[3], int) and v[3] > 10**10 and isinstance(v[5], list)}
        self.events = {str(v[4]): v for v in arrays(self.data) if len(v) >= 7 and v[0] == 0 and isinstance(v[4], int) and v[4] > 10**10}
        self.trees = {}
        self.changed_sids = set()
        self.changed_files = set()
        self.fonts = {}
        self.glyph_sources = {}

    def inputs(self):
        for name, digest in self.lock['files'].items():
            path = ROOT / name
            require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest, f'Upstream checksum mismatch: {name}')
        for name in ['source', 'dist']:
            target = (ROOT / name).resolve()
            require(target.parent == ROOT.resolve() and not target.is_symlink(), 'Unsafe output directory')
            if target.exists():
                shutil.rmtree(target)
        shutil.copytree(ROOT / 'upstream/source', SOURCE)
        shutil.copytree(ROOT / 'upstream/web', DIST)

    def tree(self, name):
        if name not in self.trees:
            self.trees[name] = ET.parse(SOURCE / name, ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
        return self.trees[name]

    def patch_text(self):
        self.check_catalog_coverage()
        for entry in self.catalog['entries']:
            path = f"Event sheets/{entry['sheet']}.xml"
            action = self.tree(path).find(f".//action[@sid='{entry['sid']}']")
            require(action is not None, f"Missing source action {entry['sid']}")
            param = action.find(f"param[@id='{entry['param']}']")
            parts = (param.text or '').split('{###}')
            position = entry.get('arg', 0)
            require(parts[position] == entry['original'], f"Source text mismatch: {entry['sid']}")
            source_expr, compiled = expression(entry['ko'])
            parts[position] = source_expr
            param.text = '{###}'.join(parts)
            runtime_action = self.actions.get(entry['sid'])
            if runtime_action is None:
                require(entry['sid'] in {'759843657247084', '708135348105202'}, 'Unexpected absent runtime action')
            elif 'arg' in entry:
                require(runtime_action[5][1][0] == 13, 'Expected variadic function arguments')
                runtime_action[5][1][entry['arg'] + 1][1] = compiled
            else:
                runtime_action[5][entry['param']][1] = compiled
            self.changed_sids.add(entry['sid'])

        variable = self.tree('Event sheets/Globals.xml').find(".//variable[@name='Name']")
        require(variable.text == 'Chara', 'Unexpected player name')
        variable.text = self.catalog['player_name']
        found = 0
        for node in arrays(self.data):
            if len(node) == 8 and node[0:2] == [1, 'Name'] and node[6] == 8522084662131715:
                require(node[3] == 'Chara', 'Unexpected runtime player name')
                node[3] = self.catalog['player_name']
                found += 1
        require(found == 1, 'Player name declaration not found')

        for filename, translations in self.catalog['csv'].items():
            for base in [SOURCE / 'Files', DIST]:
                path = base / filename
                rows = list(csv.reader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
                seen = set()
                for row in rows:
                    if len(row) >= 3 and row[1] == 'SansText' and row[2] in translations:
                        seen.add(row[2])
                        row[2] = translations[row[2]]
                require(seen == set(translations), f'CSV dialogue mismatch in {filename}')
                with path.open('w', encoding='utf-8', newline='') as f:
                    csv.writer(f, lineterminator='\n').writerows(rows)

    def check_catalog_coverage(self):
        """Require catalog entries for literal text at the game's display sinks."""
        keys = {(e['sheet'], e['sid'], e['param'], e.get('arg', 0)) for e in self.catalog['entries']}
        require(len(keys) == len(self.catalog['entries']), 'Duplicate translation target')
        for path in (ROOT / 'upstream/source/Event sheets').glob('*.xml'):
            for action in ET.parse(path).iter('action'):
                params = {int(p.get('id')): p.text or '' for p in action}
                candidates = []
                if action.get('name') in {'Set text', 'Append text'}:
                    candidates.append((0, 0, params[0]))
                elif action.get('name') == 'Set value' and params.get(0) in {'FullText', 'InfoText'}:
                    candidates.append((1, 0, params[1]))
                elif action.get('name') == 'Call function':
                    slots = {'"SansText"': [0], '"CreateMenuItem"': [4], '"RegisterItem"': [2, 3], 'PanicFunc': [0]}.get(params.get(0), [])
                    arguments = params.get(1, '').split('{###}')
                    candidates.extend((1, slot, arguments[slot]) for slot in slots)
                for param, arg, value in candidates:
                    literals = re.findall(r'"((?:[^"\n]|"")*)"', value)
                    words = set(re.findall('[A-Za-z]+', ' '.join(literals)))
                    if words - {'HP', 'LV', 'KR', 'ATK', 'DEF'}:
                        require((path.stem, action.get('sid'), param, arg) in keys,
                                f'Missing display translation: {path.stem}/{action.get("sid")}/{arg}')

    def reference_glyph(self, name, char):
        if name not in self.glyph_sources:
            image = Image.open(ROOT / 'reference/fonts' / f'{name}.png').convert('RGBA')
            lines = (ROOT / 'reference/fonts' / f'glyphs_{name}.csv').read_text(encoding='utf-8-sig').splitlines()[1:]
            glyphs = {chr(int(parts[0])): [int(n) for n in parts[1:]] for parts in (line.split(';') for line in lines) if parts}
            self.glyph_sources[name] = image, glyphs
        image, glyphs = self.glyph_sources[name]
        if char not in glyphs:
            require(name != 'fnt_maintext', f'Missing reference glyph U+{ord(char):04X}: {char}')
            return self.reference_glyph('fnt_maintext', char)
        x, y, width, height, advance, offset = glyphs[char]
        glyph = image.crop((x, y, x + width, y + height))
        white = Image.new('RGBA', glyph.size, 'white')
        white.putalpha(glyph.getchannel('A'))
        return white, max(width + max(0, offset), advance), max(0, offset)

    def make_fonts(self):
        # Include all characters used by the shipped translation; preserve original ASCII.
        text = ''.join(s for e in self.catalog['entries'] for s in localized_strings(e['ko']))
        text += self.catalog['player_name'] + ''.join(self.catalog['buttons'].values())
        text += ''.join(s for d in self.catalog['csv'].values() for s in d.values())
        extra = ''.join(sorted(set(text) - set(chr(i) for i in range(32, 127)) - {'\n', '\r'}))
        font_tree = self.tree('Event sheets/Fonts.xml')
        templates = {}
        for filename in ['Layouts/System.xml', 'Layouts/BattleScreen.xml']:
            for inst in self.tree(filename).iter('instance'):
                if inst.get('type') in FONT_IDS:
                    templates.setdefault(inst.get('type'), inst.find('properties'))

        for name, object_id in FONT_IDS.items():
            old_props = templates[name]
            ow, oh = int(old_props.findtext('character-width')), int(old_props.findtext('character-height'))
            old_charset = old_props.findtext('character-set')
            old_image = Image.open(ROOT / 'upstream/source/Textures' / (name + '.png')).convert('RGBA')
            old_widths = {c: ow for c in old_charset}
            blocks = [b for b in font_tree.iter('event-block') if b.find(f"actions/action[@type='{name}']") is not None]
            require(len(blocks) == 1, f'Unexpected width rule structure for {name}')
            actions_element = blocks[0].find('actions')
            for a in actions_element:
                chars = a.find("param[@id='0']").text[1:-1].replace('""', '"')
                width = int(a.find("param[@id='1']").text)
                for c in chars:
                    old_widths[c] = width
                self.changed_sids.add(a.get('sid'))

            # Replace width actions with complete, deterministic width groups.
            prototype = copy.deepcopy(actions_element[0])
            runtime_prototype = copy.deepcopy(self.actions[prototype.get('sid')])
            for a in list(actions_element):
                actions_element.remove(a)
            runtime_event = self.events[blocks[0].get('sid')]
            runtime_event[6] = []
            charset = ''.join(chr(i) for i in range(32, 127)) + extra
            cw, ch = CELLS[name]
            width = 2 ** math.ceil(math.log2(cw * 24))
            columns = width // cw
            height = 2 ** math.ceil(math.log2(math.ceil(len(charset) / columns) * ch))
            atlas = Image.new('RGBA', (width, height), (0, 0, 0, 0))
            widths = {}
            for i, char in enumerate(charset):
                offset = 0
                if char in old_charset:
                    j = old_charset.index(char)
                    old_columns = old_image.width // ow
                    tile = old_image.crop(((j % old_columns) * ow, (j // old_columns) * oh, (j % old_columns + 1) * ow, (j // old_columns + 1) * oh))
                    advance = old_widths[char]
                    if name == 'BattleFont':
                        tile = tile.resize((ow * 3, oh * 3), Image.Resampling.NEAREST)
                        advance *= 3
                else:
                    ref_name = 'fnt_comicsans' if name == 'SansFont' else 'fnt_maintext'
                    tile, advance, offset = self.reference_glyph(ref_name, char)
                    if name == 'DamageFont':
                        tile = tile.resize((tile.width * 2, tile.height * 2), Image.Resampling.NEAREST)
                        advance *= 2
                        offset *= 2
                require(tile.width + offset <= cw and tile.height <= ch, f'{name}: glyph does not fit: {char} ({tile.size})')
                x, y = i % columns * cw + offset, i // columns * ch
                if name == 'BattleFont' and char not in old_charset:
                    y += (ch - tile.height) // 2
                atlas.alpha_composite(tile, (x, y))
                widths[char] = advance
            groups = defaultdict(str)
            for char, advance in widths.items():
                groups[advance] += char
            for i, (advance, chars) in enumerate(sorted(groups.items())):
                sid = str(8100000000000000 + object_id * 100 + i)
                new_action = copy.deepcopy(prototype)
                new_action.set('sid', sid)
                new_action.find("param[@id='0']").text = expression(chars)[0]
                new_action.find("param[@id='1']").text = str(advance)
                actions_element.append(new_action)
                new_runtime = copy.deepcopy(runtime_prototype)
                new_runtime[3] = int(sid)
                new_runtime[5] = [[1, [2, chars]], [0, [0, advance]]]
                runtime_event[6].append(new_runtime)
            atlas.save(SOURCE / 'Textures' / (name + '.png'))
            atlas.save(DIST / 'images' / (name.lower() + '.png'))
            self.fonts[name] = dict(cell=[cw, ch], charset=charset, widths=widths, image=[width, height])

        for filename in ['Layouts/System.xml', 'Layouts/BattleScreen.xml']:
            layout = self.tree(filename)
            for inst in layout.iter('instance'):
                name = inst.get('type')
                if name not in FONT_IDS:
                    continue
                props = inst.find('properties')
                font = self.fonts[name]
                props.find('character-width').text = str(font['cell'][0])
                props.find('character-height').text = str(font['cell'][1])
                props.find('character-set').text = font['charset']
                if name == 'BattleFont':
                    props.find('scale').text = '1'
                text = props.find('text')
                text.text = {'Comic Sans': '', 'Informational Text': '', 'CHARA LV19': self.catalog['player_name'].upper() + ' LV19'}.get(text.text, text.text)
                for runtime_layout in self.data['project'][5]:
                    if runtime_layout[0] != layout.getroot().findtext('name'):
                        continue
                    for layer in runtime_layout[6]:
                        for runtime_inst in layer[14]:
                            if runtime_inst[2] == int(inst.get('uid')) and runtime_inst[1] == FONT_IDS[name]:
                                rp = runtime_inst[5]
                                rp[0:3] = [*font['cell'], font['charset']]
                                rp[3] = text.text or ''
                                rp[4] = float(props.findtext('scale'))

    def make_buttons(self):
        reference = Image.open(ROOT / 'reference/EmbeddedTextures/2.png').convert('RGBA')
        for name, text in self.catalog['buttons'].items():
            obj = self.data['project'][3][{'UIAct': 10, 'UIFight': 11, 'UIItem': 12, 'UIMercy': 13}[name]]
            atlas_path = DIST / obj[7][0][7][0][0]
            atlas = Image.open(atlas_path).convert('RGBA')
            for state_index, state in enumerate(['Default', 'Highlight']):
                path = SOURCE / 'Animations' / name / state / '000.png'
                # Reuse Team Waldo's exact Korean UI artwork. The texture pack has
                # 2px extrusion around each frame; crop the original 110x42 interior.
                x, y, w, h = self.catalog['button_crops'][name][state]
                button = reference.crop((x, y, x + w, y + h))
                button.save(path)
                frame = obj[7][state_index][7][0]
                require(button.size == tuple(frame[4:6]), 'Button frame size changed')
                atlas.paste(button, (frame[2], frame[3]))
            atlas.save(atlas_path)

    def package(self):
        project = self.tree('Bad Time Simulator (Sans Fight).caproj')
        project.find('name').text = self.catalog['title']
        project.find('description').text = self.catalog['description']
        self.data['project'][26] = self.catalog['title']
        for filename, tree in self.trees.items():
            ET.indent(tree, space='    ')
            tree.write(SOURCE / filename, encoding='utf-8', xml_declaration=True)

        # Refresh export byte-count metadata after replacing sprite/font PNGs.
        for node in arrays(self.data):
            if len(node) >= 2 and isinstance(node[0], str) and node[0].startswith('images/') and isinstance(node[1], int):
                node[1] = (DIST / node[0]).stat().st_size
        (DIST / 'data.js').write_text(json.dumps(self.data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8', newline='\n')
        index = (DIST / 'index.html').read_text(encoding='utf-8-sig')
        index = index.replace('<html>', '<html lang="ko">')
        index = re.sub(r'<title>.*?</title>', '<title>' + html.escape(self.catalog['title']) + '</title>', index)
        index = re.sub(r'<meta name="description" content=".*?" />', '<meta name="description" content="' + html.escape(self.catalog['description'], quote=True) + '" />', index)
        index = re.sub(r'\s*<!-- Google tag.*?</script>\s*<script>.*?</script>', '', index, flags=re.S)
        index = index.replace("Exported games won't work until you upload them. (When running on the file:/// protocol, browsers block many features from working for security reasons.)", '이 게임은 웹 서버에서 실행해야 합니다. README의 로컬 실행 안내를 확인해 주세요.')
        index = re.sub(r'<h1>Your browser.*?</h1>', '<h1>이 브라우저는 HTML5를 지원하지 않습니다.<br>최신 Chrome, Edge 또는 Firefox로 접속해 주세요.</h1>', index, flags=re.S)
        (DIST / 'index.html').write_text(index, encoding='utf-8', newline='\n')
        manifest = read_json(DIST / 'appmanifest.json')
        manifest.update(name=self.catalog['title'], short_name=self.catalog['title'], lang='ko', start_url='./index.html', scope='./')
        write_json(DIST / 'appmanifest.json', manifest)
        sw = (DIST / 'sw.js').read_text(encoding='utf-8-sig').replace('const CACHE_NAME_PREFIX = "c2offline";', 'const CACHE_NAME_PREFIX = "c2-sans-fight-kr";')
        (DIST / 'sw.js').write_text(sw, encoding='utf-8', newline='\n')
        (DIST / '.nojekyll').write_text('', encoding='utf-8')
        offline = read_json(DIST / 'offline.js')
        version_hash = hashlib.sha256()
        for path in sorted(DIST.rglob('*')):
            if path.is_file() and path.name not in {'offline.js', '.nojekyll'}:
                version_hash.update(path.relative_to(DIST).as_posix().encode())
                version_hash.update(path.read_bytes())
        offline['version'] = int(version_hash.hexdigest()[:12], 16)
        offline['fileList'] = sorted(p.relative_to(DIST).as_posix() for p in DIST.rglob('*') if p.is_file() and p.name not in {'offline.js', '.nojekyll', 'sw.js'})
        write_json(DIST / 'offline.js', offline)
        self.version = version_hash.hexdigest()[:12]

    def validate(self):
        require((DIST / 'c2runtime.js').read_bytes() == (ROOT / 'upstream/web/c2runtime.js').read_bytes(), 'Engine was modified')
        for filename in (ROOT / 'upstream/source/Files').glob('*.csv'):
            original = list(csv.reader(io.StringIO(filename.read_text(encoding='utf-8-sig'))))
            result = list(csv.reader(io.StringIO((SOURCE / 'Files' / filename.name).read_text(encoding='utf-8-sig'))))
            require(len(original) == len(result), f'Changed attack row count: {filename.name}')
            for before, after in zip(original, result):
                if before != after:
                    require(filename.name in self.catalog['csv'] and before[1] == after[1] == 'SansText' and before[:2] + before[3:] == after[:2] + after[3:], f'Changed attack behavior: {filename.name}')
            web_result = list(csv.reader(io.StringIO((DIST / filename.name).read_text(encoding='utf-8-sig'))))
            require(web_result == result, f'Source/web attack mismatch: {filename.name}')
        # Removing only explicitly allowed display changes must recover all original events.
        before_sheets = copy.deepcopy(self.original['project'][6])
        after_sheets = copy.deepcopy(self.data['project'][6])
        def redact(sheets):
            for sheet in sheets:
                for node in arrays(sheet):
                    if len(node) == 6 and isinstance(node[3], int) and str(node[3]) in self.changed_sids:
                        node[5] = ['localized']
                    if len(node) == 8 and node[0:2] == [1, 'Name']:
                        node[3] = 'localized'
                if sheet[0] == 'Fonts':
                    for node in arrays(sheet):
                        if len(node) >= 7 and node[0] == 0 and isinstance(node[4], int) and str(node[4]) in self.events:
                            node[6] = []
        redact(before_sheets)
        redact(after_sheets)
        require(before_sheets == after_sheets, 'Unexpected non-display event change')
        for filename in SOURCE.rglob('*.xml'):
            ET.parse(filename)
        ET.parse(SOURCE / 'Bad Time Simulator (Sans Fight).caproj')
        for name, font in self.fonts.items():
            require(len(font['charset']) == len(set(font['charset'])), f'Duplicate glyph in {name}')
            for char, advance in font['widths'].items():
                require(advance <= font['cell'][0], f'Glyph width exceeds cell: {name}/{char}')
        for filename in read_json(DIST / 'offline.js')['fileList']:
            require((DIST / filename).is_file(), f'Missing cache resource: {filename}')
        require('googletagmanager' not in (DIST / 'index.html').read_text(encoding='utf-8'), 'Original analytics remained')
        return {'version': self.version, 'translation_entries': len(self.catalog['entries']), 'fonts': self.fonts, 'engine_unchanged': True, 'attack_behavior_unchanged': True, 'event_behavior_unchanged': True}

    def run(self):
        self.inputs()
        self.patch_text()
        self.make_fonts()
        self.make_buttons()
        self.package()
        report = self.validate()
        (ROOT / 'test-results').mkdir(exist_ok=True)
        write_json(ROOT / 'test-results/build-report.json', report)
        print(f"Built Korean source and site: {len(self.catalog['entries'])} translations, version {self.version}")
        print('PASS: source checksums, source/web parity, glyph coverage, engine/event/attack invariants, resource paths')


if __name__ == '__main__':
    Build().run()
