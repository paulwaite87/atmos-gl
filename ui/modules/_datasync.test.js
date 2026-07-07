// Smoke tests for liveDataSync's GeoJSON-specific behavior on top of the shared
// reconcileLoop (architecture review candidate "unify the two reconcile engines").
// Confirms the mount-then-recheck-then-maybe-back-out sequence and the
// signature/cadence refresh policy survived the extraction unchanged, driving the
// REAL reconcileLoop end-to-end (not mocked) so this exercises the actual dispatch.
import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { liveDataSync } from './_datasync.js';

const SECTION_KEY = 'quakes';

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
        globalThis.fetch = vi.fn(async () => ({
            json: async () => ({ data: { [SECTION_KEY]: { enabled: true, minMag: 4 } } }),
        }));
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
        let callCount = 0;
        globalThis.fetch = vi.fn(async () => {
            callCount += 1;
            // First fetch (the tick itself) sees enabled=true; the SECOND fetch
            // (onEnable's own recheck, fired after `mount` resolves) sees it flipped
            // to disabled -- simulating a config change that landed mid-mount.
            const enabled = callCount === 1;
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
        globalThis.fetch = vi.fn(async () => ({
            json: async () => ({ data: { [SECTION_KEY]: section } }),
        }));
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

        globalThis.fetch = vi.fn(async () => ({
            json: async () => ({ data: { [SECTION_KEY]: { enabled: true, minMag: 5 } } }),
        }));
        await vi.advanceTimersByTimeAsync(1000);

        expect(refresh).toHaveBeenCalledTimes(1);
        expect(refresh.mock.calls[0][0]).toEqual({ enabled: true, minMag: 5 });
    });

    test('refreshes on the slow cadence when unchanged', async () => {
        const refresh = vi.fn(async () => {});
        const section = { enabled: true, minMag: 4 };
        await mountedSync(section, refresh, 5000);

        globalThis.fetch = vi.fn(async () => ({
            json: async () => ({ data: { [SECTION_KEY]: section } }),
        }));
        await vi.advanceTimersByTimeAsync(5000);  // past refreshMs, unchanged config

        expect(refresh).toHaveBeenCalledTimes(1);
    });

    test('does not refresh within the cadence when unchanged', async () => {
        const refresh = vi.fn(async () => {});
        const section = { enabled: true, minMag: 4 };
        await mountedSync(section, refresh, 60000);

        globalThis.fetch = vi.fn(async () => ({
            json: async () => ({ data: { [SECTION_KEY]: section } }),
        }));
        await vi.advanceTimersByTimeAsync(1000);  // well under refreshMs

        expect(refresh).not.toHaveBeenCalled();
    });
});
