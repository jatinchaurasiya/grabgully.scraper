"""
core/models.py
──────────────
All Pydantic data models used across the scraper service.
One place, strongly typed, no dicts flying around.
"""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, HttpUrl, field_validator


class Platform(str, Enum):
    AMAZON   = "amazon"
    FLIPKART = "flipkart"
    MYNTRA   = "myntra"
    MEESHO   = "meesho"
    AJIO     = "ajio"
    SNAPDEAL = "snapdeal"
    NYKAA    = "nykaa"


class Category(str, Enum):
    ELECTRONICS = "electronics"
    FASHION     = "fashion"
    HOME        = "home"
    BEAUTY      = "beauty"
    SPORTS      = "sports"
    GROCERY     = "grocery"


# ── Scraped Product ───────────────────────────────────────────────────────────

class ScrapedProduct(BaseModel):
    """Raw product data as returned by any scraper."""
    external_id:    str
    platform:       Platform
    title:          str
    brand:          str        = ""
    image_url:      str        = ""
    current_price:  float
    original_price: float      = 0.0
    discount_pct:   int        = 0
    affiliate_url:  str
    category:       str        = ""
    in_stock:       bool       = True
    rating:         float      = 0.0
    rating_count:   int        = 0

    @field_validator("current_price", "original_price")
    @classmethod
    def price_must_be_positive(cls, v: float) -> float:
        return max(0.0, v)

    @field_validator("discount_pct")
    @classmethod
    def clamp_discount(cls, v: int) -> int:
        return max(0, min(100, v))

    @field_validator("affiliate_url")
    @classmethod
    def url_must_be_non_empty(cls, v: str) -> str:
        if not v or not v.startswith("http"):
            raise ValueError(f"Invalid affiliate URL: {v!r}")
        return v

    def computed_discount(self) -> int:
        """Calculate discount from prices if not provided by scraper."""
        if self.discount_pct:
            return self.discount_pct
        if self.original_price > self.current_price > 0:
            return int((1 - self.current_price / self.original_price) * 100)
        return 0


# ── API Response Models ───────────────────────────────────────────────────────

class DealResponse(BaseModel):
    id:             str
    title:          str
    brand:          str
    image_url:      str
    platform:       str
    current_price:  float
    original_price: float
    discount_pct:   int
    affiliate_url:  str
    category:       str
    in_stock:       bool
    updated_at:     datetime

    class Config:
        from_attributes = True


class PricePoint(BaseModel):
    price:      float
    scraped_at: datetime


class CompareResult(BaseModel):
    product_id:   str
    title:        str
    image_url:    str
    listings:     list[PlatformPrice]
    cheapest:     Optional[PlatformPrice] = None


class PlatformPrice(BaseModel):
    platform:      str
    current_price: float
    original_price: float
    discount_pct:  int
    affiliate_url: str
    in_stock:      bool
    last_updated:  datetime


# ── Internal Scheduler Models ─────────────────────────────────────────────────

class ScrapeJob(BaseModel):
    platform:   Platform
    categories: list[str]
    status:     str = "pending"   # pending | running | done | failed
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    products_found: int = 0
    error:      Optional[str] = None


class PriceAlert(BaseModel):
    """Fired when a tracked product drops to/below target price."""
    user_id:       str
    listing_id:    str
    product_title: str
    platform:      str
    target_price:  float
    current_price: float
    affiliate_url: str
    fcm_token:     Optional[str] = None
