/* JSDOM acceptance test for the rebrand + per-URL meta PR.
 *
 * Covers:
 *   - h1 + initial <title> reflect the new brand
 *   - document.title + meta description + OG/Twitter tags update on each
 *     dashboard route variant (default, single season, range, position,
 *     tag, sort-only)
 *   - Player view titles adapt to (measurements, workouts) combination
 *   - Multi-combine season toggle re-renders the meta with the new year
 *   - OG/Twitter tags mirror title/description on every state
 *
 * Run: node tests/dynamic_meta.test.js
 */

const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = path.resolve(__dirname, '..');
const RAW_HTML = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
const DATA = fs.readFileSync(path.join(ROOT, 'data/combine.json'), 'utf8');

const EXPORT_SHIM = `<script>
    window.__appReady = (async () => {
        while (typeof state === 'undefined' || !state.data) {
            await new Promise(r => setTimeout(r, 10));
        }
        window.state = state;
        return state;
    })();
</script>`;
const HTML = RAW_HTML.replace('</body>', EXPORT_SHIM + '</body>');

let passed = 0;
let failed = 0;

function ok(name, cond, detail) {
    if (cond) { passed++; console.log(`  ✓ ${name}`); }
    else      { failed++; console.error(`  ✗ ${name}${detail ? ' — ' + detail : ''}`); }
}
function eq(name, actual, expected) {
    ok(name, actual === expected, `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
}

async function bootDom() {
    const dom = new JSDOM(HTML, {
        url: 'https://example.com/nba-draft-combine/',
        runScripts: 'dangerously',
        pretendToBeVisual: true,
        beforeParse(window) {
            window.fetch = (url) => {
                if (typeof url === 'string' && url.includes('combine.json')) {
                    return Promise.resolve({
                        ok: true, status: 200,
                        json: () => Promise.resolve(JSON.parse(DATA)),
                    });
                }
                return Promise.reject(new Error('Unexpected fetch: ' + url));
            };
            if (!window.requestAnimationFrame) {
                window.requestAnimationFrame = (fn) => window.setTimeout(fn, 0);
            }
            window.scrollTo = () => {};
        },
    });
    await dom.window.__appReady;
    return dom;
}

function navigateTo(dom, search) {
    dom.window.history.pushState({}, '', search);
    dom.window.routeFromUrl({ fromPopstate: false });
}

function metaContent(window, selector) {
    const el = window.document.head.querySelector(selector);
    return el ? el.getAttribute('content') : null;
}

function snapshot(window) {
    return {
        title: window.document.title,
        description: metaContent(window, 'meta[name="description"]'),
        ogTitle: metaContent(window, 'meta[property="og:title"]'),
        ogDesc: metaContent(window, 'meta[property="og:description"]'),
        ogUrl: metaContent(window, 'meta[property="og:url"]'),
        twitterTitle: metaContent(window, 'meta[name="twitter:title"]'),
        twitterDesc: metaContent(window, 'meta[name="twitter:description"]'),
    };
}

function assertMirroring(window, label) {
    const s = snapshot(window);
    eq(`${label}: og:title mirrors title`,     s.ogTitle, s.title);
    eq(`${label}: og:description mirrors`,     s.ogDesc, s.description);
    eq(`${label}: twitter:title mirrors`,      s.twitterTitle, s.title);
    eq(`${label}: twitter:description mirrors`, s.twitterDesc, s.description);
    ok(`${label}: og:url present and absolute`,
        !!s.ogUrl && s.ogUrl.startsWith('http'),
        JSON.stringify(s.ogUrl));
}

async function run() {
    console.log('\n— Static brand: h1, default <title>');
    {
        const dom = await bootDom();
        const { window } = dom;
        eq('h1 reads NBA Draft Prospect Central',
            window.document.querySelector('h1').textContent.trim(),
            'NBA Draft Prospect Central');
        eq('initial title is brand',
            window.document.title,
            'NBA Draft Prospect Central | HoopsMatic');
        eq('initial description is default',
            metaContent(window, 'meta[name="description"]'),
            'Explore measurements, athletic scores, shooting drills, and historical comparisons for every NBA Draft Combine attendee since 2000.');
        assertMirroring(window, 'initial');
        dom.window.close();
    }

    console.log('\n— Dashboard variants');
    {
        // applyDeepLink only resets multi/tags/workoutTeams; bucket and
        // sort leak across navigations. Fresh DOM per variant ensures the
        // route-specific URL is the only signal in play.
        async function check(search, fn) {
            const dom = await bootDom();
            navigateTo(dom, search);
            fn(dom.window);
            dom.window.close();
        }

        await check('/nba-draft-combine/', (w) => {
            eq('default title', w.document.title, 'NBA Draft Prospect Central | HoopsMatic');
            ok('default desc starts with Explore...',
                (metaContent(w, 'meta[name="description"]') || '').startsWith('Explore'));
            assertMirroring(w, 'default');
        });

        await check('/nba-draft-combine/?season=2024', (w) => {
            eq('single-season title', w.document.title, '2024 NBA Draft Prospects | HoopsMatic');
            eq('single-season description',
                metaContent(w, 'meta[name="description"]'),
                'Measurements, athletic scores, and shooting drills for prospects from the 2024 NBA Draft Combine.');
            assertMirroring(w, 'single-season');
        });

        await check('/nba-draft-combine/?season=2025', (w) => {
            eq('single-season=2025 title', w.document.title, '2025 NBA Draft Prospects | HoopsMatic');
            assertMirroring(w, 'single-season-2025');
        });

        await check('/nba-draft-combine/?season_start=2020&season_end=2024', (w) => {
            eq('range title', w.document.title, 'NBA Draft Prospects 2020-2024 | HoopsMatic');
            eq('range description',
                metaContent(w, 'meta[name="description"]'),
                'Measurements, athletic scores, and shooting drills for prospects from the 2020-2024 NBA Draft Combines.');
            assertMirroring(w, 'range');
        });

        await check('/nba-draft-combine/?position=Bigs&season=2025', (w) => {
            eq('position+season title', w.document.title, '2025 NBA Draft Prospects: Bigs | HoopsMatic');
            eq('position+season description',
                metaContent(w, 'meta[name="description"]'),
                'Bigs measurements and athletic scores from the 2025 NBA Draft Combine.');
            assertMirroring(w, 'position+season');
        });

        await check('/nba-draft-combine/?tag=long-arms&season=2025', (w) => {
            eq('tag title',
                w.document.title,
                '2025 NBA Draft Prospects with Long arms | HoopsMatic');
            ok('tag description mentions tag label',
                (metaContent(w, 'meta[name="description"]') || '').includes('Long arms'));
            assertMirroring(w, 'tag');
        });

        await check('/nba-draft-combine/?sort=Lane%20Agility%20(sec)', (w) => {
            eq('sort-only title',
                w.document.title,
                'NBA Draft Prospects Sorted by Lane Agility | HoopsMatic');
            ok('sort-only description mentions metric',
                (metaContent(w, 'meta[name="description"]') || '').includes('Lane Agility'));
            assertMirroring(w, 'sort-only');
        });
    }

    console.log('\n— Player view variants');
    {
        const dom = await bootDom();
        const { window } = dom;
        const players = window.state.data.players;

        // Both measurements + workouts (Cooper Flagg 2025)
        const flagg = players.find(p => p.player === 'Cooper Flagg' && p.season === 2025);
        ok('Cooper Flagg 2025 sample found', !!flagg);
        navigateTo(dom, '/nba-draft-combine/?player=cooper-flagg');
        eq('both title',
            window.document.title,
            'Cooper Flagg 2025 NBA Draft Combine Measurements + Workout List | HoopsMatic');
        ok('both description mentions workouts',
            (metaContent(window, 'meta[name="description"]') || '').includes('pre-draft workouts'));
        assertMirroring(window, 'player-both');

        // Workouts-only rescued player (Paolo Banchero 2022)
        navigateTo(dom, '/nba-draft-combine/?player=paolo-banchero');
        eq('workouts-only title',
            window.document.title,
            'Paolo Banchero 2022 NBA Draft Workout List | HoopsMatic');
        eq('workouts-only description',
            metaContent(window, 'meta[name="description"]'),
            "Paolo Banchero's confirmed pre-draft workouts before the 2022 NBA Draft.");
        assertMirroring(window, 'player-workouts');

        // Measurements-only (older player, no workouts)
        navigateTo(dom, '/nba-draft-combine/?player=hasheem-thabeet');
        eq('measurements-only title',
            window.document.title,
            'Hasheem Thabeet 2009 NBA Draft Combine Measurements | HoopsMatic');
        ok('measurements-only description does NOT mention workouts',
            !(metaContent(window, 'meta[name="description"]') || '').includes('workouts'));
        assertMirroring(window, 'player-measurements');

        dom.window.close();
    }

    console.log('\n— Multi-combine season toggle');
    {
        const dom = await bootDom();
        const { window } = dom;

        // Adem Bona — multi-combine. Default load picks most recent (2024).
        navigateTo(dom, '/nba-draft-combine/?player=adem-bona');
        ok('Adem Bona title contains 2024',
            (window.document.title || '').startsWith('Adem Bona 2024 '),
            JSON.stringify(window.document.title));

        // Click the 2023 tab — title should update.
        const tabs = window.document.querySelectorAll('.pv-season-tab');
        const tab2023 = [...tabs].find(t => t.textContent === '2023');
        ok('2023 season tab present', !!tab2023);
        tab2023.click();
        ok('title now starts with Adem Bona 2023',
            (window.document.title || '').startsWith('Adem Bona 2023 '),
            JSON.stringify(window.document.title));
        ok('og:url updated to 2023 canonical',
            (metaContent(window, 'meta[property="og:url"]') || '').includes('season=2023'),
            JSON.stringify(metaContent(window, 'meta[property="og:url"]')));

        dom.window.close();
    }

    console.log('\n— Back-to-dashboard restores brand');
    {
        const dom = await bootDom();
        const { window } = dom;
        // Go to a player...
        navigateTo(dom, '/nba-draft-combine/?player=cooper-flagg');
        ok('on player view', window.document.title.startsWith('Cooper Flagg'));
        // ...then back to dashboard default.
        navigateTo(dom, '/nba-draft-combine/');
        eq('back to dashboard default',
            window.document.title,
            'NBA Draft Prospect Central | HoopsMatic');
        dom.window.close();
    }

    console.log(`\nResults: ${passed} passed, ${failed} failed`);
    if (failed > 0) process.exit(1);
}

run().catch(err => {
    console.error('Test harness error:', err);
    process.exit(1);
});
