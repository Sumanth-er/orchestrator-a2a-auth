class AuthError(Exception):
    status_code: int = 401
    code: str = "AUTH_ERROR"

    def __init__(self, message: str, **extra):
        super().__init__(message)
        self.message = message
        self.extra = extra

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


class UnauthorizedError(AuthError):
    status_code = 401
    code = "UNAUTHORIZED"


class AccessDeniedError(AuthError):
    status_code = 403
    code = "ACCESS_DENIED"


class TokenExchangeError(AuthError):
    status_code = 403
    code = "TOKEN_EXCHANGE_FAILED"
