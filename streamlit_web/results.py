"""List-compatible results with request-local diagnostics, never shared globals."""


class SourceUnavailable(RuntimeError):
    """Expected source/network failure; safe message may be shown to a caller."""


class SearchResults(list):
    def __init__(self, items=(), *, errors=(), warnings=(), source_statuses=None):
        super().__init__(items)
        self.errors = list(errors)
        self.warnings = list(warnings)
        self.source_statuses = dict(source_statuses or {})

    @property
    def partial(self):
        return bool(self.errors or self.warnings)

    def diagnostics(self):
        return {
            "errors": self.errors,
            "warnings": self.warnings,
            "partial": self.partial,
            "source_statuses": self.source_statuses,
        }


def diagnostic(source: str, code: str, message: str) -> dict:
    return {"source": source, "code": code, "message": message}
