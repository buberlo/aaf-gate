class CommandError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(MESSAGES[code])


class LostResponse(Exception):
    pass


MESSAGES = {
    "INVALID_INPUT": "Invalid input.",
    "FORBIDDEN": "Denied.",
    "NOT_FOUND": "Not found.",
    "STALE_REVISION": "Revision stale.",
    "STALE_RELEASE": "Configuration release stale.",
    "IDEMPOTENCY_CONFLICT": "Idempotency conflict.",
    "APPROVAL_INVALID": "Approval invalid.",
    "INVALID_TRANSITION": "Transition invalid.",
    "STORAGE_UNAVAILABLE": "Storage unavailable.",
    "CONFLICT": "Data conflict.",
}