import { describe, expect, test } from 'vitest';
import {
    changeDays, changeSummaryRows, descriptionHtml, linkifyHtml, parseFlyTarget, visibleStatuses,
} from './frontline.js';

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

describe('descriptionHtml', () => {
    test('turns map-linked pieces into fly-to links and escapes all text', () => {
        const html = descriptionHtml({
            description: 'unused',
            description_segments: [
                { text: 'The enemy has occupied ' },
                { text: 'Svyatopetrivka', lat: 47.69, lon: 36.16, zoom: 13 },
                { text: ' & <b>more</b>' },
                { text: 'Telegram', url: 'https://t.me/DeepStateEN/99' },
            ],
        });
        expect(html).toBe(
            'The enemy has occupied '
            + '<a href="#" data-frontline-fly="36.16,47.69,13" title="Show on map">Svyatopetrivka</a>'
            + ' &amp; &lt;b&gt;more&lt;/b&gt;'
            + '<a href="https://t.me/DeepStateEN/99" target="_blank" rel="noopener noreferrer">Telegram</a>');
    });

    test('never links a non-http url', () => {
        const html = descriptionHtml({ description_segments: [{ text: 'x', url: 'javascript:alert(1)' }] });
        expect(html).toBe('x');
    });

    test('falls back to the escaped plain description for older snapshots', () => {
        expect(descriptionHtml({ description: 'A <b>', description_segments: null })).toBe('A &lt;b&gt;');
        expect(descriptionHtml(null)).toBe('');
    });
});

describe('linkifyHtml', () => {
    test('links bare URLs and escapes everything else', () => {
        expect(linkifyHtml('See https://t.me/DeepStateUA/10772 <now>')).toBe(
            'See <a href="https://t.me/DeepStateUA/10772" target="_blank" rel="noopener noreferrer">'
            + 'https://t.me/DeepStateUA/10772</a> &lt;now&gt;');
    });
});

describe('parseFlyTarget', () => {
    test('reads lon,lat,zoom', () => {
        expect(parseFlyTarget('36.16,47.69,13')).toEqual({ center: [36.16, 47.69], zoom: 13 });
    });

    test('rejects malformed or out-of-range targets', () => {
        expect(parseFlyTarget('a,b,c')).toBeNull();
        expect(parseFlyTarget('36.16,95,13')).toBeNull();
        expect(parseFlyTarget(undefined)).toBeNull();
    });
});
