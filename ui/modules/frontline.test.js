import { describe, expect, test } from 'vitest';
import { changeDays, changeSummaryRows, visibleStatuses } from './frontline.js';

describe('visibleStatuses', () => {
    test('occupied and contested by default; liberated is opt-in', () => {
        expect(visibleStatuses({})).toEqual(['occupied', 'contested']);
        expect(visibleStatuses({ show_liberated: true })).toEqual(['occupied', 'contested', 'liberated']);
    });

    test('contested can be hidden; occupied always shows', () => {
        expect(visibleStatuses({ show_contested: false, show_liberated: false })).toEqual(['occupied']);
    });
});

describe('changeDays', () => {
    test('reads the select\'s string value, falling back to 7 days', () => {
        expect(changeDays({ change_days: '30' })).toBe(30);
        expect(changeDays({ change_days: 1 })).toBe(1);
        expect(changeDays({})).toBe(7);
        expect(changeDays({ change_days: '5' })).toBe(7);
    });
});

describe('changeSummaryRows', () => {
    test('is empty until there is a comparison', () => {
        expect(changeSummaryRows(null)).toEqual([]);
    });

    test('shows each side\'s total gain', () => {
        const rows = changeSummaryRows({
            days: 7,
            from: { id: 1, created_at: '2026-09-24T18:00:00+00:00' },
            to: { id: 2, created_at: '2026-10-01T18:23:57+00:00' },
            totals_km2: { russian_gain: 32.3, ukrainian_gain: 42.9 },
        });
        expect(rows.map((r) => r.label)).toEqual(['Period', 'Russia', 'Ukraine']);
        expect(rows[1].value).toBe('+32.3 km²');
        expect(rows[2].value).toBe('+42.9 km²');
    });
});
