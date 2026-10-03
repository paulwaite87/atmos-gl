import { describe, expect, test } from 'vitest';
import { visibleStatuses } from './frontline.js';

describe('visibleStatuses', () => {
    test('occupied and contested by default; liberated is opt-in', () => {
        expect(visibleStatuses({})).toEqual(['occupied', 'contested']);
        expect(visibleStatuses({ show_liberated: true })).toEqual(['occupied', 'contested', 'liberated']);
    });

    test('contested can be hidden; occupied always shows', () => {
        expect(visibleStatuses({ show_contested: false, show_liberated: false })).toEqual(['occupied']);
    });
});
