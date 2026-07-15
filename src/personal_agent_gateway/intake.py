class IntakeClosedError(RuntimeError):
    pass


class IntakeGate:
    def __init__(self) -> None:
        self._open = True

    @property
    def is_open(self) -> bool:
        return self._open

    def close(self) -> bool:
        changed = self._open
        self._open = False
        return changed

    def open(self) -> bool:
        changed = not self._open
        self._open = True
        return changed

    def require_open(self) -> None:
        if not self._open:
            raise IntakeClosedError("Execution intake is stopped")
