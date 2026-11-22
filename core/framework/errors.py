class CommandError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(MESSAGES[code])


class LostResponse(Exception):
    pass


MESSAGES = {
    "INVALID_INPUT": "Eingabe ungültig.",
    "FORBIDDEN": "Verweigert.",
    "NOT_FOUND": "Nicht gefunden.",
    "STALE_REVISION": "Revision veraltet.",
    "STALE_RELEASE": "Konfigurations-Release veraltet.",
    "IDEMPOTENCY_CONFLICT": "Idempotenzkonflikt.",
    "APPROVAL_INVALID": "Freigabe ungültig.",
    "INVALID_TRANSITION": "Übergang ungültig.",
    "STORAGE_UNAVAILABLE": "Speicher nicht verfügbar.",
    "CONFLICT": "Datenkonflikt.",
}
