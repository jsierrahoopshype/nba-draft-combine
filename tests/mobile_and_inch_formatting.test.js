/* JSDOM acceptance test for the mobile-layout + inch-formatting PR.
 *
 * Covers:
 *   1. Inch values render with trailing zeros stripped (6' 7'' / 7' 0.5'' /
 *      6' 7.25'' / 9'' etc.), ratios + sprints + weights untouched.
 *   2. Mobile dashboard hides the page header and renders the filter
 *      panel inline at the top.
 *   3. Mobile player-view hero is compact: name + Combine Rating share
 *      one row, the "Position-cohort scores" h2 is hidden, the 4 score
 *      tiles render in a single row.
 *
 * Run: node tests/mobile_and_inch_formatting.test.js
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

async function bootDom({ viewportWidth = 1280 } = {}) {
    const dom = new JSDOM(HTML, {
        url: 'https://example.com/',
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
            // Force the matchMedia result for the mobile breakpoint. JSDOM
            // doesn't actually do viewport-driven layout — getComputedStyle
            // still evaluates @media (max-width: 768px) rules based on the
            // reported innerWidth.
            Object.defineProperty(window, 'innerWidth', { value: viewportWidth, configurable: true });
        },
    });
    await dom.window.__appReady;
    return dom;
}

function navigateTo(dom, search) {
    dom.window.history.pushState({}, '', search);
    dom.window.routeFromUrl({ fromPopstate: false });
}

async function run() {
    // ---- Change 1: inch trimming (unit-test the helper directly) ----
    console.log('\n— trimInchZeros / fmtMetric: trailing-zero trim');
    {
        const dom = await bootDom();
        const { window } = dom;
        const trim = window.trimInchZeros;

        eq("\"6' 7.00''\" -> \"6' 7''\"",    trim("6' 7.00''"),    "6' 7''");
        eq("\"6' 7.50''\" -> \"6' 7.5''\"",  trim("6' 7.50''"),    "6' 7.5''");
        eq("\"6' 7.25''\" unchanged",        trim("6' 7.25''"),    "6' 7.25''");
        eq("\"7' 0.00''\" -> \"7' 0''\"",    trim("7' 0.00''"),    "7' 0''");
        eq("\"7' 0.75''\" unchanged",        trim("7' 0.75''"),    "7' 0.75''");
        eq("\"9.00\\\"\" -> \"9\\\"\"",      trim("9.00\""),       "9\"");
        eq("\"31.5\\\"\" unchanged",         trim("31.5\""),       "31.5\"");
        eq("\"12.34s\" unchanged",           trim("12.34s"),       "12.34s");
        eq("plain integer string unchanged", trim("210 lbs"),      "210 lbs");

        // fmtMetric round-trip on a real record. Pick a player whose display
        // string is known to have trailing zeros (Ausar Thompson 2023 has
        // Wingspan "7' 0.00''" in the source data).
        const ausar = window.state.data.players.find(
            p => p.player === 'Ausar Thompson' && p.season === 2023);
        if (ausar) {
            // Wingspan: display string in JSON is "7' 0.00''"; fmtMetric
            // must strip to "7' 0''".
            eq('fmtMetric strips Wingspan zeros',
                window.fmtMetric(ausar, 'Wingspan (in)'),
                "7' 0''");
            // Hand Length raw = 8.75 -> "8.75\"" (unchanged, has non-zero frac).
            eq('fmtMetric Hand Length keeps quarter-inch',
                window.fmtMetric(ausar, 'Hand Length (in)'),
                "8.75\"");
        } else {
            ok('Ausar Thompson 2023 sample present', false, 'sample missing');
        }

        // Ratio values must NOT be stripped — wingspan_to_height stays at
        // 3 decimals even when the third digit is 0.
        const sampleRatio = window.state.data.players.find(p => {
            const r = p.metrics && p.metrics.wingspan_to_height;
            if (!r || r.raw == null) return false;
            // Look for one whose 3-decimal rendering ends in 0.
            return /\.\d\d0$/.test(r.raw.toFixed(3));
        });
        if (sampleRatio) {
            const out = window.fmtMetric(sampleRatio, 'wingspan_to_height');
            ok('wingspan_to_height keeps trailing zero (ratio, not inches)',
                out.endsWith('0'),
                `got ${JSON.stringify(out)}`);
        }

        dom.window.close();
    }

    // ---- Mobile rule introspection ----
    // JSDOM's getComputedStyle doesn't fully evaluate @media (max-width)
    // rules, so we assert the rules exist in the stylesheet directly.
    // That validates the implementation; visual correctness still needs a
    // real browser pass per the acceptance checklist.
    function findMobileRule(window, selector, prop) {
        for (const sheet of window.document.styleSheets) {
            const rules = sheet.cssRules || [];
            for (const rule of rules) {
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

    console.log('\n— Mobile dashboard: stylesheet rules');
    {
        const dom = await bootDom();
        const { window } = dom;

        eq('@media: #dashboardView > h1 -> display:none',
            findMobileRule(window, '#dashboardView > h1', 'display'), 'none');
        eq('@media: #dashboardView > p.subtitle -> display:none',
            findMobileRule(window, '#dashboardView > p.subtitle', 'display'), 'none');
        eq('@media: #dashboardView > .filters-toggle -> display:none',
            findMobileRule(window, '#dashboardView > .filters-toggle', 'display'), 'none');
        eq('@media: #dashboardView > .filters[data-collapsed="true"] -> display:block',
            findMobileRule(window, '#dashboardView > .filters[data-collapsed="true"]', 'display'), 'block');
        eq('@media: .cards is shown on mobile',
            findMobileRule(window, '.cards', 'display'), 'block');

        // Confirm the filter panel actually starts with data-collapsed="true"
        // (so the mobile override is what makes it visible).
        const filtersPanel = window.document.getElementById('filtersBody');
        eq('filters panel data-collapsed default', filtersPanel.dataset.collapsed, 'true');
        // Search input + cards container are in the DOM regardless of media.
        ok('search input in DOM', !!window.document.getElementById('search'));
        const cards = window.document.getElementById('cards');
        ok('cards container in DOM and populated',
            cards && cards.querySelectorAll('.player-card').length > 0);

        // Desktop default: filters-toggle is display:none, set OUTSIDE the
        // media query. Make sure we didn't accidentally widen that.
        let desktopToggleRule = null;
        for (const sheet of window.document.styleSheets) {
            for (const rule of (sheet.cssRules || [])) {
                if (rule.constructor.name === 'CSSStyleRule' &&
                    rule.selectorText === '.filters-toggle') {
                    if (rule.style.getPropertyValue('display') === 'none') {
                        desktopToggleRule = rule;
                    }
                }
            }
        }
        ok('desktop: .filters-toggle still display:none outside media query',
            !!desktopToggleRule);

        dom.window.close();
    }

    console.log('\n— Mobile player view: stylesheet rules + DOM structure');
    {
        const dom = await bootDom();
        const { window } = dom;

        // Render the player view; structural assertions don't depend on
        // viewport. We still verify the DOM elements exist with the right
        // classes so the CSS selectors have something to bind to.
        const target = window.state.data.players.find(
            p => p.player === 'Adou Thiero' && p.season === 2025)
            || window.state.data.players.find(p => p.scores.overall != null);
        navigateTo(dom, '?player=' + target.player_slug);

        ok('.pv-hero rendered',  !!window.document.querySelector('.pv-hero'));
        ok('.pv-hero-left rendered (wraps name+meta)',
            !!window.document.querySelector('.pv-hero-left'));
        ok('.pv-hero-name rendered',
            !!window.document.querySelector('.pv-hero-name'));
        ok('.pv-hero-rating rendered',
            !!window.document.querySelector('.pv-hero-rating'));
        const scoreSection = window.document.querySelector('.pv-section-scores');
        ok('score section has .pv-section-scores class', !!scoreSection);
        ok('score section has child h2 (will be hidden on mobile)',
            !!(scoreSection && scoreSection.querySelector('h2')));
        eq('4 score tiles rendered',
            window.document.querySelectorAll('.pv-section-scores .score-tile').length, 4);

        // Mobile rules wiring.
        eq('@media: .pv-hero -> display:grid',
            findMobileRule(window, '.pv-hero', 'display'), 'grid');
        ok('@media: .pv-hero -> uses 2-area grid template',
            /name\s+rating[\s\S]*meta\s+meta/.test(
                findMobileRule(window, '.pv-hero', 'grid-template-areas') || ''));
        eq('@media: .pv-hero-left -> display:contents',
            findMobileRule(window, '.pv-hero-left', 'display'), 'contents');
        eq('@media: .pv-section-scores h2 -> display:none',
            findMobileRule(window, '.pv-section-scores h2', 'display'), 'none');
        const cols = findMobileRule(window, '.scores-summary', 'grid-template-columns');
        ok('@media: .scores-summary -> 4 columns',
            cols && (/repeat\(4,\s*1fr\)/.test(cols) || /1fr\s+1fr\s+1fr\s+1fr/.test(cols)),
            `value=${JSON.stringify(cols)}`);

        // Desktop pv-hero default: 2-column grid 1fr auto, no mobile areas.
        // Walk the stylesheets for the *unconditional* .pv-hero rule.
        let desktopHero = null;
        for (const sheet of window.document.styleSheets) {
            for (const rule of (sheet.cssRules || [])) {
                if (rule.constructor.name === 'CSSStyleRule' &&
                    rule.selectorText === '.pv-hero') {
                    desktopHero = rule;
                }
            }
        }
        ok('desktop: .pv-hero rule still defined outside media',
            !!desktopHero);
        ok('desktop: .pv-hero still uses 1fr auto template (untouched)',
            desktopHero && /1fr\s+auto/.test(
                desktopHero.style.getPropertyValue('grid-template-columns')));

        dom.window.close();
    }

    console.log(`\nResults: ${passed} passed, ${failed} failed`);
    if (failed > 0) process.exit(1);
}

run().catch(err => {
    console.error('Test harness error:', err);
    process.exit(1);
});
