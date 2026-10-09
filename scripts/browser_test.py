"""Browser integration checks. Test hooks stay in the browser test, never the site."""
import argparse
import csv
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
FONT_STATE = """() => cr_getC2Runtime().p.slice(47,51).flatMap((t,n)=>t.d.map(i=>({
  font:n,text:i.text,full:i.hb[2],visible:i.visible,x:i.x,y:i.y,width:i.width,height:i.height,
  textWidth:i.Qg,textHeight:i.Gf,scale:i.rd,align:i.xh,lines:i.zf.map(l=>l.text),
  missing:[...i.text].filter(c=>c!=='\\n'&&!i.characterSet.includes(c))
})))"""


def run(args):
    out = ROOT / 'test-results' / args.channel
    out.mkdir(parents=True, exist_ok=True)
    report = {'channel': args.channel, 'url': args.base_url, 'checks': [], 'screenshots': []}
    errors = []
    bad_responses = []
    with sync_playwright() as p:
        # Use a clean browser profile and connect directly to the test server.
        # This does not change the user's browser or OS proxy configuration.
        launch = {'headless': True, 'args': ['--no-proxy-server']}
        if args.channel != 'chromium':
            launch['channel'] = args.channel
        browser = p.chromium.launch(**launch)
        report['version'] = browser.version
        context = browser.new_context(viewport={'width': 1280, 'height': 960})
        page = context.new_page()
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('response', lambda r: bad_responses.append([r.status, r.url]) if r.status >= 400 or r.status == 204 else None)

        def check(name, condition=True):
            if not condition:
                raise AssertionError(name)
            report['checks'].append(name)
            if not any(metric in name for metric in ['glyphs/', 'text height/', 'text width/']):
                print('PASS', name, flush=True)

        def press(key):
            page.keyboard.press(key, delay=90)
            page.wait_for_timeout(100)

        def call(name, *params):
            page.evaluate('([name,params])=>c2_callFunction(name,params)', [name, list(params)])
            page.wait_for_timeout(120)

        def wait_text(text, full=False):
            page.wait_for_function("([text,full])=>window.cr_getC2Runtime && cr_getC2Runtime().p.slice(47,51).some(t=>t.d.some(i=>(full?i.hb[2]:i.text).includes(text)))", arg=[text, full], timeout=20000)

        def screenshot(name):
            page.wait_for_timeout(120)
            page.screenshot(path=str(out / (name + '.png')))
            report['screenshots'].append(name + '.png')
            state = page.evaluate(FONT_STATE)
            (out / (name + '.json')).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
            for i in state:
                check(f'{name}: glyphs/{i["font"]}/{i["text"][:12]}', not i['missing'])
                if i['visible'] and i['text']:
                    check(f'{name}: text height/{i["text"][:12]}', i['textHeight'] <= i['height'] + 1)
                    check(f'{name}: text width/{i["text"][:12]}', i['textWidth'] <= i['width'] + 1)
            return state

        def home():
            page.goto(args.base_url, wait_until='load')
            wait_text('일반')

        def start_normal():
            home()
            press('z')
            wait_text('준비됐어?', full=True)
            press('x')
            press('z')
            page.wait_for_timeout(200)

        def battle_menu():
            # Advance a battle to its normal end-of-attack handler for UI coverage.
            # No test hook or changed gameplay data is shipped in dist/.
            call('TLPause')
            call('EndAttack')
            page.wait_for_timeout(800)

        def select_battle_button(target):
            current = page.evaluate('cr_getC2Runtime().p[61].d[0].Fb().data[0][0][0]')
            for _ in range((target - current) % 4):
                press('ArrowRight')
            press('z')

        home()
        check('Korean document language', page.locator('html').get_attribute('lang') == 'ko')
        check('Korean title', '한글판' in page.title())
        screenshot('main-menu')

        # Exercise keyboard navigation and the mapping from displayed labels to attack IDs.
        for _ in range(3):
            press('ArrowDown')
        press('z')
        wait_text('공격을 선택하세요')
        single = screenshot('attack-list')
        check('24 Korean attack labels', len([i for i in single if i['font'] == 2]) == 25)
        check('Internal attack IDs are not shown', not any('sans_' in i['text'] for i in single))
        press('z')
        page.wait_for_function("cr_getC2Runtime().ba.name==='BattleScreen'")
        check('Single attack starts')
        page.wait_for_timeout(800)
        screenshot('single-attack')
        press('x')
        wait_text('일반')
        check('Single attack quit returns to menu')

        call('MenuModeEndless')
        wait_text('1단계')
        screenshot('endless-menu')
        press('z')
        page.wait_for_function("cr_getC2Runtime().ba.name==='BattleScreen'")
        check('Endless mode starts')

        # Practice uses its original success/failure handlers and the same damage rules.
        home()
        press('ArrowDown')
        press('z')
        wait_text('준비됐어?', full=True)
        press('x')
        press('z')
        call('TLPause')
        call('EndAttack')
        wait_text('성공')
        screenshot('practice-success')
        check('Practice success is displayed in Korean')

        home()
        press('ArrowDown')
        press('z')
        wait_text('준비됐어?', full=True)
        press('x')
        press('z')
        call('DamagePlayer', 50, 0)
        wait_text('실패')
        screenshot('practice-failure')
        check('Practice damage threshold triggers Korean failure')

        # Native browser file chooser and the existing custom CSV loader.
        home()
        call('MenuModeCustom')
        screenshot('custom-menu')
        call('MenuCustomSelect')
        page.locator('input[type=file]').set_input_files(str(ROOT / 'upstream/web/sans_bluebone.csv'))
        wait_text('공격 파일을 불러왔습니다.')
        screenshot('custom-loaded')
        call('MenuCustomRun')
        page.wait_for_function("cr_getC2Runtime().ba.name==='BattleScreen'")
        check('Custom attack file loads and starts')

        for name, csv_text, expected in [
            ('missing-label', '0,JMPABS,missing\n', '라벨 없음'),
            ('infinite-loop', '0,:loop\n0,JMPABS,loop\n', '무한 반복 감지'),
        ]:
            home()
            call('MenuModeCustom')
            call('MenuCustomSelect')
            page.locator('input[type=file]').set_input_files({
                'name': name + '.csv', 'mimeType': 'text/csv', 'buffer': csv_text.encode(),
            })
            wait_text('공격 파일을 불러왔습니다.')
            call('MenuCustomRun')
            wait_text(expected)
            screenshot('error-' + name)
            check('Korean diagnostic: ' + name)

        start_normal()
        # Intro text skipping and a real keyboard input have already advanced the timeline.
        battle_menu()
        press('x')
        state = screenshot('battle-menu')
        check('Original English player name retained', any('CHARA' in i['text'] for i in state))
        select_battle_button(1)
        wait_text('* 샌즈')
        press('z')
        wait_text('* 살펴보기')
        press('z')
        wait_text('가장 쉬운 적.', full=True)
        press('x')
        wait_text('1 대미지만 줄 수 있다.')
        screenshot('check-sans')
        check('ACT and typewriter skip work')

        # Reach late battle information through the original end-of-attack selector.
        # Only the test profile changes the current battle stage to avoid a full playthrough.
        for stage, next_attack, expected, name in [
            (0, 1, '기분이 든다.', 'bad-time'),
            (15, 0, '드디어 시작된다.', 'real-battle'),
            (19, 0, '시간 낭비인 것 같다.', 'reading'),
            (20, 0, '보이기 시작했다.', 'tired'),
            (21, 0, '뭔가 준비하고 있다.', 'preparing'),
            (22, 0, '필살기를 사용할', 'special-attack'),
        ]:
            start_normal()
            battle_menu()
            page.evaluate('([stage,next])=>{const s=cr_getC2Runtime().p[25].d[0];s.hb[1]=stage;s.hb[0]=next}', [stage, next_attack])
            battle_menu()
            wait_text(expected, full=True)
            press('x')
            page.wait_for_function("expected=>cr_getC2Runtime().p[49].d.some(i=>i.hb[2].includes(expected)&&i.text===i.hb[2])", arg=expected)
            screenshot('reference-' + name)
        check('Reference battle descriptions fit their display area')

        # Render the localized line from the shipped attack CSV through the existing bubble.
        start_normal()
        call('TLPause')
        with (ROOT / 'source/Files/sans_intro.csv').open(encoding='utf-8', newline='') as f:
            intro_lines = [row[2] for row in csv.reader(f) if len(row) > 2 and row[1] == 'SansText']
        call('SansText', intro_lines[-1])
        wait_text('그럼 간다.', full=True)
        press('x')
        wait_text('그럼 간다.')
        screenshot('intro-here-we-go')
        check('Reference intro dialogue displays from attack CSV')

        start_normal()
        battle_menu()
        select_battle_button(0)
        wait_text('* 샌즈')
        press('z')
        page.wait_for_timeout(200)
        press('z')
        wait_text('빗나감')
        screenshot('attack-miss')
        check('Fight input and Korean miss indicator work')

        # Reload between independent battle scenarios to avoid bypassing dialogue cleanup.
        start_normal()
        battle_menu()
        select_battle_button(2)
        wait_text('1쪽')
        screenshot('items-page1')
        press('ArrowRight')
        press('ArrowRight')
        wait_text('2쪽')
        screenshot('items-page2')
        check('Item pages navigate with keyboard')

        start_normal()
        battle_menu()
        for item_name in ['버터스카치 파이', '컵라면', '얼굴 스테이크', '전설의 히어로']:
            select_battle_button(2)
            press('z')
            wait_text(item_name, full=True)
            press('x')
            wait_text('회복했다!')
            screenshot('item-use-' + str(len([x for x in report['screenshots'] if x.startswith('item-use-')])))
            check(item_name + ': Korean sentence and healing value')
            # Confirm finishes the dialog and starts the next attack; end it via normal handler.
            press('z')
            battle_menu()

        # The original inventory has eight entries: consume the remaining four heroes.
        for _ in range(4):
            select_battle_button(2)
            press('z')
            wait_text('회복했다!', full=True)
            press('x')
            press('z')
            battle_menu()
        select_battle_button(2)
        check('Empty inventory returns safely', not any('쪽' in i['text'] for i in page.evaluate(FONT_STATE)))

        select_battle_button(3)
        wait_text('* 살려주기')
        screenshot('mercy')
        check('Mercy menu translated')

        # Validate the original damage/death path and keyboard restart/quit.
        start_normal()
        call('DamagePlayer', 999, 0)
        page.wait_for_timeout(1100)
        screenshot('death')
        wait_text('일반')
        check('Death returns to main menu')
        press('z')
        wait_text('준비됐어?', full=True)
        check('Game restarts after death')

        # Check the existing breath/victory dialogue chain without playing every attack.
        start_normal()
        page.evaluate('cr_getC2Runtime().p[25].d[0].hb[1]=23')
        battle_menu()
        wait_text('헉... 헉...', full=True)
        press('x')
        wait_text('헉... 헉...')
        screenshot('victory-breath')
        press('z')
        wait_text('네가 이긴 것 같네.', full=True)
        press('x')
        screenshot('victory')
        press('z')
        wait_text('일반')
        check('Victory dialogue returns to main menu')

        if not args.skip_audio:
            filenames = [p.name for p in (ROOT / 'upstream/web/media').glob('*.ogg')]
            audio = page.evaluate("""async filenames => {
                const c = new AudioContext(), results=[];
                for (const name of filenames) {
                    const r=await fetch('media/'+name);
                    const b=await c.decodeAudioData(await r.arrayBuffer());
                    results.push({name,status:r.status,duration:b.duration});
                }
                await c.close(); return results;
            }""", filenames)
            check('All 18 original sounds decode', len(audio) == 18 and all(r['status'] == 200 and r['duration'] > 0 for r in audio))
            report['audio'] = audio

        page.evaluate('navigator.serviceWorker.ready')
        page.wait_for_function("navigator.serviceWorker.controller!==null", timeout=15000)
        page.wait_for_timeout(1200)
        await_cache = page.evaluate("caches.keys()")
        check('Offline cache is scoped to Korean edition', any('c2-sans-fight-kr' in k for k in await_cache))
        page.reload(wait_until='load')
        wait_text('일반')
        check('Reload works with service worker')
        context.set_offline(True)
        page.reload(wait_until='load')
        wait_text('일반')
        check('Offline reload works after first load')
        context.set_offline(False)

        check('No browser exceptions', not errors)
        check('No missing or empty HTTP responses', not bad_responses)
        report.update(errors=errors, bad_responses=bad_responses, result='passed')
        (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
    print('ALL BROWSER CHECKS PASSED', args.channel, flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8765/')
    parser.add_argument('--channel', default='chrome', choices=['chrome', 'msedge', 'chromium'])
    parser.add_argument('--skip-audio', action='store_true')
    run(parser.parse_args())
