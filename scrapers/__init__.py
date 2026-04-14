from scrapers.myntra    import MyntraScraper
from scrapers.meesho    import MeeshoScraper
from scrapers.ajio      import AjioScraper
from scrapers.snapdeal  import SnapdealScraper
from scrapers.flipkart  import FlipkartScraper   # added — was missing from original

__all__ = [
    "MyntraScraper",
    "MeeshoScraper",
    "AjioScraper",
    "SnapdealScraper",
    "FlipkartScraper",
]