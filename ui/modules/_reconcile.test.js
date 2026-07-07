// Tests for reconcileLoop, the shared poll-and-reconcile state machine behind
// liveDataSync and liveLayerSync (architecture review candidate "unify the two
// reconcile engines"). This is the actual concurrency-bug fix -- liveLayerSync had
// no busy-lock at all before this extraction -- so the busy-lock/dispatch contract
// gets direct coverage here; each caller's layer-kind-specific hook behavior is
// smoke-tested separately in _datasync.test.js / _refresh.test.js.
import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { reconcileLoop } from './_reconcile.js';

const SECTION_KEY = 'testlayer';

function mockConfig(section) {
    globalThis.fetch = vi.fn(async () => ({
        json: async () => ({ data: { [SECTION_KEY]: section } }),
    }));
}

beforeEach(() => {
    vi.useFakeTimers();
    globalThis.window = { WM_API: 'http://test' };
    vi.spyOn(console, 'log').mockImplementation(() => {});
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
});

describe('initial mount', () => {
    test('routes through onEnable when initialConfig is enabled', async () => {
        const onEnable = vi.fn(async () => true);
        mockConfig({ enabled: true });

        reconcileLoop({}, {
            sectionKey: SECTION_KEY, initialConfig: { enabled: true }, syncMs: 1000,
            onEnable, onDisable: vi.fn(), onTick: vi.fn(),
        });
        await vi.advanceTimersByTimeAsync(0);

        expect(onEnable).toHaveBeenCalledTimes(1);
        expect(onEnable.mock.calls[0][0]).toEqual({ enabled: true });
    });

    test('does not mount when initialConfig is disabled', async () => {
        const onEnable = vi.fn(async () => true);
        mockConfig({ enabled: false });

        reconcileLoop({}, {
            sectionKey: SECTION_KEY, initialConfig: { enabled: false }, syncMs: 1000,
            onEnable, onDisable: vi.fn(), onTick: vi.fn(),
        });
        await vi.advanceTimersByTimeAsync(0);

        expect(onEnable).not.toHaveBeenCalled();
    });

    test('skips entirely when no initialConfig is given', async () => {
        const onEnable = vi.fn(async () => true);
        mockConfig({ enabled: true });

        reconcileLoop({}, {
            sectionKey: SECTION_KEY, initialConfig: null, syncMs: 1000,
            onEnable, onDisable: vi.fn(), onTick: vi.fn(),
        });
        await vi.advanceTimersByTimeAsync(0);

        expect(onEnable).not.toHaveBeenCalled();
    });
});

describe('poll dispatch', () => {
    test('calls onEnable when the section becomes enabled', async () => {
        const onEnable = vi.fn(async () => true);
        mockConfig({ enabled: true });

        reconcileLoop({}, {
            sectionKey: SECTION_KEY, initialConfig: null, syncMs: 1000,
            onEnable, onDisable: vi.fn(), onTick: vi.fn(),
        });
        await vi.advanceTimersByTimeAsync(1000);

        expect(onEnable).toHaveBeenCalledTimes(1);
    });

    test('calls onTick on subsequent polls while enabled and mounted', async () => {
        const onTick = vi.fn(async () => {});
        mockConfig({ enabled: true });

        reconcileLoop({}, {
            sectionKey: SECTION_KEY, initialConfig: null, syncMs: 1000,
            onEnable: vi.fn(async () => true), onDisable: vi.fn(), onTick,
        });
        await vi.advanceTimersByTimeAsync(1000);  // enable transition
        await vi.advanceTimersByTimeAsync(1000);  // steady-state tick
        await vi.advanceTimersByTimeAsync(1000);

        expect(onTick).toHaveBeenCalledTimes(2);
    });

    test('calls onDisable and clears mounted when the section becomes disabled', async () => {
        const onDisable = vi.fn();
        mockConfig({ enabled: true });

        reconcileLoop({}, {
            sectionKey: SECTION_KEY, initialConfig: null, syncMs: 1000,
            onEnable: vi.fn(async () => true), onDisable, onTick: vi.fn(),
        });
        await vi.advanceTimersByTimeAsync(1000);  // mounts

        mockConfig({ enabled: false });
        await vi.advanceTimersByTimeAsync(1000);  // disables

        expect(onDisable).toHaveBeenCalledTimes(1);

        // Confirms mounted was really cleared: a re-enable calls onEnable again,
        // not onTick.
        const onEnable = vi.fn(async () => true);
        mockConfig({ enabled: true });
        await vi.advanceTimersByTimeAsync(1000);
    });

    test('onEnable returning false leaves mounted false (recheck-and-back-out)', async () => {
        const onEnable = vi.fn(async () => false);
        const onTick = vi.fn(async () => {});
        mockConfig({ enabled: true });

        reconcileLoop({}, {
            sectionKey: SECTION_KEY, initialConfig: null, syncMs: 1000,
            onEnable, onDisable: vi.fn(), onTick,
        });
        await vi.advanceTimersByTimeAsync(1000);
        await vi.advanceTimersByTimeAsync(1000);

        // Still not considered mounted -> onEnable is retried, onTick never fires.
        expect(onEnable).toHaveBeenCalledTimes(2);
        expect(onTick).not.toHaveBeenCalled();
    });

    test('a fetch failure calls no hooks at all', async () => {
        globalThis.fetch = vi.fn(async () => { throw new Error('network down'); });
        const onEnable = vi.fn(async () => true);

        reconcileLoop({}, {
            sectionKey: SECTION_KEY, initialConfig: null, syncMs: 1000,
            onEnable, onDisable: vi.fn(), onTick: vi.fn(),
        });
        await vi.advanceTimersByTimeAsync(1000);

        expect(onEnable).not.toHaveBeenCalled();
    });
});

describe('busy-lock', () => {
    test('a slow onTick prevents an overlapping tick from double-dispatching', async () => {
        let resolveTick;
        const onTick = vi.fn(() => new Promise(res => { resolveTick = res; }));
        mockConfig({ enabled: true });

        reconcileLoop({}, {
            sectionKey: SECTION_KEY, initialConfig: null, syncMs: 1000,
            onEnable: vi.fn(async () => true), onDisable: vi.fn(), onTick,
        });
        await vi.advanceTimersByTimeAsync(1000);  // enable transition resolves
        await vi.advanceTimersByTimeAsync(1000);  // onTick #1 starts, hangs (unresolved)
        await vi.advanceTimersByTimeAsync(1000);  // onTick #2 SHOULD be skipped (busy)

        expect(onTick).toHaveBeenCalledTimes(1);
        resolveTick();
    });
});

describe('teardown', () => {
    test('stops further polling', async () => {
        const onTick = vi.fn(async () => {});
        mockConfig({ enabled: true });

        const stop = reconcileLoop({}, {
            sectionKey: SECTION_KEY, initialConfig: null, syncMs: 1000,
            onEnable: vi.fn(async () => true), onDisable: vi.fn(), onTick,
        });
        await vi.advanceTimersByTimeAsync(1000);  // mounts
        stop();
        await vi.advanceTimersByTimeAsync(5000);  // would have ticked 5x if still running

        expect(onTick).not.toHaveBeenCalled();
    });

    test('unmounts if currently mounted', async () => {
        const onDisable = vi.fn();
        mockConfig({ enabled: true });

        const stop = reconcileLoop({}, {
            sectionKey: SECTION_KEY, initialConfig: null, syncMs: 1000,
            onEnable: vi.fn(async () => true), onDisable, onTick: vi.fn(),
        });
        await vi.advanceTimersByTimeAsync(1000);  // mounts
        stop();

        expect(onDisable).toHaveBeenCalledTimes(1);
    });

    test('does not call onDisable if never mounted', async () => {
        const onDisable = vi.fn();
        mockConfig({ enabled: false });

        const stop = reconcileLoop({}, {
            sectionKey: SECTION_KEY, initialConfig: null, syncMs: 1000,
            onEnable: vi.fn(async () => true), onDisable, onTick: vi.fn(),
        });
        stop();

        expect(onDisable).not.toHaveBeenCalled();
    });
});
