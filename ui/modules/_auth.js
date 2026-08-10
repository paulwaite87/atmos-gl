// ui/modules/_auth.js
// Google sign-in widget (issue #303): renders "Sign in with Google" or the signed-in
// user's email + "Sign out" into #authWidget, based on GET /api/auth/me.

export async function initAuthWidget() {
    const host = document.getElementById('authWidget');
    if (!host) return;

    const render = (state) => {
        host.innerHTML = '';
        if (state.authenticated) {
            const label = document.createElement('span');
            label.className = 'auth-widget__email';
            label.textContent = state.email;
            host.appendChild(label);

            // Only shown once signed in (issue #305/#314) -- unlike the always-visible
            // admin "Configuration Control" link, a personal settings page is a dead
            // end for an anonymous visitor, so it's not worth showing at all.
            const settingsLink = document.createElement('a');
            settingsLink.href = '/me/settings';
            settingsLink.textContent = 'My Settings';
            host.appendChild(settingsLink);

            const signOut = document.createElement('button');
            signOut.type = 'button';
            signOut.textContent = 'Sign out';
            signOut.addEventListener('click', async () => {
                await fetch('/api/auth/logout', { method: 'POST' });
                window.location.reload();
            });
            host.appendChild(signOut);
        } else {
            const signIn = document.createElement('a');
            signIn.href = '/api/auth/login/google';
            signIn.textContent = 'Sign in with Google';
            host.appendChild(signIn);
        }
    };

    try {
        const resp = await fetch('/api/auth/me');
        render(await resp.json());
    } catch (err) {
        console.warn('[auth] could not load sign-in state', err);
    }
}
