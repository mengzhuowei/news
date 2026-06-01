"""共享 Pydantic 验证器 — 避免路由模块间代码重复"""

from pydantic import field_validator


def validate_push_time(cls, v):
    """push_time 格式验证 — HH:MM:SS（兼容 Pydantic v2 classmethod 签名）"""
    if v is not None:
        if len(v) != 8 or v[2] != ':' or v[5] != ':':
            raise ValueError('push_time must be in HH:MM:SS format')
        try:
            hour, minute, second = map(int, v.split(':'))
            if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
                raise ValueError
        except ValueError:
            raise ValueError('push_time must be valid time (HH:MM:SS)')
    return v


def validate_frequency(cls, v):
    """frequency 枚举验证（兼容 Pydantic v2 classmethod 签名）"""
    if v is not None and v not in ('daily', 'weekly'):
        raise ValueError('frequency must be daily or weekly')
    return v
