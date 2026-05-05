import pandas as pd
import numpy as np
import re

# Column mapping from Colab scripts
GMAPS_COLUMNS_TO_RENAME = {
    'hfpxzc href': 'Google Maps Link',
    'qBF1Pd': 'Name',
    'MW4etd': 'Rating',
    'UY7F9': 'Reviews',
    'W4Efsd': 'Category',
    'W4Efsd 3': 'Address',
    'lcr4fd href': 'Website',
    'UsdlK': 'Phone'
}

COLUMNS_TO_DROP = [
    'W4Efsd 2', 'W4Efsd 4', 'W4Efsd 5', 'W4Efsd 6',
    'Cw1rxd', 'R8c4Qb', 'Cw1rxd 2', 'R8c4Qb 2',
    'ah5Ghc', 'M4A5Cf', 'ah5Ghc 2', 'doJOZc', 'W4Efsd 7'
]

def clean_website(url):
    if not isinstance(url, str) or not url.strip():
        return np.nan
    url = url.strip()
    if url.startswith('www.') and not url.startswith('http'):
        return 'http://' + url
    return url

def clean_phone(phone_str):
    if not isinstance(phone_str, str) or not phone_str.strip():
        return np.nan
    cleaned = ''
    if phone_str.startswith('+'):
        cleaned = '+' + re.sub(r'[^+\d]', '', phone_str)
    else:
        cleaned = re.sub(r'[^+\d]', '', phone_str)
    
    digits_only = re.sub(r'[^\d]', '', cleaned)
    if len(digits_only) >= 7:
        return cleaned
    return np.nan

