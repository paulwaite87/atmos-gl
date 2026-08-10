// ui/modules/_auth.js
// Google + GitHub sign-in widget (issues #303, #306): renders one link per provider,
// or the signed-in user's email + "Sign out", into #authWidget, based on GET
// /api/auth/me. Just two plain links -- no dropdown/picker, not worth it for two
// options (see the routes/auth.py docstring for the shared backend route).

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
            const signInGoogle = document.createElement('a');
            signInGoogle.href = '/api/auth/login/google';
            signInGoogle.textContent = 'Sign in with Google';
            host.appendChild(signInGoogle);

            const signInGithub = document.createElement('a');
            signInGithub.href = '/api/auth/login/github';
            signInGithub.textContent = 'Sign in with GitHub';
            host.appendChild(signInGithub);
        }
    };

    try {
        const resp = await fetch('/api/auth/me');
        render(await resp.json());
    } catch (err) {
        console.warn('[auth] could not load sign-in state', err);
    }
}
