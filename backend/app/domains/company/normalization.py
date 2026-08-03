import re
import unicodedata


_CREDIT_CODE_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_CREDIT_CODE_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)


def normalize_company_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    if not normalized:
        raise ValueError("企业名称不能为空")
    return re.sub(r"\s+", " ", normalized)


def validate_credit_code(value: str) -> str:
    code = unicodedata.normalize("NFKC", value).strip().upper()
    if len(code) != 18 or any(char not in _CREDIT_CODE_CHARS for char in code):
        raise ValueError("统一社会信用代码格式无效")
    total = sum(
        _CREDIT_CODE_CHARS.index(char) * weight
        for char, weight in zip(code[:17], _CREDIT_CODE_WEIGHTS)
    )
    expected = _CREDIT_CODE_CHARS[(31 - total % 31) % 31]
    if code[-1] != expected:
        raise ValueError("统一社会信用代码校验位无效")
    return code


def normalize_credit_code(value: str | None) -> str | None:
    return validate_credit_code(value) if value else None
