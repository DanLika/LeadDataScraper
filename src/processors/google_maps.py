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

def process_gmaps_df(df, source_label="Google Maps"):
    """Processes a raw Google Maps export DataFrame based on the Colab script logic."""
    
    # 1. Rename columns
    df = df.rename(columns=GMAPS_COLUMNS_TO_RENAME)
    
    # 2. Ensure all target columns exist
    for new_col in GMAPS_COLUMNS_TO_RENAME.values():
        if new_col not in df.columns:
            df[new_col] = np.nan
            
    # 3. Drop uninformative columns
    df = df.drop(columns=COLUMNS_TO_DROP, errors='ignore').copy()

    # 4. Clean specific columns
    if 'Website' in df.columns:
        df['Website'] = df['Website'].apply(clean_website)

    if 'Rating' in df.columns:
        df['Rating'] = pd.to_numeric(
            df['Rating'].astype(str).str.replace(',', '.', regex=False),
            errors='coerce',
        )

    if 'Reviews' in df.columns:
        df['Reviews'] = pd.to_numeric(
            df['Reviews'].astype(str).str.replace(r'[()]', '', regex=True),
            errors='coerce',
        ).astype('Int64')

    if 'Phone' in df.columns:
        df['Phone'] = df['Phone'].apply(clean_phone).fillna('')

    if 'Address' in df.columns:
        df['Address'] = df['Address'].replace(['·', ''], np.nan)

    if 'Category' in df.columns:
        df['Category'] = df['Category'].replace(['·', ''], np.nan)

    # 5. Drop fully empty rows (of essential fields)
    df.dropna(subset=['Name', 'Google Maps Link', 'Website'], how='all', inplace=True)

    # 6. Add metadata columns
    df['EXTRACTED_EMAIL'] = np.nan
    df['SOURCE_FILE'] = source_label

    # 7. Create unique_key
    df['unique_key'] = df['Google Maps Link'].fillna('') + '_' + df['Name'].fillna('')
    df['unique_key'] = df.apply(
        lambda row: row['unique_key'] if str(row['unique_key']).strip('_') != '' 
        else f"gmaps_{source_label.lower().replace(' ', '_')}_{row.name}",
        axis=1
    )

    return df
