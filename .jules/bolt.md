## 2025-03-18 - Optimized merge_and_deduplicate with native pd.concat
**Learning:** Manual DataFrame column union and reindexing before concatenation in Pandas is significantly slower and more memory-intensive than just calling `pd.concat(dataframes, ignore_index=True)` natively.
**Action:** Always prefer native Pandas `concat` functions when dealing with multiple dataframes with varying columns; the internal C code handles column alignment perfectly and efficiently.
