"""领域异常：不依赖任何外层框架。"""


class DomainError(Exception):
    """领域错误基类，携带业务错误码与 HTTP 状态。"""

    def __init__(self, message="领域错误", code="DOMAIN_ERROR", status=400):
        self.message, self.code, self.status = message, code, status
        super().__init__(message)


class Denied(DomainError):
    """权限/业务前置条件不满足。"""

    def __init__(self, message="没有访问权限", code="FORBIDDEN", status=403):
        super().__init__(message, code, status)
