// Tests for initAuthWidget (issues #303/#306/#314/#325): a hamburger-style account
// menu replacing the earlier flat "email + My Settings + Sign out" row -- one
// hamburger icon regardless of auth state, with a dropdown panel whose CONTENT
// differs (signed-in: name/email header + Settings/Sign out; signed-out: Google/
// GitHub sign-in links). Also covers #settingsBtn's admin-only visibility gate
// (unchanged from before this redesign). vitest runs in the default "node"
// environment (no jsdom/happy-dom dependency in this repo), so `document` is faked
// minimally here -- same approach _legend.test.js/_keycanvas.test.js take. Interactive
// behaviour (opening/closing the dropdown, click-outside, Escape) is deliberately NOT
// exercised here -- a fake DOM with no real event dispatch/CSS cascade can't catch a
// real interaction bug (see e.g. the .style.display='' bug this file's own history
// hit), so that's verified live via Playwright instead; this file only asserts what
// gets built into the DOM for each auth state.
import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { initAuthWidget } from './_auth.js';

function makeElement(tag) {
    const el = {
        id: '', className: '', style: {}, children: [], textContent: '', type: '',
        href: '', title: '', _tag: tag, _attrs: {},
    };
    el.appendChild = (child) => { el.children.push(child); };
    el.addEventListener = (evt, fn) => { (el._listeners ??= {})[evt] = fn; };
    el.setAttribute = (k, v) => { el._attrs[k] = String(v); };
    el.getAttribute = (k) => el._attrs[k];
    el.classList = {
        add: (c) => {
            const parts = el.className.split(' ').filter(Boolean);
            if (!parts.includes(c)) el.className = [...parts, c].join(' ');
        },
        remove: (c) => {
            el.className = el.className.split(' ').filter((x) => x && x !== c).join(' ');
        },
        contains: (c) => el.className.split(' ').filter(Boolean).includes(c),
    };
    el.contains = (node) => el.children.some((c) => c === node || c.contains(node));
    Object.defineProperty(el, 'innerHTML', {
        get: () => '',
        set: () => { el.children = []; },
    });
    return el;
}

function fakeDocument() {
    const byId = {};
    const authWidget = makeElement('div');
    authWidget.id = 'authWidget';
    byId['authWidget'] = authWidget;
    const settingsBtn = makeElement('a');
    settingsBtn.id = 'settingsBtn';
    byId['settingsBtn'] = settingsBtn;
    return {
        getElementById: (id) => byId[id] || null,
        createElement: makeElement,
        addEventListener: () => {},
        removeEventListener: () => {},
        _authWidget: authWidget,
        _settingsBtn: settingsBtn,
    };
}

// Recursively finds every descendant (any depth) carrying `className` -- the menu
// nests header/divider/items inside the panel, which itself nests inside #authWidget,
// so a shallow .children scan isn't enough.
function findAll(el, className) {
    const out = [];
    for (const c of el.children || []) {
        if ((c.className || '').split(' ').filter(Boolean).includes(className)) out.push(c);
        out.push(...findAll(c, className));
    }
    return out;
}

function findOne(el, className) {
    return findAll(el, className)[0];
}

beforeEach(() => {
    globalThis.document = fakeDocument();
});

afterEach(() => {
    vi.restoreAllMocks();
});

describe('#settingsBtn visibility', () => {
    test('hidden when signed out', async () => {
        globalThis.fetch = vi.fn(async () => ({ json: async () => ({ authenticated: false }) }));
        await initAuthWidget();
        expect(document._settingsBtn.style.display).toBe('none');
    });

    test('hidden when signed in but not an admin', async () => {
        globalThis.fetch = vi.fn(async () => ({
            json: async () => ({ authenticated: true, email: 'visitor@example.com', name: 'Visitor', is_admin: false }),
        }));
        await initAuthWidget();
        expect(document._settingsBtn.style.display).toBe('none');
    });

    test('shown when signed in as an admin', async () => {
        globalThis.fetch = vi.fn(async () => ({
            json: async () => ({ authenticated: true, email: 'admin@example.com', name: 'Admin', is_admin: true }),
        }));
        await initAuthWidget();
        expect(document._settingsBtn.style.display).toBe('inline-block');
    });

    test('tolerates a page with no #settingsBtn element', async () => {
        document._settingsBtn = null;
        document.getElementById = (id) => (id === 'settingsBtn' ? null : document._authWidget);
        globalThis.fetch = vi.fn(async () => ({
            json: async () => ({ authenticated: true, email: 'admin@example.com', name: 'Admin', is_admin: true }),
        }));
        await expect(initAuthWidget()).resolves.not.toThrow();
    });
});

describe('hamburger toggle', () => {
    test('renders exactly one toggle button, regardless of auth state', async () => {
        globalThis.fetch = vi.fn(async () => ({ json: async () => ({ authenticated: false }) }));
        await initAuthWidget();
        const toggles = findAll(document._authWidget, 'auth-menu__toggle');
        expect(toggles.length).toBe(1);
        expect(toggles[0].getAttribute('aria-expanded')).toBe('false');
    });

    test('the dropdown panel starts closed', async () => {
        globalThis.fetch = vi.fn(async () => ({
            json: async () => ({ authenticated: true, email: 'jane@example.com', name: 'Jane Doe', is_admin: false }),
        }));
        await initAuthWidget();
        const panel = findOne(document._authWidget, 'auth-menu__panel');
        expect(panel.classList.contains('is-open')).toBe(false);
    });
});

describe('signed-out menu', () => {
    test('renders both provider sign-in links and no name/email header', async () => {
        globalThis.fetch = vi.fn(async () => ({ json: async () => ({ authenticated: false }) }));
        await initAuthWidget();
        const items = findAll(document._authWidget, 'auth-menu__item');
        const hrefs = items.map((i) => i.href);
        expect(hrefs).toEqual(['/api/auth/login/google', '/api/auth/login/github']);
        expect(findOne(document._authWidget, 'auth-menu__header')).toBeUndefined();
    });
});

describe('signed-in menu', () => {
    test('header shows the full name and email as separate lines', async () => {
        globalThis.fetch = vi.fn(async () => ({
            json: async () => ({ authenticated: true, email: 'jane@example.com', name: 'Jane Doe', is_admin: false }),
        }));
        await initAuthWidget();
        const name = findOne(document._authWidget, 'auth-menu__name');
        const email = findOne(document._authWidget, 'auth-menu__email');
        expect(name.textContent).toBe('Jane Doe');
        expect(email.textContent).toBe('jane@example.com');
    });

    test('falls back to the email in the header when name is missing', async () => {
        globalThis.fetch = vi.fn(async () => ({
            json: async () => ({ authenticated: true, email: 'jane@example.com', name: null, is_admin: false }),
        }));
        await initAuthWidget();
        const name = findOne(document._authWidget, 'auth-menu__name');
        expect(name.textContent).toBe('jane@example.com');
    });

    test('a divider separates the header from the menu items', async () => {
        globalThis.fetch = vi.fn(async () => ({
            json: async () => ({ authenticated: true, email: 'jane@example.com', name: 'Jane Doe', is_admin: false }),
        }));
        await initAuthWidget();
        const panel = findOne(document._authWidget, 'auth-menu__panel');
        expect(panel.children.some((c) => c._tag === 'hr')).toBe(true);
    });

    test('renders a Settings link and a Sign out button, and nothing else', async () => {
        globalThis.fetch = vi.fn(async () => ({
            json: async () => ({ authenticated: true, email: 'jane@example.com', name: 'Jane Doe', is_admin: false }),
        }));
        await initAuthWidget();
        const items = findAll(document._authWidget, 'auth-menu__item');
        expect(items.length).toBe(2);
        const settings = items.find((i) => i.href === '/me/settings');
        const signOut = items.find((i) => i._tag === 'button');
        expect(settings).toBeTruthy();
        expect(signOut).toBeTruthy();
        expect(signOut.textContent).toContain('Sign out');
    });
});
