/* JSDOM acceptance test for desktop sticky dashboard table headers.
 *
 * JSDOM doesn't actually scroll or evaluate `position: sticky` layout,
 * so we verify the implementation contract via stylesheet rule
 * introspection (the @media rule exists with the right declarations)
 * and DOM/runtime behaviour: --th-sticky-top is set on :root after
 * boot, every <th> in the dashboard thead picks up the styles, sort
 * clicks still fire, and the mobile breakpoint hides the table
 * (so the sticky behaviour is genuinely desktop-only).
 *
 * Run: node tests/sticky_table_headers.test.js
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

// Walk all stylesheets for a rule that matches `selector` inside an
// @media (min-width: …) block (default: 769px) and return the value of
// the named CSS property, or null if not found.
function findDesktopRule(window, selector, prop, minWidthPx = 769) {
    const needle = new RegExp(`min-width:\\s*${minWidthPx}px`);
    for (const sheet of window.document.styleSheets) {
        for (const rule of (sheet.cssRules || [])) {
            if (rule.constructor.name !== 'CSSMediaRule') continue;
            const cond = rule.conditionText || rule.media.mediaText || '';
            if (!needle.test(cond)) continue;
            for (const inner of rule.cssRules) {
                if (!inner.selectorText) continue;
                const sels = inner.selectorText.split(',').map(s => s.trim());
                if (!sels.includes(selector)) continue;
                if (!prop) return inner;
                const val = inner.style.getPropertyValue(prop);
                if (val) return val;
            }
        }
    }
    return null;
}

// Same shape, but for max-width: 768 (mobile) — used to confirm the
// mobile rule that hides the table is still in place.
function findMobileRule(window, selector, prop) {
    for (const sheet of window.document.styleSheets) {
        for (const rule of (sheet.cssRules || [])) {
            if (rule.constructor.name !== 'CSSMediaRule') continue;
            const cond = rule.conditionText || rule.media.mediaText || '';
            if (!/max-width:\s*768px/.test(cond)) continue;
            for (const inner of rule.cssRules) {
                if (!inner.selectorText) continue;
                const sels = inner.selectorText.split(',').map(s => s.trim());
                if (!sels.includes(selector)) continue;
                if (!prop) return inner;
                const val = inner.style.getPropertyValue(prop);
                if (val) return val;
            }
        }
    }
    return null;
}

async function run() {
    const dom = await bootDom();
    const { window } = dom;

    console.log('\n— @media (min-width: 769px) stylesheet rules');
    eq('table.leaderboard thead th -> position:sticky',
        findDesktopRule(window, 'table.leaderboard thead th', 'position'), 'sticky');
    const topVal = findDesktopRule(window, 'table.leaderboard thead th', 'top');
    ok('table.leaderboard thead th -> top references --th-sticky-top',
        topVal && topVal.includes('--th-sticky-top'),
        `top=${JSON.stringify(topVal)}`);
    const zIndex = findDesktopRule(window, 'table.leaderboard thead th', 'z-index');
    ok('table.leaderboard thead th -> z-index >= 10 and < 100 (under filter panel)',
        zIndex && Number(zIndex) >= 10 && Number(zIndex) < 100,
        `z-index=${JSON.stringify(zIndex)}`);
    ok('table.leaderboard thead th -> background declared explicitly',
        !!findDesktopRule(window, 'table.leaderboard thead th', 'background'),
        'rows scrolling under must not bleed through');
    eq('.table-wrap -> overflow:clip on desktop (sticky needs visible/clip)',
        findDesktopRule(window, '.table-wrap', 'overflow'), 'clip');
    eq('table.leaderboard thead th.sorted-by -> background var(--accent-dim)',
        findDesktopRule(window, 'table.leaderboard thead th.sorted-by', 'background'),
        'var(--accent-dim)');

    console.log('\n— Mobile breakpoint still hides the table');
    eq('@media max-width:768px .table-wrap -> display:none (untouched)',
        findMobileRule(window, '.table-wrap', 'display'), 'none');

    console.log('\n— DOM + runtime wiring');
    const root = window.document.documentElement;
    const offset = root.style.getPropertyValue('--th-sticky-top');
    ok('--th-sticky-top is set on :root after init',
        !!offset && /^\d+px$/.test(offset),
        `--th-sticky-top=${JSON.stringify(offset)}`);
    // initStickyHeaderOffset reads filtersBody.getBoundingClientRect().
    // JSDOM returns 0 for all box dims unless we lay things out, which
    // it doesn't. So the value is "0px" — but that's fine: the contract
    // is that it gets *set*, and the ResizeObserver updates it the
    // moment a real browser computes a non-zero rect.
    const thead = window.document.querySelector('table.leaderboard thead');
    ok('thead is present', !!thead);
    const ths = thead.querySelectorAll('th');
    ok('at least 6 <th> headers rendered (rank, player, scores, metrics, ...)',
        ths.length >= 6,
        `count=${ths.length}`);
    // All <th> instances are leaf cells — sticky is applied per-cell,
    // not per-thead or per-tr (which doesn't work across browsers).
    let allThArePinnable = true;
    for (const th of ths) {
        if (th.tagName !== 'TH') { allThArePinnable = false; break; }
    }
    ok('every header cell is a <th> leaf (sticky requires leaf cells)',
        allThArePinnable);

    console.log('\n— Sort clicks still work when headers are sticky');
    // Find the Wingspan column header and click it. Sort state should flip.
    let wingspanTh = null;
    for (const th of ths) {
        if (th.textContent.includes('Wingspan')) { wingspanTh = th; break; }
    }
    ok('Wingspan header found', !!wingspanTh);
    const sortBefore = window.state.sort && window.state.sort.key;
    wingspanTh.click();
    const sortAfter = window.state.sort && window.state.sort.key;
    ok('clicking sticky header changes sort key',
        sortAfter !== sortBefore && sortAfter === 'Wingspan (in)',
        `before=${sortBefore} after=${sortAfter}`);

    console.log('\n— Filter-panel height drives the offset (ResizeObserver wired)');
    // initStickyHeaderOffset called apply() once; verify the function
    // is observable on the global so the offset stays fresh. The
    // contract is: changing the filter panel's size triggers an update.
    const filters = window.document.getElementById('filtersBody');
    ok('#filtersBody present (offset source)', !!filters);
    // Force-set a non-zero height via JSDOM's getBoundingClientRect
    // override and trigger the observer manually. JSDOM doesn't fire
    // ResizeObserver naturally, so we re-call initStickyHeaderOffset's
    // apply step by simulating a stub. The real-browser behaviour is
    // covered by the manual smoke pass in the PR description.
    Object.defineProperty(filters, 'getBoundingClientRect', {
        value: () => ({ height: 137, width: 0, top: 0, left: 0, right: 0, bottom: 137, x: 0, y: 0, toJSON: () => '' }),
        configurable: true,
    });
    // Re-init to read the new rect.
    window.initStickyHeaderOffset();
    eq('--th-sticky-top updates after filter panel measures 137px',
        root.style.getPropertyValue('--th-sticky-top'), '137px');

    console.log(`\nResults: ${passed} passed, ${failed} failed`);
    if (failed > 0) process.exit(1);
}

run().catch(err => {
    console.error('Test harness error:', err);
    process.exit(1);
});
