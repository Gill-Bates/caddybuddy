import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

function loadHooks() {
    const App = {
        exposeTestHooks: true,
        initializeSslLabsHistoryChart: () => {},
    };
    const document = {
        readyState: 'loading',
        addEventListener: () => {},
        documentElement: {
            getAttribute: () => null,
        },
    };
    const window = {
        CaddyBuddyApp: App,
        Chart: undefined,
        matchMedia: () => ({ matches: false }),
        location: { origin: 'http://localhost:8000' },
        addEventListener: () => {},
    };

    const context = {
        window,
        document,
        console,
        Date,
        Map,
        Set,
        URL,
        getComputedStyle: () => ({ getPropertyValue: () => '' }),
        HTMLCanvasElement: class HTMLCanvasElement {},
        HTMLInputElement: class HTMLInputElement {},
    };
    window.window = window;
    window.document = document;

    const source = readFileSync('app/static/js/ssllabs-history-chart.js', 'utf8');
    runInNewContext(source, context, { filename: 'ssllabs-history-chart.js' });
    return App.__testHooks.ssllabsHistoryChart;
}

test('grade scale deduplicates ranks and keeps canonical mixed grade label', () => {
    const hooks = loadHooks();
    const result = hooks.buildGradeScale({
        grade_scale: {
            'A+': 7,
            A: 6,
            T: -1,
            M: -1,
            MIXED: -1,
        },
    });

    assert.deepEqual(Array.from(result.gradeOrder), [7, 6, -1]);
    assert.equal(result.gradeMeta[-1].label, 'T/M');
});

test('weekly state forward-fills scans to week buckets only', () => {
    const hooks = loadHooks();
    const weekLabels = ['2026-06-01', '2026-06-08', '2026-06-15'];
    const state = hooks.buildWeeklyState([
        {
            host: 'example.com',
            points: [
                { date: '2026-06-03', rank: 6, grade: 'A' },
                { date: '2026-06-14', rank: 7, grade: 'A+' },
            ],
        },
    ], weekLabels);

    assert.equal(hooks.weekEndIso('2026-06-08'), '2026-06-14');
    assert.equal(state.length, 3);
    assert.equal(state[0].get('example.com').rank, 6);
    assert.equal(state[1].get('example.com').rank, 7);
    assert.equal(state[2].get('example.com').rank, 7);
});
