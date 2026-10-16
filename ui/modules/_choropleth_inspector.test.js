import { describe, expect, test } from 'vitest';
import { createChoroplethInspector } from './_choropleth_inspector.js';

// A recording inspector over a set of countries that "have data". `states` is every state
// the inspector reported, in order; `last()` the most recent.
function make(withData = ['LT', 'AM', 'EG']) {
    const data = new Set(withData);
    const states = [];
    const inspector = createChoroplethInspector({
        hasData: (code) => data.has(code),
        onChange: (state) => states.push(state),
    });
    return { inspector, data, states, last: () => states[states.length - 1] };
}

const LT_POINT = { lng: 24, lat: 55 };

describe('hovering', () => {
    test('crossing into a country with data shows its badge, and nothing is selected', () => {
        const { inspector, last } = make();
        inspector.enter('LT');
        expect(last()).toEqual({ hover: 'LT', selected: null, anchor: null });
    });

    test('a country without data shows nothing at all', () => {
        const { inspector, states } = make();
        inspector.enter('DE');
        expect(states).toEqual([]);
    });

    test('leaving the country takes the badge away', () => {
        const { inspector, last } = make();
        inspector.enter('LT');
        inspector.leave();
        expect(last()).toEqual({ hover: null, selected: null, anchor: null });
    });

    test('a stream of moves inside one country reports the badge once', () => {
        const { inspector, states } = make();
        inspector.enter('LT');
        inspector.enter('LT');
        inspector.enter('LT');
        expect(states).toHaveLength(1);
    });

    test('moving straight from one country with data into the next switches the badge', () => {
        const { inspector, last } = make();
        inspector.enter('LT');
        inspector.enter('AM');
        expect(last().hover).toBe('AM');
    });
});

describe('clicking', () => {
    test('a click inside a country with data selects it, anchored where it was clicked', () => {
        const { inspector, last } = make();
        inspector.enter('LT');
        inspector.click('LT', LT_POINT);
        expect(last()).toEqual({ hover: null, selected: 'LT', anchor: LT_POINT });
    });

    test('the selection outlives the cursor leaving the country, so the popup can be reached', () => {
        const { inspector, last } = make();
        inspector.enter('LT');
        inspector.click('LT', LT_POINT);
        inspector.leave();
        expect(last().selected).toBe('LT');
    });

    test('hovering another country while one is selected badges it without losing the selection', () => {
        const { inspector, last } = make();
        inspector.click('LT', LT_POINT);
        inspector.enter('AM');
        expect(last()).toEqual({ hover: 'AM', selected: 'LT', anchor: LT_POINT });
    });

    test('hovering the selected country again adds no second badge', () => {
        const { inspector, last } = make();
        inspector.click('LT', LT_POINT);
        inspector.enter('LT');
        expect(last().hover).toBeNull();
    });

    test('clicking another country with data moves the selection there in one click', () => {
        const { inspector, last } = make();
        inspector.click('LT', LT_POINT);
        inspector.click('AM', { lng: 45, lat: 40 });
        expect(last()).toEqual({ hover: null, selected: 'AM', anchor: { lng: 45, lat: 40 } });
    });

    test('clicking elsewhere (sea, empty map) clears the selection', () => {
        const { inspector, last } = make();
        inspector.click('LT', LT_POINT);
        inspector.click(null, { lng: 0, lat: 0 });
        expect(last()).toEqual({ hover: null, selected: null, anchor: null });
    });

    test('clicking a country without data clears the selection rather than selecting it', () => {
        const { inspector, last } = make();
        inspector.click('LT', LT_POINT);
        inspector.click('DE', { lng: 10, lat: 51 });
        expect(last().selected).toBeNull();
    });

    test('a click with nothing selected and nothing to select reports nothing', () => {
        const { inspector, states } = make();
        inspector.click(null, { lng: 0, lat: 0 });
        expect(states).toEqual([]);
    });
});

describe('when the data changes under it', () => {
    test('an open selection is reported again, so its popup can be re-rendered', () => {
        const { inspector, states, last } = make();
        inspector.click('LT', LT_POINT);
        const before = states.length;
        inspector.dataChanged();
        expect(states.length).toBe(before + 1);
        expect(last().selected).toBe('LT');
    });

    test('a selected country that no longer has data is deselected', () => {
        const { inspector, data, last } = make();
        inspector.click('LT', LT_POINT);
        data.delete('LT');
        inspector.dataChanged();
        expect(last()).toEqual({ hover: null, selected: null, anchor: null });
    });

    test('a hovered country that no longer has data loses its badge', () => {
        const { inspector, data, last } = make();
        inspector.enter('AM');
        data.delete('AM');
        inspector.dataChanged();
        expect(last().hover).toBeNull();
    });

    test('with nothing hovered or selected there is nothing to report', () => {
        const { inspector, states } = make();
        inspector.dataChanged();
        expect(states).toEqual([]);
    });
});

describe('clear', () => {
    test('drops both the badge and the selection, e.g. when the layer is turned off', () => {
        const { inspector, last } = make();
        inspector.click('LT', LT_POINT);
        inspector.enter('AM');
        inspector.clear();
        expect(last()).toEqual({ hover: null, selected: null, anchor: null });
    });
});
