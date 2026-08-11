// Smoke tests for liveDataSync's GeoJSON-specific behavior on top of the shared
// reconcileLoop (architecture review candidate "unify the two reconcile engines").
// Confirms the mount-then-recheck-then-maybe-back-out sequence and the
// signature/cadence refresh policy survived the extraction unchanged, driving the
// REAL reconcileLoop end-to-end (not mocked) so this exercises the actual dispatch.
import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { liveDataSync } from './_datasync.js';

const SECTION_KEY = 'quakes';

// reconcileLoop now polls GET /api/layer_availability alongside /api/config every
// tick (see _reconcile.js) -- these tests aren't about that gating (covered directly
// in _reconcile.test.js), so this always answers "no entry -> always collecting",
// leaving the mount/refresh policy under test here driven purely by `section`.
function mockFetch(sectionOf) {
    globalThis.fetch = vi.fn(async (url) => ({
        json: async () => (
            String(url).includes('/layer_availability')
                ? { data: {} }
                : { data: { [SECTION_KEY]: sectionOf() } }
        ),
    }));
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

describe('mount-then-recheck', () => {
    test('stays mounted when still enabled at recheck time', async () => {
        mockFetch(() => ({ enabled: true, minMag: 4 }));
        const mount = vi.fn(async () => {});
        const unmount = vi.fn();

        liveDataSync({}, {
            sectionKey: SECTION_KEY, initialConfig: null, mount, refresh: vi.fn(), unmount,
            syncMs: 1000,
        });
        await vi.advanceTimersByTimeAsync(1000);

        expect(mount).toHaveBeenCalledTimes(1);
        expect(unmount).not.toHaveBeenCalled();
    });

    test('backs out if disabled during the async mount', async () => {
        let configCallCount = 0;
        globalThis.fetch = vi.fn(async (url) => {
            if (String(url).includes('/layer_availability')) {
                return { json: async () => ({ data: {} }) };
            }
            configCallCount += 1;
            // First config fetch (the tick itself) sees enabled=true; the SECOND
            // (onEnable's own recheck, fired after `mount` resolves) sees it flipped
            // to disabled -- simulating a config change that landed mid-mount.
            const enabled = configCallCount === 1;
            return { json: async () => ({ data: { [SECTION_KEY]: { enabled } } }) };
        });
        const mount = vi.fn(async () => {});
        const unmount = vi.fn();

        liveDataSync({}, {
            sectionKey: SECTION_KEY, initialConfig: null, mount, refresh: vi.fn(), unmount,
            syncMs: 1000,
        });
        await vi.advanceTimersByTimeAsync(1000);

        expect(mount).toHaveBeenCalledTimes(1);
        expect(unmount).toHaveBeenCalledTimes(1);
    });
});

describe('steady-state refresh policy', () => {
    async function mountedSync(section, refresh, refreshMs = 60000) {
        mockFetch(() => section);
        liveDataSync({}, {
            sectionKey: SECTION_KEY, initialConfig: null,
            mount: vi.fn(async () => {}), refresh, unmount: vi.fn(),
            syncMs: 1000, refreshMs,
        });
        await vi.advanceTimersByTimeAsync(1000);  // mount tick
        return section;
    }

    test('refreshes immediately when the section signature changes', async () => {
        const refresh = vi.fn(async () => {});
        await mountedSync({ enabled: true, minMag: 4 }, refresh);

        mockFetch(() => ({ enabled: true, minMag: 5 }));
        await vi.advanceTimersByTimeAsync(1000);

        expect(refresh).toHaveBeenCalledTimes(1);
        expect(refresh.mock.calls[0][0]).toEqual({ enabled: true, minMag: 5 });
    });

    test('refreshes on the slow cadence when unchanged', async () => {
        const refresh = vi.fn(async () => {});
        const section = { enabled: true, minMag: 4 };
        await mountedSync(section, refresh, 5000);

        mockFetch(() => section);
        await vi.advanceTimersByTimeAsync(5000);  // past refreshMs, unchanged config

        expect(refresh).toHaveBeenCalledTimes(1);
    });

    test('does not refresh within the cadence when unchanged', async () => {
        const refresh = vi.fn(async () => {});
        const section = { enabled: true, minMag: 4 };
        await mountedSync(section, refresh, 60000);

        mockFetch(() => section);
        await vi.advanceTimersByTimeAsync(1000);  // well under refreshMs

        expect(refresh).not.toHaveBeenCalled();
    });
});
