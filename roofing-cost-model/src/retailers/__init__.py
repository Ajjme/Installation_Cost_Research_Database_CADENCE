"""Retailer-specific scraper modules.

Each retailer module isolates its own page/endpoint logic but is expected to
emit product rows with the shared schema described in
``src.retailers.home_depot.PRODUCT_FIELDS`` so the downstream normalization and
classification code does not need to know which retailer produced a row.
"""
