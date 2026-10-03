"""记忆服务错误类型。"""


class TDAMError(Exception):
    """Gateway 返回业务错误（信封 code != 0）或 4xx。"""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


class TDAMUnavailable(TDAMError):
    """网络错误 / 超时 / 5xx——服务暂时不可用，调用方应静默降级。"""

    def __init__(self, message: str):
        super().__init__(-1, message)


class TDAMAuthError(TDAMError):
    """401/403——鉴权配置错误，调用方应提示并退避。"""
