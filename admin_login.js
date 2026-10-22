(() => {
  'use strict';
  const form = document.getElementById('loginForm');
  const error = document.getElementById('loginError');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    error.hidden = true;
    const response = await fetch('/admin/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
    });
    if (response.ok) { window.location.href = '/admin'; return; }
    const payload = await response.json().catch(() => ({}));
    error.textContent = payload.error || 'Sign in failed';
    error.hidden = false;
  });
})();
