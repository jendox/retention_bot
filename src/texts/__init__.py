"""Project text catalog (step 1 towards i18n).

Current approach:
- Group texts by feature/module (e.g. client registration).
- Keep formatting/templating in small functions to avoid scattered f-strings in handlers.

Later, a translator function can be injected into these helpers (gettext/Babel),
without changing handler logic.
"""
