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
    
    cleaned = re.sub(r'[^+\d]', '', phone_str)

    # Ensure only a single leading '+' is kept if it was originally there
    if cleaned.startswith('+'):
        cleaned = '+' + cleaned.replace('+', '')
    else:
        cleaned = cleaned.replace('+', '')

    digits_only = re.sub(r'[^\d]', '', cleaned)
    if len(digits_only) >= 7:
        return cleaned
    return np.nan

def process_gmaps_df(df, source_label="Google Maps"):
    """Processes a raw Google Maps export DataFrame based on the Colab script logic."""

    # Defensive copy. Callers can pass a view (sliced from a parent
    # DataFrame), and pandas 2.x emits `FutureWarning:
    # ChainedAssignmentError` on every subsequent column assignment to
    # such a view — silenced today, but in pandas 3.0's Copy-on-Write
    # mode the writes will be no-ops. Forcing a copy here makes
    # downstream column writes (`df['Website'] = ...` etc.) always
    # affect the local frame.
    df = df.copy()

    # 1. Rename columns
    df = df.rename(columns=GMAPS_COLUMNS_TO_RENAME)
    
    # 2. Ensure all target columns exist
    for new_col in GMAPS_COLUMNS_TO_RENAME.values():
        if new_col not in df.columns:
            df[new_col] = np.nan
            
    # 3. Drop uninformative columns
    df = df.drop(columns=COLUMNS_TO_DROP, errors='ignore').copy()

    # 4. Clean specific columns. Use `df.loc[:, col]` rather than `df[col]`
    # so the writes are explicit single-step assignments — pandas' CoW
    # preview mode (and pandas 3.0 default) flags `df[col] = ...` as
    # `FutureWarning: ChainedAssignmentError` when the right-hand side
    # carries any internal reference back to the frame
    # (e.g. `df[col].apply(...)`).
    if 'Website' in df.columns:
        df.loc[:, 'Website'] = df['Website'].apply(clean_website)

    if 'Rating' in df.columns:
        df.loc[:, 'Rating'] = pd.to_numeric(
            df['Rating'].astype(str).str.replace(',', '.', regex=False),
            errors='coerce',
        )

    if 'Reviews' in df.columns:
        df.loc[:, 'Reviews'] = pd.to_numeric(
            df['Reviews'].astype(str).str.replace(r'[()]', '', regex=True),
            errors='coerce',
        ).astype('Int64')

    if 'Phone' in df.columns:
        df.loc[:, 'Phone'] = df['Phone'].apply(clean_phone).fillna('')

    if 'Address' in df.columns:
        df.loc[:, 'Address'] = df['Address'].replace(['·', ''], np.nan)

    if 'Category' in df.columns:
        df.loc[:, 'Category'] = df['Category'].replace(['·', ''], np.nan)

    # 5. Drop fully empty rows. `inplace=True` is deprecated in CoW mode;
    # explicit reassignment is the future-proof equivalent.
    df = df.dropna(subset=['Name', 'Google Maps Link', 'Website'], how='all')

    # 6. Add metadata columns (loc-based for CoW safety)
    df.loc[:, 'EXTRACTED_EMAIL'] = np.nan
    df.loc[:, 'SOURCE_FILE'] = source_label

    # 7. Create unique_key (loc-based for CoW safety — see comment block
    # above on `ChainedAssignmentError` semantics).
    df.loc[:, 'unique_key'] = df['Google Maps Link'].fillna('') + '_' + df['Name'].fillna('')
    df.loc[:, 'unique_key'] = df.apply(
        lambda row: row['unique_key'] if str(row['unique_key']).strip('_') != ''
        else f"gmaps_{source_label.lower().replace(' ', '_')}_{row.name}",
        axis=1
    )

    return df
