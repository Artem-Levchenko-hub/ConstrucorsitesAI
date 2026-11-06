"""Fixed, public-safe errors; never interpolate inputs or underlying exceptions."""


class RecoveryError(Exception):
    code = "RECOVERY_ERROR"
    message = "Recovery operation could not be completed"

    def __init__(self) -> None:
        super().__init__(self.message)


class ProfileInvalid(RecoveryError):
    code = "PROFILE_INVALID"
    message = "Recovery profile could not be authenticated or validated"


class LocalUnlockError(RecoveryError):
    code = "LOCAL_UNLOCK_UNAVAILABLE"
    message = "Secure local unlocking is unavailable"
