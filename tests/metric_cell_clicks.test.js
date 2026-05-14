/* JSDOM acceptance test for player-view metric-cell clicks.
 *
 * Each non-empty cell in the Anthro / Athletic / Ratios grids is a
 * button: clicking it navigates to the dashboard filtered to the
 * player's season and sorted by that metric, best first. Shooting
 * drill cells and empty cells stay inert.
 *
 * Run: node tests/metric_cell_clicks.test.js
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

// Find a clickable metric cell by its rendered name text inside the
// currently-rendered player view.
function findCellByName(window, name) {
    const cells = window.document.querySelectorAll('.metric-cell');
    for (const c of cells) {
        const n = c.querySelector('.name');
        if (n && n.textContent.trim() === name) return c;
    }
    return null;
}

async function run() {
    const dom = await bootDom();
    const { window } = dom;

    // ---- Cooper Flagg 2025: anthro/athletic/ratio click each ----
    console.log('\n— Cooper Flagg 2025: per-category click URL');
    navigateTo(dom, '/nba-draft-combine/?player=cooper-flagg');
    eq('on Cooper Flagg 2025', window.state.currentPlayer && window.state.currentPlayer.season, 2025);

    // Anthro click: Wingspan (higher-is-better). Best first = descending.
    {
        const cell = findCellByName(window, 'Wingspan');
        ok('Wingspan cell rendered', !!cell);
        ok('Wingspan cell is clickable',
            cell && cell.classList.contains('metric-cell-clickable'));
        eq('Wingspan cell tabindex=0',
            cell && cell.getAttribute('tabindex'), '0');
        eq('Wingspan cell role=button',
            cell && cell.getAttribute('role'), 'button');
        eq('Wingspan cell title text',
            cell && cell.title,
            'Sort 2025 prospects by Wingspan, best first');
        cell.click();
        eq('after Wingspan click: on dashboard view',
            window.state.view, 'dashboard');
        const params = new URLSearchParams(window.location.search);
        eq('URL season=2025', params.get('season'), '2025');
        eq('URL sort=Wingspan (in)', params.get('sort'), 'Wingspan (in)');
        eq('state.sort.key matches',
            window.state.sort && window.state.sort.key, 'Wingspan (in)');
        // Higher is better → 'best' direction (descending by value).
        eq('state.sort.dir = best (higher-is-better)',
            window.state.sort && window.state.sort.dir, 'best');
    }

    // Lower-is-better metric: Lane Agility. Direction stays 'best' but
    // the underlying sort respects metrics_meta.higher_is_better=false.
    {
        navigateTo(dom, '/nba-draft-combine/?player=cooper-flagg');
        const cell = findCellByName(window, 'Lane agility');
        ok('Lane agility cell rendered', !!cell);
        ok('Lane agility cell is clickable',
            cell && cell.classList.contains('metric-cell-clickable'));
        cell.click();
        const params = new URLSearchParams(window.location.search);
        eq('URL sort=Lane Agility (sec)',
            params.get('sort'), 'Lane Agility (sec)');
        eq('state.sort.dir = best (lower-is-better metric still uses best)',
            window.state.sort && window.state.sort.dir, 'best');
        const meta = window.state.data.metrics_meta['Lane Agility (sec)'];
        ok('Lane Agility is recorded as lower-is-better',
            meta && meta.higher_is_better === false);
    }

    // Ratio click: Wingspan : Height.
    {
        navigateTo(dom, '/nba-draft-combine/?player=cooper-flagg');
        const cell = findCellByName(window, 'Wingspan : Height');
        ok('W:H ratio cell rendered', !!cell);
        ok('W:H ratio cell is clickable',
            cell && cell.classList.contains('metric-cell-clickable'));
        cell.click();
        const params = new URLSearchParams(window.location.search);
        eq('URL sort=wingspan_to_height',
            params.get('sort'), 'wingspan_to_height');
    }

    console.log('\n— Empty cell: no-op');
    {
        navigateTo(dom, '/nba-draft-combine/?player=cooper-flagg');
        // Cooper Flagg's Body Fat % is null in the dataset.
        const cell = findCellByName(window, 'Body fat');
        ok('Body fat cell rendered (even when empty)', !!cell);
        ok('Body fat cell is NOT clickable',
            cell && !cell.classList.contains('metric-cell-clickable'),
            'empty cells must stay inert');
        ok('Body fat cell has no tabindex',
            cell && cell.getAttribute('tabindex') == null);
        ok('Body fat cell has no role',
            cell && cell.getAttribute('role') == null);
        ok('Body fat cell has no title',
            cell && !cell.title);
        const before = window.location.search;
        cell.click();
        eq('URL unchanged after empty-cell click',
            window.location.search, before);
    }

    console.log('\n— Shooting drill cells: no-op');
    {
        navigateTo(dom, '/nba-draft-combine/?player=cooper-flagg');
        // Shooting drills render inside .shot-cell elements, NOT
        // .metric-cell. Their cells must not carry the clickable class.
        const shotCells = window.document.querySelectorAll('.shot-cell');
        ok('shooting drill cells render', shotCells.length > 0);
        let anyClickable = false;
        for (const c of shotCells) {
            if (c.classList.contains('metric-cell-clickable')) { anyClickable = true; break; }
        }
        ok('no shooting cell is marked clickable', !anyClickable);
        // Click one anyway — verify URL doesn't change.
        const before = window.location.search;
        shotCells[0].click();
        eq('URL unchanged after shooting-cell click',
            window.location.search, before);
    }

    console.log('\n— Keyboard activation (Enter)');
    {
        navigateTo(dom, '/nba-draft-combine/?player=cooper-flagg');
        const cell = findCellByName(window, 'Max vertical');
        ok('Max vertical cell rendered', !!cell);
        const evt = new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true });
        cell.dispatchEvent(evt);
        const params = new URLSearchParams(window.location.search);
        eq('Enter key fires navigation',
            params.get('sort'), 'Max Vertical Leap (in)');
    }

    console.log('\n— Season-aware: clicking from a 2004 player goes to 2004');
    {
        // Pick any 2004 player record with at least one measured metric.
        const p2004 = window.state.data.players.find(
            p => p.season === 2004 && p.metrics &&
            p.metrics['Wingspan (in)'] && p.metrics['Wingspan (in)'].raw != null);
        ok('2004 player sample found', !!p2004);
        navigateTo(dom, '/nba-draft-combine/?player=' + p2004.player_slug);
        const cell = findCellByName(window, 'Wingspan');
        ok('2004 player Wingspan cell rendered', !!cell);
        eq('title attr reflects 2004 season',
            cell && cell.title,
            'Sort 2004 prospects by Wingspan, best first');
        cell.click();
        const params = new URLSearchParams(window.location.search);
        eq('URL season=2004 after click', params.get('season'), '2004');
    }

    console.log('\n— Browser back returns to player view');
    {
        navigateTo(dom, '/nba-draft-combine/?player=cooper-flagg');
        eq('on player view before click', window.state.view, 'player');
        const cell = findCellByName(window, 'Standing reach');
        cell.click();
        eq('on dashboard after click', window.state.view, 'dashboard');
        window.history.back();
        await new Promise(r => setTimeout(r, 10));
        // JSDOM doesn't fire popstate reliably; re-route manually.
        if (window.state.view !== 'player') {
            window.routeFromUrl({ fromPopstate: true });
        }
        eq('back on player view', window.state.view, 'player');
        eq('back on Cooper Flagg',
            window.state.currentPlayer && window.state.currentPlayer.player,
            'Cooper Flagg');
    }

    console.log(`\nResults: ${passed} passed, ${failed} failed`);
    if (failed > 0) process.exit(1);
}

run().catch(err => {
    console.error('Test harness error:', err);
    process.exit(1);
});
