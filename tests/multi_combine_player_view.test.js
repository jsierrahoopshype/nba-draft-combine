/* JSDOM acceptance test for the multi-combine player view consolidation.
 *
 * Boots index.html with a stubbed fetch that serves data/combine.json,
 * lets the dashboard finish initial render, then exercises the routing,
 * season-toggle, legacy URL rewrite, missing-season fallback, and comp
 * navigation behaviors against the real DOM.
 *
 * Run: node tests/multi_combine_player_view.test.js
 */

const fs = require('fs');
const path = require('path');
const { JSDOM, ResourceLoader } = require('jsdom');

const ROOT = path.resolve(__dirname, '..');
const RAW_HTML = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
const DATA = fs.readFileSync(path.join(ROOT, 'data/combine.json'), 'utf8');

// Inject a trailing script that re-exports the const-scoped `state` (and
// any other module-scoped bindings we want to assert against) onto window.
// Function declarations like routeFromUrl are already on window via
// classic-script semantics.
const EXPORT_SHIM = `<script>
    window.__appReady = (async () => {
        // Spin until loadData() finishes and state.data is populated.
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
    if (cond) {
        passed++;
        console.log(`  ✓ ${name}`);
    } else {
        failed++;
        console.error(`  ✗ ${name}${detail ? ' — ' + detail : ''}`);
    }
}

function eq(name, actual, expected) {
    const same = (
        actual === expected ||
        (Number.isNaN(actual) && Number.isNaN(expected))
    );
    ok(name, same, `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
}

async function bootDom() {
    const dom = new JSDOM(HTML, {
        url: 'https://example.com/',
        runScripts: 'dangerously',
        pretendToBeVisual: true,
        beforeParse(window) {
            // Stub fetch to serve the bundled JSON synchronously.
            window.fetch = (url) => {
                if (typeof url === 'string' && url.includes('combine.json')) {
                    return Promise.resolve({
                        ok: true,
                        status: 200,
                        json: () => Promise.resolve(JSON.parse(DATA)),
                    });
                }
                return Promise.reject(new Error('Unexpected fetch: ' + url));
            };
            // requestAnimationFrame fallback (pretendToBeVisual covers it, but
            // be defensive).
            if (!window.requestAnimationFrame) {
                window.requestAnimationFrame = (fn) => window.setTimeout(fn, 0);
            }
            // JSDOM doesn't implement scrollTo; silence the warnings.
            window.scrollTo = () => {};
        },
    });

    // The export shim returns a promise that resolves once state.data is
    // populated; wait on that.
    await dom.window.__appReady;

    return dom;
}

// Use history.pushState + invoke routeFromUrl directly to simulate user
// navigation. Avoid relying on popstate firing in JSDOM.
function navigateTo(dom, search) {
    dom.window.history.pushState({}, '', search);
    dom.window.routeFromUrl({ fromPopstate: false });
}

async function run() {
    const dom = await bootDom();
    const { window } = dom;

    console.log('\n— Canonical URL resolution');
    {
        // Adem Bona — multi-combine (2023, 2024). Bare slug → most recent.
        navigateTo(dom, '?player=adem-bona');
        const cp = window.state.currentPlayer;
        ok('bare slug resolves to a record', cp != null);
        eq('player_slug is adem-bona', cp && cp.player_slug, 'adem-bona');
        eq('defaults to most recent season (2024)', cp && cp.season, 2024);
        // URL should have been rewritten to include the season.
        eq('canonical URL has season param', window.location.search, '?player=adem-bona&season=2024');
    }

    console.log('\n— Season toggle');
    {
        // Toggle should be rendered with both seasons.
        const tabs = window.document.querySelectorAll('.pv-season-tab');
        eq('two season tabs rendered', tabs.length, 2);
        const labels = [...tabs].map(t => t.textContent);
        ok('tab labels include 2024 and 2023',
            labels.includes('2024') && labels.includes('2023'),
            JSON.stringify(labels));
        const active = window.document.querySelector('.pv-season-tab.active');
        eq('active tab is 2024', active && active.textContent, '2024');

        // Click the 2023 tab → URL updates, content re-renders, active flips.
        const tab2023 = [...tabs].find(t => t.textContent === '2023');
        tab2023.click();
        eq('URL switched to 2023', window.location.search, '?player=adem-bona&season=2023');
        eq('currentPlayer.season is 2023', window.state.currentPlayer.season, 2023);
        const active2 = window.document.querySelector('.pv-season-tab.active');
        eq('active tab is now 2023', active2 && active2.textContent, '2023');
    }

    console.log('\n— Browser back');
    {
        // history.back() — JSDOM's history supports it. After back we should
        // be on the 2024 URL again. We invoke routeFromUrl manually since
        // JSDOM doesn't always fire popstate listeners reliably.
        window.history.back();
        // Give the queue a tick to settle.
        await new Promise(r => setTimeout(r, 10));
        // Re-route in case popstate didn't fire.
        if (window.state.currentPlayer && window.state.currentPlayer.season !== 2024) {
            window.routeFromUrl({ fromPopstate: true });
        }
        eq('URL back to 2024', window.location.search, '?player=adem-bona&season=2024');
        eq('currentPlayer.season back to 2024', window.state.currentPlayer.season, 2024);
    }

    console.log('\n— Legacy URL rewrite');
    {
        // ?player=adem-bona-2023 (old format) should resolve and rewrite
        // to ?player=adem-bona&season=2023 in-place.
        navigateTo(dom, '?player=adem-bona-2023');
        eq('legacy URL rewritten to canonical',
            window.location.search, '?player=adem-bona&season=2023');
        eq('currentPlayer is Adem Bona 2023',
            window.state.currentPlayer && window.state.currentPlayer.player,
            'Adem Bona');
        eq('currentPlayer.season is 2023',
            window.state.currentPlayer && window.state.currentPlayer.season,
            2023);
    }

    console.log('\n— Slug collision disambiguation (Tony Mitchell)');
    {
        // Earlier season (2012) keeps canonical slug.
        navigateTo(dom, '?player=tony-mitchell');
        eq('tony-mitchell → 2012 record',
            window.state.currentPlayer && window.state.currentPlayer.season, 2012);
        // 2013 namesake disambiguated to -2.
        navigateTo(dom, '?player=tony-mitchell-2');
        eq('tony-mitchell-2 → 2013 record',
            window.state.currentPlayer && window.state.currentPlayer.season, 2013);
        // Legacy URL for 2013 should still resolve to that record.
        navigateTo(dom, '?player=tony-mitchell-2013');
        eq('legacy tony-mitchell-2013 → 2013 record',
            window.state.currentPlayer && window.state.currentPlayer.season, 2013);
        // Both Tony Mitchells are single-combine humans, so no season toggle.
        const tabs = window.document.querySelectorAll('.pv-season-tab');
        eq('no season tabs for single-combine player', tabs.length, 0);
    }

    console.log('\n— Single-combine player (no toggle)');
    {
        // Pick any player who only attended one combine.
        const players = window.state.data.players;
        const slugCounts = new Map();
        for (const p of players) {
            slugCounts.set(p.player_slug, (slugCounts.get(p.player_slug) || 0) + 1);
        }
        const single = players.find(p => slugCounts.get(p.player_slug) === 1);
        navigateTo(dom, '?player=' + single.player_slug);
        const tabs = window.document.querySelectorAll('.pv-season-tab');
        eq('no season tabs', tabs.length, 0);
        // Single-combine URL omits season.
        eq('single-combine URL has no season param',
            window.location.search, '?player=' + single.player_slug);
    }

    console.log('\n— Missing season fallback');
    {
        // Adem Bona doesn't have a 2099 combine. We should fall back to
        // most recent (2024) and show an inline notice.
        navigateTo(dom, '?player=adem-bona&season=2099');
        eq('fell back to most recent season',
            window.state.currentPlayer && window.state.currentPlayer.season, 2024);
        const notice = window.document.querySelector('.pv-missing-season');
        ok('missing-season notice rendered', !!notice);
        ok('notice mentions requested year',
            notice && notice.textContent.includes('2099'),
            notice && notice.textContent);
        // URL canonicalized to the fallback season.
        eq('URL rewritten to fallback canonical',
            window.location.search, '?player=adem-bona&season=2024');
    }

    console.log('\n— Comp card navigation');
    {
        // Pick a player with comps; click the first comp; verify nav.
        const players = window.state.data.players;
        const focal = players.find(p => p.doppelgangers && p.doppelgangers.length);
        navigateTo(dom, '?player=' + focal.player_slug +
            (window.getPlayerSlugIndex().get(focal.player_slug).length > 1
                ? '&season=' + focal.season : ''));
        const firstComp = window.document.querySelector('.dopp-card');
        ok('comp card rendered', !!firstComp);
        const expectedComp = focal.doppelgangers[0];
        ok('comp card href present',
            firstComp && firstComp.getAttribute('href').includes('player='),
            firstComp && firstComp.getAttribute('href'));
        firstComp.click();
        eq('navigated to comp player',
            window.state.currentPlayer && window.state.currentPlayer.player,
            expectedComp.player);
        eq('navigated to comp season',
            window.state.currentPlayer && window.state.currentPlayer.season,
            expectedComp.season);
    }

    console.log('\n— Player not found');
    {
        navigateTo(dom, '?player=nobody-named-this');
        const notFound = window.document.querySelector('.pv-not-found');
        ok('not-found view rendered', !!notFound);
    }

    console.log(`\nResults: ${passed} passed, ${failed} failed`);
    if (failed > 0) process.exit(1);
}

run().catch(err => {
    console.error('Test harness error:', err);
    process.exit(1);
});
