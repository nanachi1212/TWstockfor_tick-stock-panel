"""Taiwan quant research layer: feature eligibility, regime, walk-forward folds.

Everything here is *model-side*. Market truth lives in
``app/taiwan/pit_universe.py``; this package may read it but never edits it.
"""
