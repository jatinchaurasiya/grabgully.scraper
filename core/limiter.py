"""
core/limiter.py
───────────────
SlowAPI rate-limiter singleton.

Defined here (not in main.py) to avoid a circular import:
  main.py imports api/* routers, and api/* files need the limiter.
  If limiter lived in main.py, api/* → main.py would be circular.

Usage:
  from core.limiter import limiter

  @router.get("/path")
  @limiter.limit("60/minute")
  async def handler(request: Request, ...):
      ...
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

# Key function: rate-limit by real client IP.
# Railway sets X-Forwarded-For — slowapi's get_remote_address reads it correctly.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200/minute"],
)
