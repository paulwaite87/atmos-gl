// The interaction rules shared by every choropleth layer (country_stats.js,
// internet_outages.js), kept free of map and DOM code so they can be tested on their own.
//
//   - Crossing into a country that has data badges it (`hover`); leaving takes the badge away.
//     Nothing else happens on hover.
//   - Clicking inside a country with data selects it (`selected`) and remembers where the
//     click was (`anchor`, the fallback for a popup when the country has no label point).
//     The selection -- its popup and ringed badge -- survives the cursor leaving, so the
//     popup can be entered and its links clicked.
//   - Clicking another country with data moves the selection in one click; clicking anywhere
//     else (sea, a country with no data, empty map) clears it.
//
// `hasData(code)` says whether a country has anything to inspect; `onChange` receives the
// new {hover, selected, anchor} whenever it differs from the last one reported. The layer
// calls `dataChanged()` after replacing its data, `clear()` when it is switched off.
export function createChoroplethInspector({ hasData, onChange }) {
    let hover = null;
    let selected = null;
    let anchor = null;
    let reported = null;

    const report = (force = false) => {
        // A badge on the selected country would sit under its own ring: show it once.
        const shownHover = hover === selected ? null : hover;
        const next = { hover: shownHover, selected, anchor };
        const same = reported
            && reported.hover === next.hover
            && reported.selected === next.selected
            && reported.anchor === next.anchor;
        if (same && !force) return;
        // Nothing hovered or selected, and nothing ever reported: nothing to say.
        if (!reported && !next.hover && !next.selected) return;
        reported = next;
        onChange(next);
    };

    return {
        enter(code) {
            hover = code && hasData(code) ? code : null;
            report();
        },
        leave() {
            hover = null;
            report();
        },
        click(code, point) {
            if (code && hasData(code)) {
                selected = code;
                anchor = point;
            } else {
                selected = null;
                anchor = null;
            }
            report();
        },
        dataChanged() {
            if (hover && !hasData(hover)) hover = null;
            if (selected && !hasData(selected)) {
                selected = null;
                anchor = null;
            }
            // An open selection is re-reported even if unchanged: its popup may need re-rendering.
            report(selected !== null);
        },
        clear() {
            hover = null;
            selected = null;
            anchor = null;
            report();
        },
    };
}
