// ui/modules/_auth.js
// Google + GitHub sign-in widget (issues #303, #306, #325): a hamburger-style account
// menu into #authWidget, based on GET /api/auth/me. One hamburger icon regardless of
// auth state (so the top-right toolbar's width never changes and #settingsBtn never
// has to reflow around it) opens a dropdown whose CONTENT differs:
//   - Signed in: full name + email header, a divider, then Settings / Sign out.
//   - Signed out: Sign in with Google / Sign in with GitHub.
// Replaces the earlier flat "email + My Settings + Sign out" row.
//
// Also owns #settingsBtn's ("Configuration Control") visibility: hidden unless the
// visitor is a signed-in admin, reusing this same /api/auth/me fetch rather than a
// second one -- unrelated to and untouched by the menu redesign above.

function buildMenu(host) {
    host.innerHTML = '';

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'auth-menu__toggle';
    toggle.setAttribute('aria-haspopup', 'true');
    toggle.setAttribute('aria-expanded', 'false');
    toggle.title = 'Account menu';
    for (let i = 0; i < 3; i++) {
        const bar = document.createElement('span');
        bar.className = 'auth-menu__bar';
        toggle.appendChild(bar);
    }

    const panel = document.createElement('div');
    panel.className = 'auth-menu__panel';
    panel.setAttribute('role', 'menu');

    const close = () => {
        panel.classList.remove('is-open');
        toggle.setAttribute('aria-expanded', 'false');
    };
    const open = () => {
        panel.classList.add('is-open');
        toggle.setAttribute('aria-expanded', 'true');
    };
    toggle.addEventListener('click', () => {
        if (panel.classList.contains('is-open')) close(); else open();
    });
    // Click-outside-to-close: #authWidget contains both the toggle and the panel, so
    // any click still inside it (opening it, or picking a menu item -- the latter
    // also closed explicitly below) is excluded here rather than closed and reopened.
    document.addEventListener('click', (e) => {
        if (!panel.classList.contains('is-open')) return;
        if (host.contains(e.target)) return;
        close();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') close();
    });
    // Any menu item click (a provider link, Settings, or Sign out) closes the panel
    // immediately rather than leaving it visibly open through the ensuing navigation/reload.
    panel.addEventListener('click', close);

    host.appendChild(toggle);
    host.appendChild(panel);
    return panel;
}

function renderSignedIn(panel, state) {
    const header = document.createElement('div');
    header.className = 'auth-menu__header';

    const name = document.createElement('div');
    name.className = 'auth-menu__name';
    name.textContent = state.name || state.email;
    header.appendChild(name);

    const email = document.createElement('div');
    email.className = 'auth-menu__email';
    email.textContent = state.email;
    header.appendChild(email);

    panel.appendChild(header);

    const divider = document.createElement('hr');
    divider.className = 'auth-menu__divider';
    panel.appendChild(divider);

    const settingsLink = document.createElement('a');
    settingsLink.className = 'auth-menu__item';
    settingsLink.href = '/me/settings';
    settingsLink.textContent = '⚙️ Settings';
    panel.appendChild(settingsLink);

    const signOut = document.createElement('button');
    signOut.type = 'button';
    signOut.className = 'auth-menu__item';
    signOut.textContent = '↪ Sign out';
    signOut.addEventListener('click', async () => {
        await fetch('/api/auth/logout', { method: 'POST' });
        window.location.reload();
    });
    panel.appendChild(signOut);
}

function renderSignedOut(panel) {
    const signInGoogle = document.createElement('a');
    signInGoogle.className = 'auth-menu__item';
    signInGoogle.href = '/api/auth/login/google';
    signInGoogle.textContent = 'Sign in with Google';
    panel.appendChild(signInGoogle);

    const signInGithub = document.createElement('a');
    signInGithub.className = 'auth-menu__item';
    signInGithub.href = '/api/auth/login/github';
    signInGithub.textContent = 'Sign in with GitHub';
    panel.appendChild(signInGithub);
}

export async function initAuthWidget() {
    const host = document.getElementById('authWidget');
    const settingsBtn = document.getElementById('settingsBtn');
    if (!host) return;

    const render = (state) => {
        // 'inline-block', not '' -- #settingsBtn's own CSS rule sets display:none as
        // its default, so clearing the inline style would just fall back to that
        // stylesheet rule rather than actually showing it.
        if (settingsBtn) settingsBtn.style.display = (state.authenticated && state.is_admin) ? 'inline-block' : 'none';

        const panel = buildMenu(host);
        if (state.authenticated) {
            renderSignedIn(panel, state);
        } else {
            renderSignedOut(panel);
        }
    };

    try {
        const resp = await fetch('/api/auth/me');
        render(await resp.json());
    } catch (err) {
        console.warn('[auth] could not load sign-in state', err);
    }
}
