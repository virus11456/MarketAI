// Only remove MarketAI keys; leave unrelated site preferences alone.
for (const storage of [localStorage, sessionStorage]) {
  for (let i = storage.length - 1; i >= 0; i--) {
    const key = storage.key(i);
    if (key.startsWith('marketai_')) storage.removeItem(key);
  }
}
// Other open tabs clear their own per-tab keys on this event.
localStorage.setItem('marketai_logout_event', String(Date.now()));
localStorage.removeItem('marketai_logout_event');
