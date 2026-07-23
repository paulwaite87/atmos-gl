// Smoke tests for liveLayerSync's raster-specific behavior on top of the shared
// reconcileLoop (architecture review candidate "unify the two reconcile engines").
// Confirms the image-existence chase and regen-detection sequence survived the
// extraction unchanged, driving the REAL reconcileLoop end-to-end (not mocked).
import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { liveLayerSync } from './_refresh.js';

const SECTION_KEY = 'sst';

function mockFetch({ section, imageStatus = 200, lastModified = null }) {
    globalThis.fetch = vi.fn(async (url, opts) => {
        if (opts && opts.method === 'HEAD') {
            if (imageStatus !== 200) {
                return { ok: false, status: imageStatus, headers: { get: () => null } };
            }
            return {
                ok: true, status: 200,
                headers: { get: (h) => (h === 'Last-Modified' ? lastModified : null) },
            };
        }
        return { json: async () => ({ data: { [SECTION_KEY]: section } }) };
    });
}

beforeEach(() => {
    vi.useFakeTimers();
    globalThis.window = { WM_API: 'http://test' };
    vi.spyOn(console, 'log').mockImplementation(() => {});
    vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
});

describe('image-existence chase', () => {
    test('does not refresh until the probed image exists', async () => {
        mockFetch({ section: { enabled: true }, imageStatus: 404 });
        const refresh = vi.fn();

        liveLayerSync({}, {
            sectionKey: SECTION_KEY, initialConfig: null,
            mount: vi.fn(), refresh, unmount: vi.fn(),
            imageUrl: () => 'http://test/sst.png',
            syncMs: 1000,
        });
        await vi.advanceTimersByTimeAsync(1000);  // enable transition (mount)
        await vi.advanceTimersByTimeAsync(1000);  // image-chase tick: still 404

        expect(refresh).not.toHaveBeenCalled();
    });

    test('refreshes once the image appears', async () => {
        const section = { enabled: true };
        mockFetch({ section, imageStatus: 404 });
        const refresh = vi.fn();

        liveLayerSync({}, {
            sectionKey: SECTION_KEY, initialConfig: null,
            mount: vi.fn(), refresh, unmount: vi.fn(),
            imageUrl: () => 'http://test/sst.png',
            syncMs: 1000,
        });
        await vi.advanceTimersByTimeAsync(1000);  // mount
        await vi.advanceTimersByTimeAsync(1000);  // still missing

        mockFetch({ section, imageStatus: 200 });
        await vi.advanceTimersByTimeAsync(1000);  // now present

        expect(refresh).toHaveBeenCalledTimes(1);
    });

    test('applies a settings change immediately even before the image first exists', async () => {
        // Regression: a layer stuck chasing a not-yet-rendered hour (imageReady still
        // false) must not ignore live settings changes like opacity -- those are
        // frontend-only and unrelated to whether the backend has produced this hour's
        // PNG yet.
        const refresh = vi.fn();
        mockFetch({ section: { enabled: true, opacity: 50 }, imageStatus: 404 });

        liveLayerSync({}, {
            sectionKey: SECTION_KEY, initialConfig: null,
            mount: vi.fn(), refresh, unmount: vi.fn(),
            imageUrl: () => 'http://test/sst.png',
            syncMs: 1000,
        });
        await vi.advanceTimersByTimeAsync(1000);  // mount (lastSig seeded to opacity:50)
        await vi.advanceTimersByTimeAsync(1000);  // still 404, imageReady stays false
        expect(refresh).not.toHaveBeenCalled();

        mockFetch({ section: { enabled: true, opacity: 0 }, imageStatus: 404 });
        await vi.advanceTimersByTimeAsync(1000);  // settings changed, image STILL missing

        expect(refresh).toHaveBeenCalledTimes(1);
        expect(refresh.mock.calls[0][0]).toEqual({ enabled: true, opacity: 0 });
    });

    test('calls onMissing when the probe 404s', async () => {
        mockFetch({ section: { enabled: true }, imageStatus: 404 });
        const onMissing = vi.fn();

        liveLayerSync({}, {
            sectionKey: SECTION_KEY, initialConfig: null,
            mount: vi.fn(), refresh: vi.fn(), unmount: vi.fn(),
            imageUrl: () => 'http://test/sst.png', onMissing,
            syncMs: 1000,
        });
        await vi.advanceTimersByTimeAsync(1000);  // mount
        await vi.advanceTimersByTimeAsync(1000);  // 404 probe

        expect(onMissing).toHaveBeenCalled();
    });
});

describe('regen-detection', () => {
    async function readySync({ section, refresh, refreshMs = 300000, regenWaitMs = 120000 }) {
        mockFetch({ section, imageStatus: 200, lastModified: 'Mon, 01 Jun 2026 00:00:00 GMT' });
        liveLayerSync({}, {
            sectionKey: SECTION_KEY, initialConfig: null,
            mount: vi.fn(), refresh, unmount: vi.fn(),
            imageUrl: () => 'http://test/sst.png',
            syncMs: 1000, refreshMs, regenWaitMs,
        });
        await vi.advanceTimersByTimeAsync(1000);  // mount
        await vi.advanceTimersByTimeAsync(1000);  // image-ready tick
        refresh.mockClear();
    }

    test('a settings change refreshes immediately and starts the regen chase', async () => {
        const refresh = vi.fn();
        const section = { enabled: true, palette: 'a' };
        await readySync({ section, refresh });

        mockFetch({
            section: { enabled: true, palette: 'b' }, imageStatus: 200,
            lastModified: 'Mon, 01 Jun 2026 00:00:00 GMT',
        });
        await vi.advanceTimersByTimeAsync(1000);

        expect(refresh).toHaveBeenCalledTimes(1);
        expect(refresh.mock.calls[0][0]).toEqual({ enabled: true, palette: 'b' });
    });

    test('applies a fresher backend render once its Last-Modified advances', async () => {
        const refresh = vi.fn();
        const section = { enabled: true, palette: 'a' };
        await readySync({ section, refresh });

        // Settings change (starts the regen chase, baseline mtime = 01 Jun).
        mockFetch({
            section: { enabled: true, palette: 'b' }, imageStatus: 200,
            lastModified: 'Mon, 01 Jun 2026 00:00:00 GMT',
        });
        await vi.advanceTimersByTimeAsync(1000);
        refresh.mockClear();

        // Backend produces a newer render.
        mockFetch({
            section: { enabled: true, palette: 'b' }, imageStatus: 200,
            lastModified: 'Tue, 02 Jun 2026 00:00:00 GMT',
        });
        await vi.advanceTimersByTimeAsync(1000);

        expect(refresh).toHaveBeenCalledTimes(1);
    });

    test('gives up waiting after regenWaitMs, then resumes the slow cadence', async () => {
        const refresh = vi.fn();
        const section = { enabled: true, palette: 'a' };
        await readySync({ section, refresh, regenWaitMs: 2000, refreshMs: 4000 });

        mockFetch({
            section: { enabled: true, palette: 'b' }, imageStatus: 200,
            lastModified: 'Mon, 01 Jun 2026 00:00:00 GMT',
        });
        await vi.advanceTimersByTimeAsync(1000);  // starts the regen chase
        refresh.mockClear();

        // Still the SAME mtime (no fresher render ever arrives) for the rest of the
        // regen window -- neither the "fresher render" nor the "m===0" branch fires,
        // so refresh is NOT called while awaitingRegen. Once regenWaitMs elapses the
        // deadline check clears awaitingRegen; once refreshMs elapses too, the plain
        // slow-cadence branch takes over and refresh fires again.
        await vi.advanceTimersByTimeAsync(1000);  // regen deadline passes (t=2000 since change)
        expect(refresh).not.toHaveBeenCalled();

        await vi.advanceTimersByTimeAsync(3000);  // now past refreshMs (t=4000+ since change)
        expect(refresh).toHaveBeenCalled();
    });
});

describe('slow-cadence fallback', () => {
    test('refreshes on the slow cadence when nothing changed', async () => {
        const refresh = vi.fn();
        const section = { enabled: true };
        mockFetch({ section, imageStatus: 200, lastModified: 'Mon, 01 Jun 2026 00:00:00 GMT' });

        liveLayerSync({}, {
            sectionKey: SECTION_KEY, initialConfig: null,
            mount: vi.fn(), refresh, unmount: vi.fn(),
            imageUrl: () => 'http://test/sst.png',
            syncMs: 1000, refreshMs: 3000,
        });
        await vi.advanceTimersByTimeAsync(1000);  // mount
        await vi.advanceTimersByTimeAsync(1000);  // image-ready
        refresh.mockClear();

        await vi.advanceTimersByTimeAsync(3000);  // past refreshMs, unchanged config

        expect(refresh).toHaveBeenCalled();
    });
});

describe('key-image regen-detection', () => {
    // Legend/colourbar-key PNGs (keyUrl) are backend-rendered but live at a DIFFERENT
    // path than the main per-hour data image (imageUrl) and aren't gated on
    // forecast-hour freshness -- for client-side-colormap layers (wind/currents/
    // jetstream/etc.) a palette-only config change never touches the data image's
    // mtime at all (the raw velocity/scalar texture is unchanged; only the legend key
    // is re-rendered), so the existing imageUrl-only regen chase never detects it and
    // the legend sits stale until the slow refreshMs fallback (minutes later). keyUrl
    // is opt-in and independent of imageUrl's regen chase, so existing callers that
    // don't pass it are completely unaffected (see every other describe block above,
    // none of which pass keyUrl).
    function mockFetchTwoUrls({
        section, dataStatus = 200, dataLastModified = null,
        keyStatus = 200, keyLastModified = null,
    }) {
        globalThis.fetch = vi.fn(async (url, opts) => {
            if (opts && opts.method === 'HEAD') {
                const isKey = String(url).includes('_key.png');
                const status = isKey ? keyStatus : dataStatus;
                const lm = isKey ? keyLastModified : dataLastModified;
                if (status !== 200) return { ok: false, status, headers: { get: () => null } };
                return { ok: true, status: 200, headers: { get: (h) => (h === 'Last-Modified' ? lm : null) } };
            }
            return { json: async () => ({ data: { [SECTION_KEY]: section } }) };
        });
    }

    async function readySync({ section, refresh }) {
        mockFetchTwoUrls({
            section, dataLastModified: 'Mon, 01 Jun 2026 00:00:00 GMT',
            keyLastModified: 'Mon, 01 Jun 2026 00:00:00 GMT',
        });
        liveLayerSync({}, {
            sectionKey: SECTION_KEY, initialConfig: null,
            mount: vi.fn(), refresh, unmount: vi.fn(),
            imageUrl: () => 'http://test/sst.png',
            keyUrl: () => 'http://test/sst_key.png',
            syncMs: 1000,
        });
        await vi.advanceTimersByTimeAsync(1000);  // mount
        await vi.advanceTimersByTimeAsync(1000);  // image-ready
        refresh.mockClear();
    }

    test('a palette-only change (data image mtime unchanged) still refreshes once the key regenerates', async () => {
        const refresh = vi.fn();
        const section = { enabled: true, palette: 'a' };
        await readySync({ section, refresh });

        // Config changes (palette) but the DATA image's mtime never moves -- exactly
        // what happens for a client-side-colormap layer, where only the legend key is
        // re-rendered server-side.
        mockFetchTwoUrls({
            section: { enabled: true, palette: 'b' },
            dataLastModified: 'Mon, 01 Jun 2026 00:00:00 GMT',
            keyLastModified: 'Mon, 01 Jun 2026 00:00:00 GMT',
        });
        await vi.advanceTimersByTimeAsync(1000);  // sig change: immediate refresh
        expect(refresh).toHaveBeenCalledTimes(1);
        refresh.mockClear();

        // Backend regenerates the key (its mtime advances); data image mtime is
        // unrelated and still hasn't moved.
        mockFetchTwoUrls({
            section: { enabled: true, palette: 'b' },
            dataLastModified: 'Mon, 01 Jun 2026 00:00:00 GMT',
            keyLastModified: 'Tue, 02 Jun 2026 00:00:00 GMT',
        });
        await vi.advanceTimersByTimeAsync(1000);

        expect(refresh).toHaveBeenCalledTimes(1);
    });

    test('gives up watching the key after regenWaitMs without ever refreshing extra', async () => {
        const refresh = vi.fn();
        const section = { enabled: true, palette: 'a' };
        await readySync({ section, refresh });

        mockFetchTwoUrls({
            section: { enabled: true, palette: 'b' },
            dataLastModified: 'Mon, 01 Jun 2026 00:00:00 GMT',
            keyLastModified: 'Mon, 01 Jun 2026 00:00:00 GMT',
        });
        await vi.advanceTimersByTimeAsync(1000);  // sig change
        refresh.mockClear();

        // Key never regenerates for the rest of the window.
        await vi.advanceTimersByTimeAsync(120000);

        expect(refresh).not.toHaveBeenCalled();
    });
});

describe('initialGlobals', () => {
    test('seeds the initial mount when the shared loop cannot supply globals yet', async () => {
        mockFetch({ section: { enabled: true }, imageStatus: 200 });
        const mount = vi.fn();

        liveLayerSync({}, {
            sectionKey: SECTION_KEY, initialConfig: { enabled: true },
            mount, refresh: vi.fn(), unmount: vi.fn(),
            imageUrl: () => 'http://test/sst.png',
            globalKeys: ['animation'],
            initialGlobals: { animation: { fps: 5 } },
            syncMs: 1000,
        });
        await vi.advanceTimersByTimeAsync(0);  // initial dispatch

        expect(mount).toHaveBeenCalledTimes(1);
        expect(mount.mock.calls[0][1]).toEqual({ animation: { fps: 5 } });
    });
});
