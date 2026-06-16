"""utils.py — 小さな共通関数。"""
import datetime as dt


def days_until(date_str: str):
    """'YYYY-MM-DD' までの残り日数。解析できなければ None。"""
    if not date_str or date_str in ("未定", "", None):
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            d = dt.datetime.strptime(str(date_str)[:10], fmt).date()
            return (d - dt.date.today()).days
        except Exception:
            continue
    return None


def safe_float(v, default=0.0):
    try:
        if v is None:
            return float(default)
        return float(v)
    except Exception:
        return float(default)
