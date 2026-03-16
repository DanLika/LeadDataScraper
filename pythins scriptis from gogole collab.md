import pandas as pd
import numpy as np
import os
from IPython.display import display

def load_csv_with_unique_key(filepath, df_name):
    """
    Loads a CSV file, initializes if not found/empty, and ensures a 'UNIQUE_KEY' column exists.
    Also ensures default essential columns are present if initializing.
    """
    df = None
    # Define essential columns for an empty DataFrame to ensure consistency
    essential_cols = ['Name', 'Website', 'EXTRACTED_EMAIL', 'UNIQUE_KEY']

    if not os.path.exists(filepath):
        print(f"⚠️ '{filepath}' not found. Initializing empty DataFrame for {df_name}.")
        df = pd.DataFrame(columns=essential_cols, dtype=str) # Add Name for better reporting
    elif os.path.getsize(filepath) == 0:
        print(f"⚠️ '{filepath}' is empty. Initializing empty DataFrame for {df_name}.")
        df = pd.DataFrame(columns=essential_cols, dtype=str)
    else:
        try:
            df = pd.read_csv(filepath, dtype=str)
            print(f"✅ Successfully loaded {len(df)} leads from '{filepath}' for {df_name}.")
        except pd.errors.EmptyDataError:
            print(f"❌ Error: '{filepath}' has headers but no data. Initializing empty DataFrame for {df_name}.")
            df = pd.DataFrame(columns=essential_cols, dtype=str)
        except Exception as e:
            print(f"❌ An error occurred while loading '{filepath}' for {df_name}: {e}. Initializing empty DataFrame.")
            df = pd.DataFrame(columns=essential_cols, dtype=str)

    # Ensure essential columns exist, even if NaN
    for col in essential_cols:
        if col not in df.columns:
            df[col] = np.nan

    # Generate 'UNIQUE_KEY' if it does not exist or is fully empty
    if 'UNIQUE_KEY' not in df.columns or df['UNIQUE_KEY'].isnull().all() or (df['UNIQUE_KEY'] == '').all():
        print(f"Generating 'UNIQUE_KEY' for {df_name}...")
        if 'Website' in df.columns:
            df['UNIQUE_KEY'] = df['Website'].fillna('')
            if 'EXTRACTED_EMAIL' in df.columns:
                df['UNIQUE_KEY'] = df['UNIQUE_KEY'] + '_' + df['EXTRACTED_EMAIL'].fillna('')
            if 'Name' in df.columns:
                df['UNIQUE_KEY'] = df.apply(lambda row: row['UNIQUE_KEY'] if row['UNIQUE_KEY'].strip('_') != '' else (row['Name'] if pd.notna(row['Name']) else f"idx_{row.name}"), axis=1)
            else:
                df['UNIQUE_KEY'] = df.apply(lambda row: row['UNIQUE_KEY'] if row['UNIQUE_KEY'].strip() != '' else f"idx_{row.name}", axis=1)
        elif 'Name' in df.columns:
            df['UNIQUE_KEY'] = df['Name'].fillna('')
        else:
            df['UNIQUE_KEY'] = df.index.map(lambda x: f"idx_{x}")
        # Final cleanup for UNIQUE_KEY if it's still just an underscore or empty from fillna
        df['UNIQUE_KEY'] = df['UNIQUE_KEY'].replace('^_*$', np.nan, regex=True).fillna(df.index.map(lambda x: f"idx_{x}"))
        print(f"Finished generating 'UNIQUE_KEY' for {df_name}.")

    return df

print("### Loading and Standardizing Existing Lead Files ###\n")

# 1. Define file paths
fajl_with_emails = 'FINALNA_LISTA_SA_EMAILOM.csv'
fajl_without_emails = 'LEADOVI_BEZ_EMAILA.csv'

# 2. Load or initialize df_emails_current
df_emails_current = load_csv_with_unique_key(fajl_with_emails, 'leads with emails')

# 3. Load or initialize df_no_emails_current
df_no_emails_current = load_csv_with_unique_key(fajl_without_emails, 'leads without emails')

print("\n--- Verification of loaded DataFrames ---")

# 4. Display the first 5 rows and column names of both DataFrames
print(f"\nFirst 5 rows of '{fajl_with_emails}':")
display(df_emails_current.head())
print("Columns:", df_emails_current.columns.tolist())
print(f"Total rows: {len(df_emails_current)}")

print(f"\nFirst 5 rows of '{fajl_without_emails}':")
display(df_no_emails_current.head())
print("Columns:", df_no_emails_current.columns.tolist())
print(f"Total rows: {len(df_no_emails_current)}")






===========

import pandas as pd
import numpy as np
import os

print("### Merging new leads with existing files, deduplicating, and updating ###\n")

# Ensure all necessary DataFrames are available
if 'df_new_leads' not in locals() or df_new_leads.empty:
    print("❌ df_new_leads not found or is empty. Please ensure the new leads DataFrame is created.")
elif 'df_emails_current' not in locals() or df_emails_current.empty:
    print("❌ df_emails_current not found or is empty. Please ensure existing leads with emails are loaded.")
elif 'df_no_emails_current' not in locals() or df_no_emails_current.empty:
    print("❌ df_no_emails_current not found or is empty. Please ensure existing leads without emails are loaded.")
else:
    # 1. Identify all unique column names across all three DataFrames
    all_columns = pd.Index([]).union(df_new_leads.columns)
    all_columns = all_columns.union(df_emails_current.columns)
    all_columns = all_columns.union(df_no_emails_current.columns)

    print(f"Unified columns for concatenation: {all_columns.tolist()}\n")

    # 2. Reindex each of these three DataFrames to ensure they all have the identified unified set of columns
    df_new_leads = df_new_leads.reindex(columns=all_columns, fill_value=np.nan)
    df_emails_current = df_emails_current.reindex(columns=all_columns, fill_value=np.nan)
    df_no_emails_current = df_no_emails_current.reindex(columns=all_columns, fill_value=np.nan)

    # 3. Concatenate all leads into a single DataFrame
    df_combined = pd.concat([df_new_leads, df_emails_current, df_no_emails_current], ignore_index=True)
    print(f"✅ All leads combined. Initial total rows: {len(df_combined)}")

    # 4. Deduplicate the combined DataFrame
    # Ensure UNIQUE_KEY is present for deduplication (it should be from previous steps)
    if 'UNIQUE_KEY' not in df_combined.columns:
        print("❌ 'UNIQUE_KEY' missing in combined DataFrame. Cannot deduplicate effectively.")
        # Fallback to Name + Website if UNIQUE_KEY is critically missing
        if 'Name' in df_combined.columns and 'Website' in df_combined.columns:
            df_combined['UNIQUE_KEY'] = df_combined['Name'].fillna('') + '_' + df_combined['Website'].fillna('')
            df_combined['UNIQUE_KEY'] = df_combined.apply(lambda row: row['UNIQUE_KEY'] if row['UNIQUE_KEY'].strip('_') != '' else f"dedup_idx_{row.name}", axis=1)
        else:
             df_combined['UNIQUE_KEY'] = df_combined.index.map(lambda x: f"dedup_idx_{x}")

    df_deduplicated_final = df_combined.drop_duplicates(subset=['UNIQUE_KEY'], keep='first').reset_index(drop=True)
    print(f"✅ Deduplication complete. Final unique rows: {len(df_deduplicated_final)}. Removed {len(df_combined) - len(df_deduplicated_final)} duplicates.")

    # 5. Display the first 5 rows of df_deduplicated_final
    print("\nFirst 5 rows of df_deduplicated_final:")
    display(df_deduplicated_final.head())

    # Optionally, you can also display columns to confirm consistency
    print("\nFinal columns in df_deduplicated_final:")
    print(df_deduplicated_final.columns.tolist())


print("\n### Merging and deduplication process completed. ###")



==========

import numpy as np
import re
from urllib.parse import urlparse

# 1. Create a dictionary columns_to_rename with corrected mappings
columns_to_rename = {
    'hfpxzc href': 'Google Maps Link',
    'qBF1Pd': 'Name',
    'MW4etd': 'Rating',
    'UY7F9': 'Reviews',
    'W4Efsd': 'Category',
    'W4Efsd 3': 'Address',     # Corrected: W4Efsd 3 is Address
    'lcr4fd href': 'Website',  # Corrected: lcr4fd href is Website
    'UsdlK': 'Phone'           # Corrected: UsdlK is Phone
}

# 2. Rename the columns in df_gmaps_raw
df_gmaps_processed = df_gmaps_raw.rename(columns=columns_to_rename)

# 3. Clean the 'Website' column (now from 'lcr4fd href')
def clean_website_column(url):
    if not isinstance(url, str) or not url.strip():
        return np.nan
    url = url.strip()
    if url.startswith('www.') and not url.startswith('http'):
        return 'http://' + url
    return url

df_gmaps_processed['Website'] = df_gmaps_processed['Website'].apply(clean_website_column)

# 4. Clean the 'Rating' column
df_gmaps_processed['Rating'] = df_gmaps_processed['Rating'].str.replace(',', '.', regex=False).astype(float, errors='ignore')

# 5. Clean the 'Reviews' column
df_gmaps_processed['Reviews'] = df_gmaps_processed['Reviews'].str.replace(r'[()]', '', regex=True).astype(int, errors='ignore')

# 6. Clean the 'Phone' column (now from 'UsdlK')
def clean_and_validate_phone(phone_str):
    if not isinstance(phone_str, str) or not phone_str.strip():
        return np.nan
    cleaned = ''
    if phone_str.startswith('+'):
        cleaned = '+' + re.sub(r'[^\d]', '', phone_str[1:])
    else:
        cleaned = re.sub(r'[^\d]', '', phone_str)
    if len(re.sub(r'[^\d]', '', cleaned)) >= 7:
        return cleaned
    return np.nan

df_gmaps_processed['Phone'] = df_gmaps_processed['Phone'].apply(clean_and_validate_phone)
df_gmaps_processed['Phone'] = df_gmaps_processed['Phone'].fillna('')

# 7. Clean the 'Address' column (now from 'W4Efsd 3')
df_gmaps_processed['Address'] = df_gmaps_processed['Address'].replace('·', np.nan)
df_gmaps_processed['Address'] = df_gmaps_processed['Address'].replace('', np.nan)

# Display the first 5 rows and the column names
print("DataFrame after cleaning and renaming:")
display(df_gmaps_processed.head())
print("Updated Columns:", df_gmaps_processed.columns.tolist())




======


import numpy as np

# List of columns to drop - these are either raw, temporary, or uninformative
# Updated to reflect new column mappings
columns_to_drop = [
    'W4Efsd 2', # Previously incorrectly mapped to Address, now discarding.
    'W4Efsd 4', # Previously incorrectly mapped to Phone, now discarding.
    'W4Efsd 5', 'W4Efsd 6', # Other uninformative columns
    'Cw1rxd', 'R8c4Qb', 'Cw1rxd 2', 'R8c4Qb 2',
    'ah5Ghc', 'M4A5Cf', 'ah5Ghc 2', 'doJOZc', 'W4Efsd 7'
]

# Drop the specified columns from the DataFrame
# Use .copy() to ensure we are working on a new DataFrame slice and avoid SettingWithCopyWarning
df_gmaps_processed = df_gmaps_processed.drop(columns=columns_to_drop, errors='ignore').copy()

# Clean the 'Address' column again by replacing '·' (which seems to be a placeholder for empty) with NaN
df_gmaps_processed['Address'] = df_gmaps_processed['Address'].replace('·', np.nan)

# Also, clean 'Category' column which might contain '·' as an empty placeholder
df_gmaps_processed['Category'] = df_gmaps_processed['Category'].replace('·', np.nan)


# Drop rows where 'Name', 'Google Maps Link', and 'Website' are all NaN
# These rows are likely completely empty entries or headers that weren't properly parsed.
df_gmaps_processed.dropna(subset=['Name', 'Google Maps Link', 'Website'], how='all', inplace=True)

# Ensure 'Phone' column has empty strings for NaN values for consistency, as per original instruction 6.
df_gmaps_processed['Phone'] = df_gmaps_processed['Phone'].fillna('')

# Display the first 5 rows and the column names of the DataFrame after these cleaning and dropping operations
print("DataFrame after dropping unnecessary columns and further cleaning:")
display(df_gmaps_processed.head())
print("Current Columns:", df_gmaps_processed.columns.tolist())




====



import os

# Define the desired order of columns
desired_columns_order = [
    'Google Maps Link',
    'Name',
    'Rating',
    'Reviews',
    'Category',
    'Address',
    'Website',
    'Phone'
]

# Reindex the DataFrame to the desired order
# Use 'errors="ignore"' to handle cases where a desired column might be missing (though it shouldn't be here)
df_gmaps_processed = df_gmaps_processed.reindex(columns=desired_columns_order, copy=False)

# Define the output file name
output_file_cleaned = 'cleaned_gmaps_data.csv'

# Save the processed DataFrame to a new CSV file
df_gmaps_processed.to_csv(output_file_cleaned, index=False)

print(f"✅ Processed data saved to '{output_file_cleaned}'.")

# Display the first 5 rows and the column names of the final DataFrame
print("\nFinal DataFrame after column reordering and saving:")
display(df_gmaps_processed.head())
print("Final Columns:", df_gmaps_processed.columns.tolist())



===

import pandas as pd

# Define the filename
cleaned_gmaps_file = 'cleaned_gmaps_data.csv'

# Load the CSV file into a pandas DataFrame, ensuring all columns are read as strings
df_gmaps_cleaned_data = pd.read_csv(cleaned_gmaps_file, dtype=str)

# Display the first 5 rows of the df_gmaps_cleaned_data DataFrame
print("First 5 rows of cleaned_gmaps_data:")
df_gmaps_cleaned_data.head()

# Display a summary of the df_gmaps_cleaned_data DataFrame
print("\nDataFrame Info:")
df_gmaps_cleaned_data.info()



=====

import numpy as np

# 1. Initialize EXTRACTED_EMAIL column with NaN
df_gmaps_cleaned_data['EXTRACTED_EMAIL'] = np.nan

# 2. Add SOURCE_FILE column with 'Google Maps' value
df_gmaps_cleaned_data['SOURCE_FILE'] = 'Google Maps'

# 3. Create UNIQUE_KEY by concatenating 'Google Maps Link' and 'Name', handling NaNs and providing a fallback
df_gmaps_cleaned_data['UNIQUE_KEY'] = df_gmaps_cleaned_data['Google Maps Link'].fillna('') + '_' + df_gmaps_cleaned_data['Name'].fillna('')

# Handle cases where UNIQUE_KEY might still be empty (e.g., both 'Google Maps Link' and 'Name' were NaN)
df_gmaps_cleaned_data['UNIQUE_KEY'] = df_gmaps_cleaned_data.apply(
    lambda row: row['UNIQUE_KEY'] if row['UNIQUE_KEY'].strip('_') != '' else f"gmaps_idx_{row.name}",
    axis=1
)

# 4. Convert 'Rating' to float, coercing errors to NaN
df_gmaps_cleaned_data['Rating'] = pd.to_numeric(df_gmaps_cleaned_data['Rating'], errors='coerce')

# 5. Convert 'Reviews' to int, coercing errors to NaN
df_gmaps_cleaned_data['Reviews'] = pd.to_numeric(df_gmaps_cleaned_data['Reviews'], errors='coerce').astype('Int64') # Using 'Int64' to allow for NaN in integer column

# 6. Replace empty strings or '·' with NaN in specified columns
columns_to_clean = ['Address', 'Category', 'Phone', 'Website']
for col in columns_to_clean:
    if col in df_gmaps_cleaned_data.columns:
        df_gmaps_cleaned_data[col] = df_gmaps_cleaned_data[col].replace(['', '·'], np.nan)

# 7. Display head and info of the DataFrame
print("First 5 rows of df_gmaps_cleaned_data after standardization:")
df_gmaps_cleaned_data.head()

print("\nDataFrame Info after standardization:")
df_gmaps_cleaned_data.info()


======


import pandas as pd
import numpy as np
import re
from urllib.parse import urlparse

# --- Step 1: Load the 'google (1).csv' file ---
input_file_1 = 'google (1).csv'
df_gmaps_raw_1 = pd.read_csv(input_file_1, dtype=str)

# --- Step 2: Rename the generic columns ---
# Using the same mapping from previous steps
columns_to_rename = {
    'hfpxzc href': 'Google Maps Link',
    'qBF1Pd': 'Name',
    'MW4etd': 'Rating',
    'UY7F9': 'Reviews',
    'W4Efsd': 'Category',
    'W4Efsd 3': 'Address',
    'lcr4fd href': 'Website',
    'UsdlK': 'Phone'
}
df_gmaps_processed_1 = df_gmaps_raw_1.rename(columns=columns_to_rename)

# --- Ensure all target columns exist after renaming, filling with NaN if not present ---
# This prevents KeyError if an original column was missing in the raw data
for new_col_name in columns_to_rename.values():
    if new_col_name not in df_gmaps_processed_1.columns:
        df_gmaps_processed_1[new_col_name] = np.nan

# --- Step 3: Clean the 'Website' column ---
def clean_website_column(url):
    if not isinstance(url, str) or not url.strip():
        return np.nan
    url = url.strip()
    if url.startswith('www.') and not url.startswith('http'):
        return 'http://' + url
    return url

df_gmaps_processed_1['Website'] = df_gmaps_processed_1['Website'].apply(clean_website_column)

# --- Step 4: Clean the 'Rating' column ---
df_gmaps_processed_1['Rating'] = df_gmaps_processed_1['Rating'].str.replace(',', '.', regex=False).astype(float, errors='ignore')

# --- Step 5: Clean the 'Reviews' column ---
df_gmaps_processed_1['Reviews'] = df_gmaps_processed_1['Reviews'].str.replace(r'[()]', '', regex=True)
df_gmaps_processed_1['Reviews'] = pd.to_numeric(df_gmaps_processed_1['Reviews'], errors='coerce').astype('Int64') # Using 'Int64' to allow for NaN in integer column

# --- Step 6: Clean the 'Phone' column ---
def clean_and_validate_phone(phone_str):
    if not isinstance(phone_str, str) or not phone_str.strip():
        return np.nan
    cleaned = ''
    if phone_str.startswith('+'):
        cleaned = '+' + re.sub(r'[^+\d]', '', phone_str) # Keep '+' at start, remove other non-digits
    else:
        cleaned = re.sub(r'[^+\d]', '', phone_str) # Remove non-digits
    if len(re.sub(r'[^+\d]', '', cleaned)) >= 7: # Validate length after cleaning
        return cleaned
    return np.nan

df_gmaps_processed_1['Phone'] = df_gmaps_processed_1['Phone'].apply(clean_and_validate_phone)
df_gmaps_processed_1['Phone'] = df_gmaps_processed_1['Phone'].fillna('')

# --- Step 7: Clean the 'Address' and 'Category' columns ---
df_gmaps_processed_1['Address'] = df_gmaps_processed_1['Address'].replace(['·', ''], np.nan)
df_gmaps_processed_1['Category'] = df_gmaps_processed_1['Category'].replace(['·', ''], np.nan)

# --- Step 8: Drop unnecessary columns and rows ---
# Using the same list of columns to drop from previous steps
columns_to_drop = [
    'W4Efsd 2',
    'W4Efsd 4',
    'W4Efsd 5', 'W4Efsd 6',
    'Cw1rxd', 'R8c4Qb', 'Cw1rxd 2', 'R8c4Qb 2',
    'ah5Ghc', 'M4A5Cf', 'ah5Ghc 2', 'doJOZc', 'W4Efsd 7'
]
# Ensure to create a copy after dropping for safe modification
df_gmaps_processed_1 = df_gmaps_processed_1.drop(columns=columns_to_drop, errors='ignore').copy()

# Drop rows where 'Name', 'Google Maps Link', and 'Website' are all NaN
df_gmaps_processed_1.dropna(subset=['Name', 'Google Maps Link', 'Website'], how='all', inplace=True)

# --- Step 9: Reorder the columns ---
desired_columns_order = [
    'Google Maps Link',
    'Name',
    'Rating',
    'Reviews',
    'Category',
    'Address',
    'Website',
    'Phone'
]
df_gmaps_processed_1 = df_gmaps_processed_1.reindex(columns=desired_columns_order, copy=False)

# --- Step 10: Add EXTRACTED_EMAIL column ---
df_gmaps_processed_1['EXTRACTED_EMAIL'] = np.nan

# --- Step 11: Add SOURCE_FILE column ---
df_gmaps_processed_1['SOURCE_FILE'] = 'Google Maps 1'

# --- Step 12: Create UNIQUE_KEY column ---
df_gmaps_processed_1['UNIQUE_KEY'] = df_gmaps_processed_1['Google Maps Link'].fillna('') + '_' + df_gmaps_processed_1['Name'].fillna('')

df_gmaps_processed_1['UNIQUE_KEY'] = df_gmaps_processed_1.apply(
    lambda row: row['UNIQUE_KEY'] if row['UNIQUE_KEY'].strip('_') != '' else f"gmaps1_idx_{row.name}",
    axis=1
)

# --- Step 13: Display the first 5 rows and info ---
print("First 5 rows of df_gmaps_processed_1 after all operations:")
display(df_gmaps_processed_1.head())

print("\nDataFrame Info after all operations:")
df_gmaps_processed_1.info()



=======

import pandas as pd
import numpy as np
import re
from urllib.parse import urlparse

# --- Step 1: Load the 'google (2).csv' file ---
input_file_2 = 'google (2).csv'
df_gmaps_raw_2 = pd.read_csv(input_file_2, dtype=str)

# --- Step 2: Rename the generic columns ---
# Using the same mapping from previous steps
columns_to_rename = {
    'hfpxzc href': 'Google Maps Link',
    'qBF1Pd': 'Name',
    'MW4etd': 'Rating',
    'UY7F9': 'Reviews',
    'W4Efsd': 'Category',
    'W4Efsd 3': 'Address',
    'lcr4fd href': 'Website',
    'UsdlK': 'Phone'
}
df_gmaps_processed_2 = df_gmaps_raw_2.rename(columns=columns_to_rename)

# --- Step 3: Ensure all target columns exist after renaming, filling with NaN if not present ---
# This prevents KeyError if an original column was missing in the raw data
for new_col_name in columns_to_rename.values():
    if new_col_name not in df_gmaps_processed_2.columns:
        df_gmaps_processed_2[new_col_name] = np.nan

# --- Step 4: Clean the 'Website' column ---
def clean_website_column(url):
    if not isinstance(url, str) or not url.strip():
        return np.nan
    url = url.strip()
    if url.startswith('www.') and not url.startswith('http'):
        return 'http://' + url
    return url

df_gmaps_processed_2['Website'] = df_gmaps_processed_2['Website'].apply(clean_website_column)

# --- Step 5: Clean the 'Rating' column ---
df_gmaps_processed_2['Rating'] = df_gmaps_processed_2['Rating'].str.replace(',', '.', regex=False).astype(float, errors='ignore')

# --- Step 6: Clean the 'Reviews' column ---
df_gmaps_processed_2['Reviews'] = df_gmaps_processed_2['Reviews'].str.replace(r'[()]', '', regex=True)
df_gmaps_processed_2['Reviews'] = pd.to_numeric(df_gmaps_processed_2['Reviews'], errors='coerce').astype('Int64') # Using 'Int64' to allow for NaN in integer column

# --- Step 7: Clean the 'Phone' column ---
def clean_and_validate_phone(phone_str):
    if not isinstance(phone_str, str) or not phone_str.strip():
        return np.nan
    cleaned = ''
    if phone_str.startswith('+'):
        cleaned = '+' + re.sub(r'[^\d]', '', phone_str[1:]) # Keep '+' at start, remove other non-digits
    else:
        cleaned = re.sub(r'[^\d]', '', phone_str) # Remove non-digits
    if len(re.sub(r'[^\d]', '', cleaned)) >= 7: # Validate length after cleaning
        return cleaned
    return np.nan

df_gmaps_processed_2['Phone'] = df_gmaps_processed_2['Phone'].apply(clean_and_validate_phone)
df_gmaps_processed_2['Phone'] = df_gmaps_processed_2['Phone'].fillna('')

# --- Step 8: Clean the 'Address' and 'Category' columns ---
df_gmaps_processed_2['Address'] = df_gmaps_processed_2['Address'].replace(['·', ''], np.nan)
df_gmaps_processed_2['Category'] = df_gmaps_processed_2['Category'].replace(['·', ''], np.nan)

# --- Step 9: Drop unnecessary columns and rows ---
# Using the same list of columns to drop from previous steps
columns_to_drop = [
    'W4Efsd 2',
    'W4Efsd 4',
    'W4Efsd 5', 'W4Efsd 6',
    'Cw1rxd', 'R8c4Qb', 'Cw1rxd 2', 'R8c4Qb 2',
    'ah5Ghc', 'M4A5Cf', 'ah5Ghc 2', 'doJOZc', 'W4Efsd 7'
]
# Ensure to create a copy after dropping for safe modification
df_gmaps_processed_2 = df_gmaps_processed_2.drop(columns=columns_to_drop, errors='ignore').copy()

df_gmaps_processed_2.dropna(subset=['Name', 'Google Maps Link', 'Website'], how='all', inplace=True)

# --- Step 10: Reorder the columns ---
desired_columns_order = [
    'Google Maps Link',
    'Name',
    'Rating',
    'Reviews',
    'Category',
    'Address',
    'Website',
    'Phone'
]
df_gmaps_processed_2 = df_gmaps_processed_2.reindex(columns=desired_columns_order, copy=False)

# --- Step 11: Add EXTRACTED_EMAIL column ---
df_gmaps_processed_2['EXTRACTED_EMAIL'] = np.nan

# --- Step 12: Add SOURCE_FILE column ---
df_gmaps_processed_2['SOURCE_FILE'] = 'Google Maps 2'

# --- Step 13: Create UNIQUE_KEY column ---
df_gmaps_processed_2['UNIQUE_KEY'] = df_gmaps_processed_2['Google Maps Link'].fillna('') + '_' + df_gmaps_processed_2['Name'].fillna('')

df_gmaps_processed_2['UNIQUE_KEY'] = df_gmaps_processed_2.apply(
    lambda row: row['UNIQUE_KEY'] if row['UNIQUE_KEY'].strip('_') != '' else f"gmaps2_idx_{row.name}",
    axis=1
)

# --- Step 14: Display the first 5 rows and info ---
print("First 5 rows of df_gmaps_processed_2 after all operations:")
display(df_gmaps_processed_2.head())

print("\nDataFrame Info after all operations:")
df_gmaps_processed_2.info()



=====

import pandas as pd
import numpy as np
import re
from urllib.parse import urlparse

# --- Step 1: Load the 'google (3).csv' file ---
input_file_3 = 'google (3).csv'
df_gmaps_raw_3 = pd.read_csv(input_file_3, dtype=str)

# --- Step 2: Rename the generic columns ---
# Using the same mapping from previous steps
columns_to_rename = {
    'hfpxzc href': 'Google Maps Link',
    'qBF1Pd': 'Name',
    'MW4etd': 'Rating',
    'UY7F9': 'Reviews',
    'W4Efsd': 'Category',
    'W4Efsd 3': 'Address',
    'lcr4fd href': 'Website',
    'UsdlK': 'Phone'
}
df_gmaps_processed_3 = df_gmaps_raw_3.rename(columns=columns_to_rename)

# --- Step 3: Ensure all target columns exist after renaming, filling with NaN if not present ---
# This prevents KeyError if an original column was missing in the raw data
for new_col_name in columns_to_rename.values():
    if new_col_name not in df_gmaps_processed_3.columns:
        df_gmaps_processed_3[new_col_name] = np.nan

# --- Step 4: Clean the 'Website' column ---
def clean_website_column(url):
    if not isinstance(url, str) or not url.strip():
        return np.nan
    url = url.strip()
    if url.startswith('www.') and not url.startswith('http'):
        return 'http://' + url
    return url

df_gmaps_processed_3['Website'] = df_gmaps_processed_3['Website'].apply(clean_website_column)

# --- Step 5: Clean the 'Rating' column ---
df_gmaps_processed_3['Rating'] = df_gmaps_processed_3['Rating'].str.replace(',', '.', regex=False).astype(float, errors='ignore')

# --- Step 6: Clean the 'Reviews' column ---
df_gmaps_processed_3['Reviews'] = df_gmaps_processed_3['Reviews'].str.replace(r'[()]', '', regex=True)
df_gmaps_processed_3['Reviews'] = pd.to_numeric(df_gmaps_processed_3['Reviews'], errors='coerce').astype('Int64') # Using 'Int64' to allow for NaN in integer column

# --- Step 7: Clean the 'Phone' column ---
def clean_and_validate_phone(phone_str):
    if not isinstance(phone_str, str) or not phone_str.strip():
        return np.nan
    cleaned = ''
    if phone_str.startswith('+'):
        cleaned = '+' + re.sub(r'[^+\d]', '', phone_str) # Keep '+' at start, remove other non-digits
    else:
        cleaned = re.sub(r'[^+\d]', '', phone_str) # Remove non-digits
    if len(re.sub(r'[^+\d]', '', cleaned)) >= 7: # Validate length after cleaning
        return cleaned
    return np.nan

df_gmaps_processed_3['Phone'] = df_gmaps_processed_3['Phone'].apply(clean_and_validate_phone)
df_gmaps_processed_3['Phone'] = df_gmaps_processed_3['Phone'].fillna('')

# --- Step 8: Clean the 'Address' and 'Category' columns ---
df_gmaps_processed_3['Address'] = df_gmaps_processed_3['Address'].replace(['·', ''], np.nan)
df_gmaps_processed_3['Category'] = df_gmaps_processed_3['Category'].replace(['·', ''], np.nan)

# --- Step 9: Drop unnecessary columns and rows ---
# Using the same list of columns to drop from previous steps
columns_to_drop = [
    'W4Efsd 2',
    'W4Efsd 4',
    'W4Efsd 5', 'W4Efsd 6',
    'Cw1rxd', 'R8c4Qb', 'Cw1rxd 2', 'R8c4Qb 2',
    'ah5Ghc', 'M4A5Cf', 'ah5Ghc 2', 'doJOZc', 'W4Efsd 7'
]
# Ensure to create a copy after dropping for safe modification
df_gmaps_processed_3 = df_gmaps_processed_3.drop(columns=columns_to_drop, errors='ignore').copy()

df_gmaps_processed_3.dropna(subset=['Name', 'Google Maps Link', 'Website'], how='all', inplace=True)

# --- Step 10: Reorder the columns ---
desired_columns_order = [
    'Google Maps Link',
    'Name',
    'Rating',
    'Reviews',
    'Category',
    'Address',
    'Website',
    'Phone'
]
df_gmaps_processed_3 = df_gmaps_processed_3.reindex(columns=desired_columns_order, copy=False)

# --- Step 11: Add EXTRACTED_EMAIL column ---
df_gmaps_processed_3['EXTRACTED_EMAIL'] = np.nan

# --- Step 12: Add SOURCE_FILE column ---
df_gmaps_processed_3['SOURCE_FILE'] = 'Google Maps 3'

# --- Step 13: Create UNIQUE_KEY column ---
df_gmaps_processed_3['UNIQUE_KEY'] = df_gmaps_processed_3['Google Maps Link'].fillna('') + '_' + df_gmaps_processed_3['Name'].fillna('')

df_gmaps_processed_3['UNIQUE_KEY'] = df_gmaps_processed_3.apply(
    lambda row: row['UNIQUE_KEY'] if row['UNIQUE_KEY'].strip('_') != '' else f"gmaps3_idx_{row.name}",
    axis=1
)

# --- Step 14: Display the first 5 rows and info ---
print("First 5 rows of df_gmaps_processed_3 after all operations:")
display(df_gmaps_processed_3.head())

print("\nDataFrame Info after all operations:")
df_gmaps_processed_3.info()


======

import pandas as pd
import numpy as np
import re
from urllib.parse import urlparse

# --- Step 1: Load the 'google (4).csv' file ---
input_file_4 = 'google (4).csv'
df_gmaps_raw_4 = pd.read_csv(input_file_4, dtype=str)

# --- Step 2: Rename the generic columns ---
# Using the same mapping from previous steps
columns_to_rename = {
    'hfpxzc href': 'Google Maps Link',
    'qBF1Pd': 'Name',
    'MW4etd': 'Rating',
    'UY7F9': 'Reviews',
    'W4Efsd': 'Category',
    'W4Efsd 3': 'Address',
    'lcr4fd href': 'Website',
    'UsdlK': 'Phone'
}
df_gmaps_processed_4 = df_gmaps_raw_4.rename(columns=columns_to_rename)

# --- Step 3: Ensure all target columns exist after renaming, filling with NaN if not present ---
# This prevents KeyError if an original column was missing in the raw data
for new_col_name in columns_to_rename.values():
    if new_col_name not in df_gmaps_processed_4.columns:
        df_gmaps_processed_4[new_col_name] = np.nan

# --- Step 4: Clean the 'Website' column ---
def clean_website_column(url):
    if not isinstance(url, str) or not url.strip():
        return np.nan
    url = url.strip()
    if url.startswith('www.') and not url.startswith('http'):
        return 'http://' + url
    return url

df_gmaps_processed_4['Website'] = df_gmaps_processed_4['Website'].apply(clean_website_column)

# --- Step 5: Clean the 'Rating' column ---
df_gmaps_processed_4['Rating'] = df_gmaps_processed_4['Rating'].str.replace(',', '.', regex=False).astype(float, errors='ignore')

# --- Step 6: Clean the 'Reviews' column ---
df_gmaps_processed_4['Reviews'] = df_gmaps_processed_4['Reviews'].str.replace(r'[()]', '', regex=True)
df_gmaps_processed_4['Reviews'] = pd.to_numeric(df_gmaps_processed_4['Reviews'], errors='coerce').astype('Int64') # Using 'Int64' to allow for NaN in integer column

# --- Step 7: Clean the 'Phone' column ---
def clean_and_validate_phone(phone_str):
    if not isinstance(phone_str, str) or not phone_str.strip():
        return np.nan
    cleaned = ''
    if phone_str.startswith('+'):
        cleaned = '+' + re.sub(r'[^+\d]', '', phone_str) # Keep '+' at start, remove other non-digits
    else:
        cleaned = re.sub(r'[^+\d]', '', phone_str) # Remove non-digits
    if len(re.sub(r'[^\d]', '', cleaned)) >= 7: # Validate length after cleaning
        return cleaned
    return np.nan

df_gmaps_processed_4['Phone'] = df_gmaps_processed_4['Phone'].apply(clean_and_validate_phone)
df_gmaps_processed_4['Phone'] = df_gmaps_processed_4['Phone'].fillna('')

# --- Step 8: Clean the 'Address' and 'Category' columns ---
df_gmaps_processed_4['Address'] = df_gmaps_processed_4['Address'].replace(['·', ''], np.nan)
df_gmaps_processed_4['Category'] = df_gmaps_processed_4['Category'].replace(['·', ''], np.nan)

# --- Step 9: Drop unnecessary columns and rows ---
# Using the same list of columns to drop from previous steps
columns_to_drop = [
    'W4Efsd 2',
    'W4Efsd 4',
    'W4Efsd 5', 'W4Efsd 6',
    'Cw1rxd', 'R8c4Qb', 'Cw1rxd 2', 'R8c4Qb 2',
    'ah5Ghc', 'M4A5Cf', 'ah5Ghc 2', 'doJOZc', 'W4Efsd 7'
]
# Ensure to create a copy after dropping for safe modification
df_gmaps_processed_4 = df_gmaps_processed_4.drop(columns=columns_to_drop, errors='ignore').copy()

df_gmaps_processed_4.dropna(subset=['Name', 'Google Maps Link', 'Website'], how='all', inplace=True)

# --- Step 10: Reorder the columns ---
desired_columns_order = [
    'Google Maps Link',
    'Name',
    'Rating',
    'Reviews',
    'Category',
    'Address',
    'Website',
    'Phone'
]
df_gmaps_processed_4 = df_gmaps_processed_4.reindex(columns=desired_columns_order, copy=False)

# --- Step 11: Add EXTRACTED_EMAIL column ---
df_gmaps_processed_4['EXTRACTED_EMAIL'] = np.nan

# --- Step 12: Add SOURCE_FILE column ---
df_gmaps_processed_4['SOURCE_FILE'] = 'Google Maps 4'

# --- Step 13: Create UNIQUE_KEY column ---
df_gmaps_processed_4['UNIQUE_KEY'] = df_gmaps_processed_4['Google Maps Link'].fillna('') + '_' + df_gmaps_processed_4['Name'].fillna('')

df_gmaps_processed_4['UNIQUE_KEY'] = df_gmaps_processed_4.apply(
    lambda row: row['UNIQUE_KEY'] if row['UNIQUE_KEY'].strip('_') != '' else f"gmaps4_idx_{row.name}",
    axis=1
)

# --- Step 14: Display the first 5 rows and info ---
print("First 5 rows of df_gmaps_processed_4 after all operations:")
display(df_gmaps_processed_4.head())

print("\nDataFrame Info after all operations:")
df_gmaps_processed_4.info()




====

import numpy as np
import os
import pandas as pd

pd.set_option('future.no_silent_downcasting', True)

# Define the filename for the complete deduplicated DataFrame
fajl_complete_df = 'Biznis Klima uređaji Hrvatska.csv'

# Check if df_deduplicated_final is already defined in the current environment
if 'df_deduplicated_final' not in locals():
    print(f"Warning: 'df_deduplicated_final' not found. Loading from '{fajl_complete_df}'.")
    if os.path.exists(fajl_complete_df) and os.path.getsize(fajl_complete_df) > 0:
        df_deduplicated_final = pd.read_csv(fajl_complete_df, dtype=str)
    else:
        print(f"Error: '{fajl_complete_df}' not found or is empty. Cannot proceed.")
        # Initialize an empty DataFrame with expected columns to prevent further errors
        df_deduplicated_final = pd.DataFrame(columns=['EXTRACTED_EMAIL', 'UNIQUE_KEY'])


# 1. Ensure the 'EXTRACTED_EMAIL' column in df_deduplicated_final is treated as strings
# and that empty strings, 'nan', or 'no email found' values are replaced with np.nan.
if 'EXTRACTED_EMAIL' in df_deduplicated_final.columns:
    df_deduplicated_final['EXTRACTED_EMAIL'] = df_deduplicated_final['EXTRACTED_EMAIL'].astype(str)
    df_deduplicated_final['EXTRACTED_EMAIL'] = df_deduplicated_final['EXTRACTED_EMAIL'].replace(['', 'nan', 'no email found'], np.nan)
else:
    print("Warning: 'EXTRACTED_EMAIL' column not found in df_deduplicated_final. Creating it with NaN values.")
    df_deduplicated_final['EXTRACTED_EMAIL'] = np.nan

# 2. Create a new DataFrame, df_leads_with_emails, containing all rows from df_deduplicated_final
# where the 'EXTRACTED_EMAIL' column is not null.
df_leads_with_emails = df_deduplicated_final[df_deduplicated_final['EXTRACTED_EMAIL'].notna()].copy()

# 3. Create another new DataFrame, df_leads_without_emails, containing all rows from df_deduplicated_final
# where the 'EXTRACTED_EMAIL' column is null.
df_leads_without_emails = df_deduplicated_final[df_deduplicated_final['EXTRACTED_EMAIL'].isna()].copy()

# Define output file paths
fajl_with_emails = 'FINALNA_LISTA_SA_EMAILOM.csv'
fajl_without_emails = 'LEADOVI_BEZ_EMAILA.csv'
# fajl_complete_df is already defined above

# 4. Save df_leads_with_emails to a CSV file named 'FINALNA_LISTA_SA_EMAILOM.csv' without the index.
if not df_leads_with_emails.empty:
    df_leads_with_emails.to_csv(fajl_with_emails, index=False)
    print(f"✅ {len(df_leads_with_emails)} leads with emails saved to '{fajl_with_emails}'.")
else:
    # If the DataFrame is empty, ensure the file is empty or doesn't exist
    if os.path.exists(fajl_with_emails): os.remove(fajl_with_emails)
    pd.DataFrame(columns=df_deduplicated_final.columns).to_csv(fajl_with_emails, index=False)
    print(f"⚠️ No leads with emails found. Empty file '{fajl_with_emails}' created.")

# 5. Save df_leads_without_emails to a CSV file named 'LEADOVI_BEZ_EMAILA.csv' without the index.
if not df_leads_without_emails.empty:
    df_leads_without_emails.to_csv(fajl_without_emails, index=False)
    print(f"✅ {len(df_leads_without_emails)} leads without emails saved to '{fajl_without_emails}'.")
else:
    # If the DataFrame is empty, ensure the file is empty or doesn't exist
    if os.path.exists(fajl_without_emails): os.remove(fajl_without_emails)
    pd.DataFrame(columns=df_deduplicated_final.columns).to_csv(fajl_without_emails, index=False)
    print(f"⚠️ No leads without emails found. Empty file '{fajl_without_emails}' created.")

# 6. Save the complete df_deduplicated_final to a CSV file named 'Biznis Klima uređaji Hrvatska.csv' without the index.
if not df_deduplicated_final.empty:
    df_deduplicated_final.to_csv(fajl_complete_df, index=False)
    print(f"✅ Complete deduplicated DataFrame ({len(df_deduplicated_final)} rows) saved to '{fajl_complete_df}'.")
else:
    # If the DataFrame is empty, ensure the file is empty or doesn't exist
    if os.path.exists(fajl_complete_df): os.remove(fajl_complete_df)
    pd.DataFrame(columns=df_deduplicated_final.columns).to_csv(fajl_complete_df, index=False)
    print(f"⚠️ The deduplicated DataFrame is empty. Empty file '{fajl_complete_df}' created.")

print("Final separation and saving of leads completed.")


=====

import pandas as pd
import numpy as np

# Define the filename
input_file = 'Biznis Klima uređaji Hrvatska.csv'

# Load the CSV file into a pandas DataFrame, ensuring all columns are read as strings
df_leads = pd.read_csv(input_file, dtype=str)

# Check if the 'EXTRACTED_EMAIL' column exists
if 'EXTRACTED_EMAIL' not in df_leads.columns:
    # If it does not exist, create it and initialize all its values to np.nan
    df_leads['EXTRACTED_EMAIL'] = np.nan
    print("Created 'EXTRACTED_EMAIL' column and initialized with NaN values.")
else:
    # If it exists, replace any empty strings, 'nan' (as string), or 'no email found' with np.nan
    # Ensure the column is treated as string before replacing
    df_leads['EXTRACTED_EMAIL'] = df_leads['EXTRACTED_EMAIL'].astype(str)
    df_leads['EXTRACTED_EMAIL'] = df_leads['EXTRACTED_EMAIL'].replace(['', 'nan', 'no email found'], np.nan)
    print("Standardized 'EXTRACTED_EMAIL' column by replacing empty/placeholder values with NaN.")

# Display the first 5 rows of the df_leads DataFrame
print("\nFirst 5 rows of df_leads after preparing for email extraction:")
df_leads.head()

# Print a summary of the DataFrame's structure
print("\nDataFrame Info after preparing for email extraction:")
df_leads.info()


=======

import requests
import re
from requests.exceptions import RequestException, HTTPError

# --- Function to fetch website content ---
def fetch_website_content(url):
    if not url or not isinstance(url, str):
        return None

    # Add http if missing for robust request handling
    if not url.startswith('http://') and not url.startswith('https://'):
        url = 'http://' + url

    try:
        # Set a timeout for the request to avoid hanging indefinitely
        response = requests.get(url, timeout=10)
        # Raise an exception for HTTP errors (4xx or 5xx)
        response.raise_for_status()
        return response.text
    except HTTPError as e:
        print(f"HTTP Error fetching {url}: {e}")
        return None
    except RequestException as e:
        print(f"Request Error fetching {url}: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred while fetching {url}: {e}")
        return None

# --- Function to extract emails from content ---
def extract_emails_from_content(html_content):
    if not html_content:
        return []

    # Regular expression for email addresses
    # This regex attempts to be robust for common email formats
    email_regex = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'

    # Find all unique email addresses
    found_emails = set(re.findall(email_regex, html_content, re.IGNORECASE))

    return list(found_emails)

print("Web scraping functions (fetch_website_content, extract_emails_from_content) defined.")


========

import time
import numpy as np

print("🚀 Starting automatic email extraction...")

# Counter for emails found
emails_found_count = 0

# Iterate through each row of the df_leads DataFrame
for index, row in df_leads.iterrows():
    # Check if EXTRACTED_EMAIL is NaN and if a Website URL is available
    if pd.isna(row['EXTRACTED_EMAIL']) and pd.notna(row['Website']):
        website_url = row['Website']

        print(f"\n🔍 Attempting to extract email from: {website_url} (Lead: {row['Name']})")

        # Fetch website content
        html_content = fetch_website_content(website_url)

        if html_content:
            # Extract emails from content
            found_emails = extract_emails_from_content(html_content)

            if found_emails:
                # Take the first email and update the EXTRACTED_EMAIL column
                df_leads.at[index, 'EXTRACTED_EMAIL'] = found_emails[0]
                emails_found_count += 1
                print(f"✅ Found email: {found_emails[0]}")
            else:
                # Mark as 'no email found' to avoid re-processing this URL later
                df_leads.at[index, 'EXTRACTED_EMAIL'] = 'no email found'
                print("😔 No email found on this page.")
        else:
            # Mark as 'no email found' if content could not be fetched
            df_leads.at[index, 'EXTRACTED_EMAIL'] = 'no email found'
            print("❌ Could not fetch website content.")

        # Implement a short time delay between requests
        time.sleep(1) # 1 second delay

print(f"\n🏁 Automatic email extraction finished. Found {emails_found_count} new emails.")

# Save the modified df_leads DataFrame
output_file = 'Biznis Klima uređaji Hrvatska.csv'
df_leads.to_csv(output_file, index=False)
print(f"✅ Updated DataFrame saved to '{output_file}'.")

# Display the first 5 rows of the updated df_leads DataFrame
print("\nFirst 5 rows of df_leads after automatic email extraction:")
df_leads.head()

# Print a summary of the DataFrame's structure
print("\nDataFrame Info after automatic email extraction:")
df_leads.info()


========

import pandas as pd
import numpy as np
import os
import ipywidgets as widgets
from IPython.display import display, clear_output

print("🚀 POKREĆEM PAMETNI MANUALNI UNOS ZA PREOSTALE LEADove (EMAILI)...")

# --- 1. POSTAVKE ---
fajl_za_obradu = 'Biznis Klima uređaji Hrvatska.csv'

# Provjera postojanja ulaznog fajla
if not os.path.exists(fajl_za_obradu):
    print(f"❌ Nema fajla '{fajl_za_obradu}'! Molimo provjerite da li je uploadovan ili je prethodni korak uspješno generisao fajl.")
else:
    try:
        df_leads = pd.read_csv(fajl_za_obradu, dtype=str)

        # Ensure 'EXTRACTED_EMAIL' column exists and is standardized
        if 'EXTRACTED_EMAIL' not in df_leads.columns:
            df_leads['EXTRACTED_EMAIL'] = np.nan
        else:
            df_leads['EXTRACTED_EMAIL'] = df_leads['EXTRACTED_EMAIL'].astype(str)
            df_leads['EXTRACTED_EMAIL'] = df_leads['EXTRACTED_EMAIL'].replace(['', 'nan', 'no email found'], np.nan)

        # --- 2. LOGIKA ZA FILTRIRANJE ---
        # Filtriraj: Samo oni bez emaila I s pravim web linkom
        maska_bez_emaila = df_leads['EXTRACTED_EMAIL'].isna()
        maska_sa_webom = df_leads['Website'].notna()

        indeksi_za_obradu = df_leads[maska_bez_emaila & maska_sa_webom].index.tolist()

        print(f"✅ Pronađeno {len(indeksi_za_obradu)} leadova za ručnu provjeru emaila.\n")

        trenutni_korak = 0
        rows_to_delete = set() # Set za čuvanje indeksa redova za brisanje

        # --- 3. WIDGETI ---
        polje_email = widgets.Text(placeholder='Zalijepi email ovdje...', description='📧 Email:', layout=widgets.Layout(width='60%'))

        gumb_spremi = widgets.Button(description="✅ SPREMI EMAIL", button_style='success', layout=widgets.Layout(width='200px'))
        gumb_preskoci = widgets.Button(description="⏩ NEMA EMAILA / DALJE", button_style='warning', layout=widgets.Layout(width='200px'))
        gumb_izbrisi = widgets.Button(description="🗑️ IZBRIŠI RED", button_style='danger', layout=widgets.Layout(width='180px'))
        gumb_kraj = widgets.Button(description="💾 KRAJ I IZLAZ", button_style='danger', layout=widgets.Layout(width='150px'))

        output_area = widgets.Output()

        def find_display_link(row):
            # Prioritet: Website
            website_link = row.get('Website')

            if pd.notna(website_link):
                link = str(website_link)
                if not link.startswith('http'):
                    link = 'http://' + link
                return link, "🌐 OTVORI WEB STRANICU", "background-color: #ff9900;"
            return None, None, None

        def display_next_entry():
            global trenutni_korak, indeksi_za_obradu, rows_to_delete
            output_area.clear_output()

            # Preskoči redove koji su već označeni za brisanje
            while trenutni_korak < len(indeksi_za_obradu) and \
                  (indeksi_za_obradu[trenutni_korak] in rows_to_delete):
                trenutni_korak += 1

            if trenutni_korak >= len(indeksi_za_obradu):
                with output_area:
                    print("🎉 GOTOVO! Nema više leadova za ručnu provjeru.")
                return

            idx = indeksi_za_obradu[trenutni_korak]
            row = df_leads.loc[idx] # Koristimo df_leads.loc sa originalnim indeksom

            link, button_text, button_style = find_display_link(row)
            ime = row.get('Name', 'N/A') # Koristi 'Name' kolonu

            with output_area:
                print("-" * 60)
                print(f"🏢 FIRMA: {ime}")
                print(f"📊 NAPREDAK: {trenutni_korak + 1} / {len(indeksi_za_obradu)}")
                print(f"Originalni indeks: {idx}")

                if link:
                    # HTML Link
                    html = f'''
                    <div style="margin: 20px 0;">
                        <a href="{link}" target="_blank" style="{button_style} color: white; padding: 15px 30px; text-decoration: none; font-size: 18px; border-radius: 5px; display: inline-block; font-family: sans-serif; font-weight: bold;">
                            {button_text}
                        </a>
                        <br><small style="color: grey; margin-top: 5px; display: block;">Link: {link}</small>
                    </div>
                    '''
                    display(widgets.HTML(html))
                else:
                    print("⚠️ Greška: Ne mogu izdvojiti link za prikaz.")

            polje_email.value = ""

        def on_save_click(b):
            global trenutni_korak, indeksi_za_obradu
            if not indeksi_za_obradu or trenutni_korak >= len(indeksi_za_obradu): return

            idx = indeksi_za_obradu[trenutni_korak]
            email = polje_email.value.strip()

            if email and '@' in email:
                df_leads.at[idx, 'EXTRACTED_EMAIL'] = email
                with output_area:
                    print("✅ Email uspješno zapisan.")
                trenutni_korak += 1
                display_next_entry()
            else:
                with output_area:
                    print("⚠️ Moraš upisati ispravan email (mora imati @)!")

        def on_skip_click(b):
            global trenutni_korak, indeksi_za_obradu
            if not indeksi_za_obradu or trenutni_korak >= len(indeksi_za_obradu): return

            idx = indeksi_za_obradu[trenutni_korak]
            df_leads.at[idx, 'EXTRACTED_EMAIL'] = 'no email found'
            with output_area:
                print("⏩ Red preskočen i označen kao 'no email found'.")
            trenutni_korak += 1
            display_next_entry()

        def on_delete_click(b):
            global trenutni_korak, indeksi_za_obradu, rows_to_delete

            if not indeksi_za_obradu or trenutni_korak >= len(indeksi_za_obradu):
                with output_area:
                    print("🎉 Nema više redova za obradu/brisanje.")
                return

            idx_to_mark = indeksi_za_obradu[trenutni_korak]
            rows_to_delete.add(idx_to_mark)

            with output_area:
                print(f"🗑️ Red sa originalnim indeksom {idx_to_mark} označen za brisanje.")

            trenutni_korak += 1
            display_next_entry()

        def on_finish_click(b):
            global df_leads
            output_area.clear_output()

            if rows_to_delete:
                df_leads = df_leads.drop(index=list(rows_to_delete))
                with output_area:
                    print(f"🗑️ Izbrisano {len(rows_to_delete)} redova iz glavnog fajla prije spremanja.")

            df_leads.to_csv(fajl_za_obradu, index=False)
            with output_area:
                print(f"💾 SPREMLJENO! Ažurirani fajl '{fajl_za_obradu}' je sačuvan.\n")
                print("👉 Sada možeš skinuti fajl ili nastaviti sa sljedećim koracima.")

            # Reset the state so the notebook can be rerun
            rows_to_delete.clear()
            # Do not clear indeksi_za_obradu or trenutni_korak as that might affect subsequent runs if the user expects it to restart
            # Instead, rely on the display_next_entry to check if there are any remaining items


        # Povezivanje dugmadi
        gumb_spremi.on_click(on_save_click)
        gumb_preskoci.on_click(on_skip_click)
        gumb_izbrisi.on_click(on_delete_click)
        gumb_kraj.on_click(on_finish_click)

        # Prikaz
        display(output_area)
        display(widgets.HBox([polje_email]))
        display(widgets.HBox([gumb_spremi, gumb_preskoci, gumb_izbrisi, gumb_kraj]))

        display_next_entry()

    except pd.errors.EmptyDataError:
        print(f"❌ Fajl '{fajl_za_obradu}' je prazan ili nema zaglavlja.")
    except Exception as e:
        print(f"Došlo je do greške prilikom učitavanja ili obrade fajla: {e}")


        =======



        import pandas as pd
import numpy as np
import os

print("🚀 Pokrećem odvajanje leadova sa i bez email adresa...")

# Define the input file from the previous steps
input_file = 'Biznis Klima uređaji Hrvatska.csv'
fajl_with_emails = 'FINALNA_LISTA_SA_EMAILOM.csv'
fajl_without_emails = 'LEADOVI_BEZ_EMAILA.csv'

# Load the DataFrame to ensure we have the latest state
if not os.path.exists(input_file):
    print(f"❌ Fajl '{input_file}' ne postoji! Nema podataka za obradu.")
else:
    try:
        df_leads_final = pd.read_csv(input_file, dtype=str)

        # Ensure 'EXTRACTED_EMAIL' column is properly cleaned and standardized
        if 'EXTRACTED_EMAIL' not in df_leads_final.columns:
            df_leads_final['EXTRACTED_EMAIL'] = np.nan
        else:
            df_leads_final['EXTRACTED_EMAIL'] = df_leads_final['EXTRACTED_EMAIL'].astype(str)
            df_leads_final['EXTRACTED_EMAIL'] = df_leads_final['EXTRACTED_EMAIL'].replace(['', 'nan', 'no email found'], np.nan)

        # Create a new DataFrame for leads with emails
        df_leads_with_emails = df_leads_final[df_leads_final['EXTRACTED_EMAIL'].notna()].copy()

        # Create a new DataFrame for leads without emails
        df_leads_without_emails = df_leads_final[df_leads_final['EXTRACTED_EMAIL'].isna()].copy()

        # Save leads with emails
        if not df_leads_with_emails.empty:
            df_leads_with_emails.to_csv(fajl_with_emails, index=False)
            print(f"✅ {len(df_leads_with_emails)} leadova SA email adresom sačuvano u '{fajl_with_emails}'.")
        else:
            if os.path.exists(fajl_with_emails): os.remove(fajl_with_emails)
            pd.DataFrame(columns=df_leads_final.columns).to_csv(fajl_with_emails, index=False) # Ensure file exists, even if empty
            print(f"⚠️ Nema pronađenih leadova SA email adresom. Kreiran prazan fajl '{fajl_with_emails}'.")

        # Save leads without emails
        if not df_leads_without_emails.empty:
            df_leads_without_emails.to_csv(fajl_without_emails, index=False)
            print(f"✅ {len(df_leads_without_emails)} leadova BEZ email adrese sačuvano u '{fajl_without_emails}'.")
        else:
            if os.path.exists(fajl_without_emails): os.remove(fajl_without_emails)
            pd.DataFrame(columns=df_leads_final.columns).to_csv(fajl_without_emails, index=False) # Ensure file exists, even if empty
            print(f"⚠️ Svi leadovi imaju email adrese. Kreiran prazan fajl '{fajl_without_emails}'.")

        print("🏁 Odvajanje leadova završeno.")
        print(f"Ukupno leadova u '{input_file}': {len(df_leads_final)}")
        print(f"Leadova sa emailom: {len(df_leads_with_emails)}")
        print(f"Leadova bez emaila: {len(df_leads_without_emails)}")

    except Exception as e:
        print(f"Došlo je do greške prilikom učitavanja ili obrade fajla: {e}")


========


import pandas as pd
import os
import requests
from bs4 import BeautifulSoup
import time
import random
import re
from urllib.parse import unquote, urlparse, quote_plus
from requests.exceptions import RequestException, HTTPError

print("🚀 Defining Web Scraping and Social Search Functions...")

# --- Crawlbase API Tokens ---
CRAWLBASE_NORMAL_TOKEN = '0OaRK4xfwfebVbyiHiYyCg'
CRAWLBASE_JS_TOKEN = '1fBU2JH_jY70dPU86EKnDw'
CRAWLBASE_API_URL_NORMAL = "https://api.crawlbase.com/"
CRAWLBASE_API_URL_JS = "https://api.crawlbase.com/js"

# --- User agents ---
user_agents = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
]

# --- Helper function: Extract name from domain ---
def izvuci_ime_iz_domene(url):
    if not isinstance(url, str) or len(url) < 5: return ""
    try:
        if not url.startswith('http'): url = 'http://' + url
        parsed = urlparse(url)
        domain = parsed.netloc
        if domain.startswith('www.'):
            domain = domain[4:]
        ime = domain.split('.')[0]
        ime = ime.replace('-', ' ').replace('_', ' ')
        return ime.title()
    except:
        return ""

# --- Function: Search social media links ---
def trazi_social_linkove(pojam, scraped_phone=None):
    if not pojam and not scraped_phone: return None, None

    query_parts = []
    if pojam and len(pojam) > 2:
        query_parts.append(pojam)
    if scraped_phone and len(scraped_phone) > 5:
        query_parts.append(scraped_phone)
    query_parts.append('official facebook instagram page')
    query = ' '.join(query_parts)

    if not query_parts: return None, None

    duckduckgo_target_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"

    crawlbase_params = {
        'token': CRAWLBASE_NORMAL_TOKEN,
        'url': duckduckgo_target_url,
        'user_agent': random.choice(user_agents)
    }

    try:
        response = requests.get(CRAWLBASE_API_URL_NORMAL, params=crawlbase_params, timeout=30)
        response.raise_for_status()

        fb_link = None
        insta_link = None

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for link in soup.find_all('a', class_='result__a'):
                href = link.get('href')
                if href:
                    href = unquote(href)
                    if 'facebook.com' in href and not fb_link:
                        if 'search' not in href and 'directory' not in href and 'public' not in href:
                            fb_link = href
                    if 'instagram.com' in href and not insta_link:
                        if 'explore' not in href and 'accounts/login' not in href:
                            insta_link = href
                if fb_link and insta_link: break
        return fb_link, insta_link
    except HTTPError as e:
        if e.response.status_code == 429:
            print(f"    Crawlbase Rate Limit (429) hit for social search query '{query}'. Retrying after 60 seconds.")
            time.sleep(random.uniform(60, 120))
            response = requests.get(CRAWLBASE_API_URL_NORMAL, params=crawlbase_params, timeout=30)
            response.raise_for_status()
        elif e.response.status_code >= 400:
            print(f"    HTTP error for social search '{pojam}' (Phone: {scraped_phone or 'N/A'}): Status {e.response.status_code}, Response: {e.response.text.strip() if e.response.text else 'No response body'}")
        return None, None
    except RequestException as e:
        print(f"    Network/Request error for social search query '{query}': {e}")
        return None, None
    except Exception as e:
        print(f"    Unexpected error during social search query '{query}': {e}")
        return None, None

# --- Function: Scrape website details ---
def scrape_website_details(url):
    business_name = None
    phone_number = None

    if not url or not isinstance(url, str) or not url.startswith('http'):
        return None, None

    crawlbase_params = {
        'token': CRAWLBASE_JS_TOKEN,
        'url': url,
        'user_agent': random.choice(user_agents),
        'js_render': 'true'
    }

    try:
        response = requests.get(CRAWLBASE_API_URL_JS, params=crawlbase_params, timeout=60)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        og_site_name = soup.find('meta', property='og:site_name')
        if og_site_name and og_site_name.get('content'):
            business_name = og_site_name.get('content').strip()

        if not business_name:
            og_title = soup.find('meta', property='og:title')
            if og_title and og_title.get('content'):
                business_name = og_title.get('content').strip()

        if not business_name and soup.title:
            business_name = soup.title.string.strip()

        if not business_name:
            h1_tags = soup.find_all('h1')
            if h1_tags:
                business_name = max(h1_tags, key=lambda x: len(x.get_text().strip()), default=None)
                if business_name:
                    business_name = business_name.get_text().strip()

        if business_name:
            business_name = re.sub(r'\s*[|/\-]+?\s*(Booking\.com|Airbnb|Accommodation|Hotels|Apartments|Guest House|Villa)\b.*', '', business_name, flags=re.IGNORECASE)
            business_name = re.sub(r'\s*[|/\-]+?\s*$', '', business_name)
            business_name = business_name.strip()
            if len(business_name) < 3:
                business_name = None

        phone_number = None
        phone_patterns = [
            r'\+?\d{1,4}[\s.\-]?\(?\d{1,4}\)?[\s.\-]?\d{1,4}[\s.\-]?\d{1,4}[\s.\-]?\d{1,9}',
            r'\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}',
            r'\b\d{9,15}\b'
        ]

        text_content = soup.get_text(separator=' ', strip=True)
        for pattern in phone_patterns:
            matches = re.findall(pattern, text_content)
            for match_str in matches:
                cleaned_phone = re.sub(r'[^\d+]', '', match_str)
                if len(cleaned_phone) >= 7:
                    phone_number = cleaned_phone
                    break
            if phone_number:
                break

    except HTTPError as e:
        if e.response.status_code == 429:
            print(f"    Crawlbase Rate Limit (429) hit for website '{url}'. Status: {e.response.status_code}, Response: {e.response.text}")
            time.sleep(random.uniform(60, 120))
            response = requests.get(CRAWLBASE_API_URL_JS, params=crawlbase_params, timeout=60)
            response.raise_for_status()
        elif e.response.status_code >= 400:
            print(f"    HTTP error fetching '{url}': Status {e.response.status_code}, Reason: {e.response.reason}")
        return None, None
    except RequestException as e:
        print(f"    Network/Request error fetching '{url}': {e}")
        pass
    except Exception as e:
        print(f"    An unexpected error occurred for '{url}': {e}")
        pass

    return business_name, phone_number

print("✅ Web scraping and social search functions defined.")


=========

print("🚀 Starting social media link extraction for leads without email...")

# Counter for newly found social media links
new_social_links_found = 0

# Iterate through each row of the df_leads_without_email DataFrame
for index, row in df_leads_without_email.iterrows():
    website_url = row.get('Website')
    lead_name = row.get('Name', 'N/A')
    current_facebook = row.get('Facebook')
    current_instagram = row.get('Instagram')

    # Check if a Website link is available and if social media links are missing
    if pd.notna(website_url) and (pd.isna(current_facebook) or pd.isna(current_instagram)):

        print(f"\n🔍 Processing lead: {lead_name} (Website: {website_url})")

        # 4b. Call the scrape_website_details function
        scraped_name, scraped_phone = scrape_website_details(website_url)

        # 4c. Determine the most appropriate search term (pojam)
        pojam = ""
        if scraped_name and len(scraped_name) > 3:
            pojam = scraped_name
        elif len(lead_name) > 3 and "http" not in lead_name:
            pojam = lead_name
        else:
            pojam = izvuci_ime_iz_domene(website_url)

        if (not pojam or len(pojam) < 3) and (not scraped_phone or len(scraped_phone) < 7):
            print(f"   ⚠️ Skipping {lead_name}: Insufficient search terms for social media.")
            # Mark as 'no social found' to avoid re-processing this lead for social media
            if pd.isna(df_leads_without_email.at[index, 'Facebook']):
                df_leads_without_email.at[index, 'Facebook'] = 'no social found'
            if pd.isna(df_leads_without_email.at[index, 'Instagram']):
                df_leads_without_email.at[index, 'Instagram'] = 'no social found'
            time.sleep(random.uniform(1, 3)) # Small delay even if skipped
            continue

        print(f"   Searching social media for: '{pojam}' (Phone: {scraped_phone or 'N/A'})")

        # 4d. Call the trazi_social_linkove function
        found_fb, found_insta = trazi_social_linkove(pojam, scraped_phone)

        # 4e. Update Facebook link
        if found_fb and pd.isna(current_facebook):
            df_leads_without_email.at[index, 'Facebook'] = found_fb
            new_social_links_found += 1
            print(f"   ✅ Found Facebook: {found_fb}")
        elif pd.isna(current_facebook): # If still NaN and not found, mark as 'no social found'
            df_leads_without_email.at[index, 'Facebook'] = 'no social found'
            print("   😔 No new Facebook link found.")

        # 4f. Update Instagram link
        if found_insta and pd.isna(current_instagram):
            df_leads_without_email.at[index, 'Instagram'] = found_insta
            new_social_links_found += 1
            print(f"   ✅ Found Instagram: {found_insta}")
        elif pd.isna(current_instagram): # If still NaN and not found, mark as 'no social found'
            df_leads_without_email.at[index, 'Instagram'] = 'no social found'
            print("   😔 No new Instagram link found.")

        # 4g. Implement a time delay
        time.sleep(random.uniform(1, 3)) # Random delay between 1 and 3 seconds
    else:
        # Skip if website is missing or social links are already present
        if pd.isna(website_url):
            print(f"Skipping {lead_name}: No website URL available.")
        else:
            print(f"Skipping {lead_name}: Social media links already present.")

print(f"\n🏁 Social media link extraction finished. Found {new_social_links_found} new social media links.")

# Save the modified df_leads_without_email DataFrame
output_file_without_email = 'LEADOVI_BEZ_EMAILA.csv'
df_leads_without_email.to_csv(output_file_without_email, index=False)
print(f"✅ Updated DataFrame saved to '{output_file_without_email}'.")

# Display the first 5 rows of the updated df_leads_without_email DataFrame
print("\nFirst 5 rows of df_leads_without_email after social media extraction:")
df_leads_without_email.head()

# Print a summary of the DataFrame's structure
print("\nDataFrame Info after social media extraction:")
df_leads_without_email.info()


=========


import pandas as pd
import numpy as np
import os

# Define the filename
leads_without_email_file = 'LEADOVI_BEZ_EMAILA.csv'

# Load the CSV file into a pandas DataFrame, ensuring all columns are read as strings
df = pd.read_csv(leads_without_email_file, dtype=str)

# Check if the 'EXTRACTED_EMAIL' column exists
if 'EXTRACTED_EMAIL' not in df.columns:
    # If it does not exist, create it and initialize all its values to np.nan
    df['EXTRACTED_EMAIL'] = np.nan
    print("Created 'EXTRACTED_EMAIL' column and initialized with NaN values.")
else:
    # If it exists, replace any empty strings, 'nan' (as string), or 'no email found' with np.nan
    # Ensure the column is treated as string before replacing
    df['EXTRACTED_EMAIL'] = df['EXTRACTED_EMAIL'].astype(str)
    df['EXTRACTED_EMAIL'] = df['EXTRACTED_EMAIL'].replace(['', 'nan', 'no email found'], np.nan)
    print("Standardized 'EXTRACTED_EMAIL' column by replacing empty/placeholder values with NaN.")

# Display the first 5 rows of the df DataFrame
print("\nFirst 5 rows of LEADOVI_BEZ_EMAILA.csv after standardization:")
df.head()

# Display a summary of the df DataFrame
print("\nDataFrame Info after standardization:")
df.info()


=======

import pandas as pd
import numpy as np

# 1. Define a function ima_pravi_link(row) that checks if a row has a valid Facebook, Instagram, or Website link.
def ima_pravi_link(row):
    # Check for non-null and not 'no social found' or 'nan' values in 'Facebook' or 'Instagram' columns
    if pd.notna(row.get('Facebook')) and str(row.get('Facebook')).lower() not in ('no social found', 'nan'):
        return True
    if pd.notna(row.get('Instagram')) and str(row.get('Instagram')).lower() not in ('no social found', 'nan'):
        return True
    # Also include Website as a fallback if social links are not available/found
    if pd.notna(row.get('Website')) and str(row.get('Website')).lower() not in ('no social found', 'nan'):
        return True
    return False

# 2. Create a boolean mask `maska_bez_emaila` for rows where the 'EXTRACTED_EMAIL' column is NaN.
maska_bez_emaila = df['EXTRACTED_EMAIL'].isna()

# 3. Create another boolean mask `maska_pravi_link` by applying the `ima_pravi_link` function to each row of the DataFrame.
maska_pravi_link = df.apply(ima_pravi_link, axis=1)

# 4. Combine these two masks to get a list of indices `indeksi_za_obradu` for rows that lack an email but have a valid social media or website link.
indeksi_za_obradu = df[maska_bez_emaila & maska_pravi_link].index.tolist()

# 5. Print the number of leads found for manual review.
print(f"✅ Pronađeno {len(indeksi_za_obradu)} leadova za ručnu provjeru emaila sa društvenim mrežama/web lokacijama.")



=========


import pandas as pd
import numpy as np
import os
import ipywidgets as widgets
from IPython.display import display, clear_output

print("🚀 POKREĆEM PAMETNI MANUALNI UNOS ZA LEADOVE SA DRUŠTVENIM MREŽAMA...")

# --- 1. POSTAVKE ---
fajl = 'LEADOVI_BEZ_EMAILA.csv' # Changed to load LEADOVI_BEZ_EMAILA.csv

if not os.path.exists(fajl):
    print(f"❌ Nema fajla '{fajl}'! Molimo provjerite da li je uploadovan ili da li je prethodni korak uspješno generisao fajl.")
else:
    try:
        df = pd.read_csv(fajl, dtype=str)

        # Ensure 'EXTRACTED_EMAIL' column exists and is standardized (replacing empty strings, 'nan', 'no email found' with actual NaN)
        if 'EXTRACTED_EMAIL' not in df.columns:
            df['EXTRACTED_EMAIL'] = np.nan
        else:
            df['EXTRACTED_EMAIL'] = df['EXTRACTED_EMAIL'].astype(str)
            df['EXTRACTED_EMAIL'] = df['EXTRACTED_EMAIL'].replace(['', 'nan', 'no email found'], np.nan)

        # --- 2. LOGIKA ZA FILTRIRANJE ---
        # Tražimo redove koji nemaju email, ali IMAJU Facebook, Instagram ili Website link
        def ima_pravi_link(row):
            # Check for non-null and not 'no social found' or 'nan' values in 'Facebook' or 'Instagram' columns
            if pd.notna(row.get('Facebook')) and str(row.get('Facebook')).lower() not in ('no social found', 'nan'):
                return True
            if pd.notna(row.get('Instagram')) and str(row.get('Instagram')).lower() not in ('no social found', 'nan'):
                return True
            # Also include Website as a fallback if social links are not available/found
            if pd.notna(row.get('Website')) and str(row.get('Website')).lower() not in ('no social found', 'nan'):
                return True
            return False

        # Filtriraj: Samo oni bez emaila I s pravim linkom
        maska_bez_emaila = df['EXTRACTED_EMAIL'].isna()
        maska_pravi_link = df.apply(ima_pravi_link, axis=1)

        indeksi_za_obradu = df[maska_bez_emaila & maska_pravi_link].index.tolist()

        print(f"✅ Pronađeno {len(indeksi_za_obradu)} redova s ispravnim linkovima za obradu.\n")

        trenutni_korak = 0
        rows_to_delete = set() # Set za čuvanje indeksa redova za brisanje

        # Track emails added in this session for summary
        initial_leads_without_email_count = len(indeksi_za_obradu)
        session_emails_added = 0

        # --- 3. WIDGETI ---
        polje_email = widgets.Text(placeholder='Zalijepi email ovdje...', description='📧 Email:', layout=widgets.Layout(width='50%'))

        gumb_spremi = widgets.Button(description="✅ SPREMI", button_style='success')
        gumb_preskoci = widgets.Button(description="⏩ NEMA EMAILA / DALJE", button_style='warning')
        gumb_izbrisi = widgets.Button(description="🗑️ OBRIŠI LEAD", button_style='danger')
        gumb_kraj = widgets.Button(description="💾 KRAJ I SPREMI", button_style='danger')
        output_area = widgets.Output()

        def nadji_pravi_link(row):
            # Prioritet: Facebook > Instagram > Website
            fb_link = row.get('Facebook')
            insta_link = row.get('Instagram')
            website_link = row.get('Website')

            if pd.notna(fb_link) and 'facebook.com' in str(fb_link) and str(fb_link) not in ('no social found', 'nan'):
                return str(fb_link), "OTVORI FACEBOOK PROFIL", "background-color: #3b5998;"
            if pd.notna(insta_link) and 'instagram.com' in str(insta_link) and str(insta_link) not in ('no social found', 'nan'):
                return str(insta_link), "OTVORI INSTAGRAM PROFIL", "background-color: #C13584;"
            if pd.notna(website_link) and ('http' in str(website_link) or 'www.' in str(website_link)):
                link = str(website_link)
                if not link.startswith('http'):
                    link = 'http://' + link
                return link, "🌐 OTVORI WEB STRANICU", "background-color: #ff9900;"
            return None, None, None

        def prikazi_sljedeci():
            global trenutni_korak, indeksi_za_obradu, rows_to_delete
            output_area.clear_output()

            # Preskači redove koji su označeni za brisanje
            while trenutni_korak < len(indeksi_za_obradu) and \
                  (indeksi_za_obradu[trenutni_korak] in rows_to_delete):
                trenutni_korak += 1

            if trenutni_korak >= len(indeksi_za_obradu):
                with output_area:
                    print("🎉 GOTOVO! Nema više linkova.")
                return

            idx = indeksi_za_obradu[trenutni_korak]
            row = df.loc[idx]

            link, button_text, button_style = nadji_pravi_link(row)
            ime = row.get('Name', 'N/A')

            with output_area:
                print("-" * 60)
                print(f"🏢 FIRMA: {ime}")
                print(f"📊 NAPREDAK: {trenutni_korak + 1} / {len(indeksi_za_obradu)}")
                print(f"Originalni indeks: {idx}")

                if link:
                    html = f'''
                    <div style="margin: 20px 0;">
                        <a href="{link}" target="_blank" style="{button_style} color: white; padding: 15px 30px; text-decoration: none; font-size: 18px; border-radius: 5px; display: inline-block; font-family: sans-serif;">
                            {button_text}
                        </a>
                        <br><small style="color: grey;">Link: {link}</small>
                    </div>
                    '''
                    display(widgets.HTML(html))
                else:
                    print("⚠️ Greška: Ne mogu naći ispravan link u ovom redu.")

            polje_email.value = ""

        def na_klik_spremi(b):
            global trenutni_korak, indeksi_za_obradu, session_emails_added
            if not indeksi_za_obradu or trenutni_korak >= len(indeksi_za_obradu): return

            idx = indeksi_za_obradu[trenutni_korak]
            email = polje_email.value.strip()

            if email and '@' in email:
                df.at[idx, 'EXTRACTED_EMAIL'] = email
                session_emails_added += 1 # Increment counter for summary
                # Autosave (only email changes are applied immediately to df, deletions are handled at the end)
                df.to_csv(fajl, index=False)
                with output_area:
                    print("✅ Email uspješno zapisan.")
                trenutni_korak += 1
                prikazi_sljedeci()
            else:
                with output_area:
                    print("⚠️ Moraš upisati ispravan email (mora imati @)!")

        def na_klik_preskoci(b):
            global trenutni_korak, indeksi_za_obradu
            if not indeksi_za_obradu or trenutni_korak >= len(indeksi_za_obradu): return

            idx = indeksi_za_obradu[trenutni_korak]
            df.at[idx, 'EXTRACTED_EMAIL'] = 'no email found'
            df.to_csv(fajl, index=False) # Sačuvaj promenu u fajl

            with output_area:
                print("⏩ Red preskočen i označen kao 'no email found'.")

            trenutni_korak += 1
            prikazi_sljedeci()

        def na_klik_izbrisi(b):
            global trenutni_korak, indeksi_za_obradu, rows_to_delete

            if not indeksi_za_obradu or trenutni_korak >= len(indeksi_za_obradu):
                with output_area:
                    print("🎉 Nema više redova za obradu/brisanje.")
                return

            idx_to_mark = indeksi_za_obradu[trenutni_korak]
            rows_to_delete.add(idx_to_mark)

            with output_area:
                print(f"🗑️ Red sa originalnim indeksom {idx_to_mark} označen za brisanje.")

            trenutni_korak += 1
            prikazi_sljedeci()

        def na_klik_kraj(b):
            global df, session_emails_added
            output_area.clear_output()

            if rows_to_delete:
                df_leads_final_processed = df.drop(index=list(rows_to_delete))
                with output_area:
                    print(f"🗑️ Izbrisano {len(rows_to_delete)} redova iz glavnog fajla prije spremanja.")
            else:
                df_leads_final_processed = df.copy()

            df_leads_final_processed.to_csv(fajl, index=False)
            with output_area:
                print(f"💾 SPREMLJENO! Ažurirani fajl '{fajl}' je sačuvan.\n")
                print("👉 Sada možeš skinuti fajlove ili nastaviti sa sljedećim koracima.")

                remaining_leads_without_email = df_leads_final_processed['EXTRACTED_EMAIL'].isna().sum()

                print(f"\n--- Rezultati ručne obrade ---")
                print(f"Ukupno leadova za ručnu obradu na početku: {initial_leads_without_email_count}")
                print(f"Broj emaila dodanih ručno: {session_emails_added}")
                print(f"Broj leadova označenih za brisanje: {len(rows_to_delete)}")
                print(f"Preostalo leadova bez emaila (u '{fajl}' nakon obrade): {remaining_leads_without_email}")
                print(f"Fajl '{fajl}' je ažuriran i sadrži {len(df_leads_final_processed)} redova.")
            rows_to_delete.clear()


        # Povezivanje dugmadi
        gumb_spremi.on_click(na_klik_spremi)
        gumb_preskoci.on_click(na_klik_preskoci)
        gumb_izbrisi.on_click(na_klik_izbrisi)
        gumb_kraj.on_click(na_klik_kraj)

        # Prikaz
        display(output_area)
        display(widgets.HBox([polje_email]))
        display(widgets.HBox([gumb_spremi, gumb_preskoci, gumb_izbrisi, gumb_kraj]))

        prikazi_sljedeci()

    except pd.errors.EmptyDataError:
        print(f"❌ Fajl '{fajl}' je prazan ili nema zaglavlja.")
    except Exception as e:
        print(f"Došlo je do greške prilikom učitavanja ili obrade fajla: {e}")


        =
 =========

 
        import pandas as pd
import numpy as np
import os

# Define the filename
leads_without_email_file = 'LEADOVI_BEZ_EMAILA.csv'

# Load the CSV file into a pandas DataFrame, ensuring all columns are read as strings
df_leads_without_email = pd.read_csv(leads_without_email_file, dtype=str)

# Check if the 'EXTRACTED_EMAIL' column exists
if 'EXTRACTED_EMAIL' not in df_leads_without_email.columns:
    # If it does not exist, create it and initialize all its values to np.nan
    df_leads_without_email['EXTRACTED_EMAIL'] = np.nan
    print("Created 'EXTRACTED_EMAIL' column and initialized with NaN values.")
else:
    # If it exists, replace any empty strings, 'nan' (as string), or 'no email found' with np.nan
    # Ensure the column is treated as string before replacing
    df_leads_without_email['EXTRACTED_EMAIL'] = df_leads_without_email['EXTRACTED_EMAIL'].astype(str)
    df_leads_without_email['EXTRACTED_EMAIL'] = df_leads_without_email['EXTRACTED_EMAIL'].replace(['', 'nan', 'no email found'], np.nan)
    print("Standardized 'EXTRACTED_EMAIL' column by replacing empty/placeholder values with NaN.")

# Display the first 5 rows of the df_leads_without_email DataFrame
print("\nFirst 5 rows of LEADOVI_BEZ_EMAILA.csv:")
df_leads_without_email.head()

# Display a summary of the df_leads_without_email DataFrame
print("\nDataFrame Info:")
df_leads_without_email.info()


======

import pandas as pd
import numpy as np
import os

# Define the filename for the complete deduplicated DataFrame
fajl_complete_df = 'Biznis Klima uređaji Hrvatska.csv'

# Load the complete deduplicated DataFrame
if os.path.exists(fajl_complete_df) and os.path.getsize(fajl_complete_df) > 0:
    df_deduplicated_final = pd.read_csv(fajl_complete_df, dtype=str)
    print(f"✅ Successfully loaded {len(df_deduplicated_final)} leads from '{fajl_complete_df}' into df_deduplicated_final.")
else:
    print(f"❌ Error: '{fajl_complete_df}' not found or is empty. Cannot proceed with final separation.")
    # Initialize an empty DataFrame with expected columns to prevent further errors
    # Assuming the structure from previous steps, including 'EXTRACTED_EMAIL' and 'UNIQUE_KEY'
    df_deduplicated_final = pd.DataFrame(columns=['Google Maps Link', 'Name', 'Rating', 'Reviews', 'Category', 'Address', 'Website', 'Phone', 'EXTRACTED_EMAIL', 'SOURCE_FILE', 'UNIQUE_KEY'])

# Ensure 'EXTRACTED_EMAIL' is standardized as NaN for proper splitting
if 'EXTRACTED_EMAIL' in df_deduplicated_final.columns:
    df_deduplicated_final['EXTRACTED_EMAIL'] = df_deduplicated_final['EXTRACTED_EMAIL'].astype(str)
    df_deduplicated_final['EXTRACTED_EMAIL'] = df_deduplicated_final['EXTRACTED_EMAIL'].replace(['', 'nan', 'no email found'], np.nan)
else:
    df_deduplicated_final['EXTRACTED_EMAIL'] = np.nan
    print("Warning: 'EXTRACTED_EMAIL' column was missing in df_deduplicated_final, initialized to NaN.")

print("\nFirst 5 rows of df_deduplicated_final before final separation:")
df_deduplicated_final.head()

print("\nDataFrame Info of df_deduplicated_final before final separation:")
df_deduplicated_final.info()


======

import time
import random
import pandas as pd
import numpy as np
import os
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import unquote, urlparse, quote_plus, parse_qs
from requests.exceptions import RequestException, HTTPError

print("🚀 Redefining Web Scraping and Social Search Functions...")

# --- Crawlbase API Tokens ---
CRAWLBASE_NORMAL_TOKEN = '0OaRK4xfwfebVbyiHiYyCg'
CRAWLBASE_JS_TOKEN = '1fBU2JH_jY70dPU86EKnDw'
CRAWLBASE_API_URL_NORMAL = "https://api.crawlbase.com/"
CRAWLBASE_API_URL_JS = "https://api.crawlbase.com/js"

# --- User agents ---
user_agents = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
]

# --- Helper function: Extract name from domain ---
def izvuci_ime_iz_domene(url):
    if not isinstance(url, str) or not url.strip(): return ""
    try:
        if not url.startswith('http'): url = 'http://' + url
        parsed = urlparse(url)
        domain = parsed.netloc
        if domain.startswith('www.'):
            domain = domain[4:]
        ime = domain.split('.')[0]
        ime = ime.replace('-', ' ').replace('_', ' ')
        return ime.title()
    except:
        return ""

# --- Function: Search social media links ---
def trazi_social_linkove(pojam, scraped_phone=None):
    if not pojam and not scraped_phone: return None, None

    query_parts = []
    if pojam and len(pojam) > 2:
        query_parts.append(pojam)
    if scraped_phone and len(scraped_phone) > 5:
        query_parts.append(scraped_phone)
    query_parts.append('official facebook instagram page')
    query = ' '.join(query_parts)

    if not query_parts: return None, None

    duckduckgo_target_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"

    crawlbase_params = {
        'token': CRAWLBASE_NORMAL_TOKEN,
        'url': duckduckgo_target_url,
        'user_agent': random.choice(user_agents)
    }

    try:
        response = requests.get(CRAWLBASE_API_URL_NORMAL, params=crawlbase_params, timeout=30)
        response.raise_for_status()

        fb_link = None
        insta_link = None

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for link in soup.find_all('a', class_='result__a'):
                original_href = link.get('href') # Keep original to always process the DDG part
                cleaned_href = original_href # Start with original

                if original_href:
                    # --- NEW LOGIC: Always extract actual URL from DuckDuckGo redirect if present ---
                    if 'duckduckgo.com/l/?uddg=' in original_href:
                        parsed_ddg_url = urlparse(original_href)
                        query_params = parse_qs(parsed_ddg_url.query)
                        if 'uddg' in query_params and query_params['uddg']:
                            cleaned_href = unquote(query_params['uddg'][0])
                    # --- END NEW LOGIC ---

                    if 'facebook.com' in cleaned_href and not fb_link:
                        if 'search' not in cleaned_href and 'directory' not in cleaned_href and 'public' not in cleaned_href:
                            fb_link = cleaned_href
                    if 'instagram.com' in cleaned_href and not insta_link:
                        if 'explore' not in cleaned_href and 'accounts/login' not in cleaned_href:
                            insta_link = cleaned_href
                if fb_link and insta_link: break
        return fb_link, insta_link
    except HTTPError as e:
        if e.response.status_code == 429:
            print(f"    Crawlbase Rate Limit (429) hit for social search query '{query}'. Retrying after 60 seconds.")
            time.sleep(random.uniform(60, 120))
            response = requests.get(CRAWLBASE_API_URL_NORMAL, params=crawlbase_params, timeout=30)
            response.raise_for_status()
        elif e.response.status_code >= 400:
            print(f"    HTTP error for social search '{pojam}' (Phone: {scraped_phone or 'N/A'}): Status {e.response.status_code}, Response: {e.response.text.strip() if e.response.text else 'No response body'}")
        return None, None
    except RequestException as e:
        print(f"    Network/Request error for social search query '{query}': {e}")
        return None, None
    except Exception as e:
        print(f"    An unexpected error occurred during social search query '{query}': {e}")
        return None, None

# --- Function: Scrape website details ---
def scrape_website_details(url):
    business_name = None
    phone_number = None

    if not url or not isinstance(url, str) or not url.startswith('http'):
        return None, None

    crawlbase_params = {
        'token': CRAWLBASE_JS_TOKEN,
        'url': url,
        'user_agent': random.choice(user_agents),
        'js_render': 'true'
    }

    try:
        response = requests.get(CRAWLBASE_API_URL_JS, params=crawlbase_params, timeout=60)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        og_site_name = soup.find('meta', property='og:site_name')
        if og_site_name and og_site_name.get('content'):
            business_name = og_site_name.get('content').strip()

        if not business_name:
            og_title = soup.find('meta', property='og:title')
            if og_title and og_title.get('content'):
                business_name = og_title.get('content').strip()

        if not business_name and soup.title:
            business_name = soup.title.string.strip()

        if not business_name:
            h1_tags = soup.find_all('h1')
            if h1_tags:
                business_name = max(h1_tags, key=lambda x: len(x.get_text().strip()), default=None)
                if business_name:
                    business_name = business_name.get_text().strip()

        if business_name:
            business_name = re.sub(r'\s*[|/\-]+?\s*(Booking\.com|Airbnb|Accommodation|Hotels|Apartments|Guest House|Villa)\b.*', '', business_name, flags=re.IGNORECASE)
            business_name = re.sub(r'\s*[|/\-]+?\s*$', '', business_name)
            business_name = business_name.strip()
            if len(business_name) < 3:
                business_name = None

        phone_number = None
        phone_patterns = [
            r'\+?\d{1,4}[\s.\-]?\(?\d{1,4}\)?[\s.\-]?\d{1,4}[\s.\-]?\d{1,4}[\s.\-]?\d{1,9}',
            r'\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}',
            r'\b\d{9,15}\b'
        ]

        text_content = soup.get_text(separator=' ', strip=True)
        for pattern in phone_patterns:
            matches = re.findall(pattern, text_content)
            for match_str in matches:
                cleaned_phone = re.sub(r'[^\d+]', '', match_str)
                if len(cleaned_phone) >= 7:
                    phone_number = cleaned_phone
                    break
            if phone_number:
                break

    except HTTPError as e:
        if e.response.status_code == 429:
            print(f"    Crawlbase Rate Limit (429) hit for website '{url}'. Status: {e.response.status_code}, Response: {e.response.text}")
            time.sleep(random.uniform(60, 120))
            response = requests.get(CRAWLBASE_API_URL_JS, params=crawlbase_params, timeout=60)
            response.raise_for_status()
        elif e.response.status_code >= 400:
            print(f"    HTTP error fetching '{url}': Status {e.response.status_code}, Reason: {e.response.reason}")
        return None, None
    except RequestException as e:
        print(f"    Network/Request error fetching '{url}': {e}")
        pass
    except Exception as e:
        print(f"    An unexpected error occurred for '{url}': {e}")
        pass

    return business_name, phone_number

print("✅ Web scraping and social search functions redefined.")




print("🚀 Starting social media link extraction for leads without email and existing social links...")

# Ensure 'Facebook' and 'Instagram' columns exist in df_leads_without_email and are of object type
if 'Facebook' not in df_leads_without_email.columns:
    df_leads_without_email['Facebook'] = np.nan
    df_leads_without_email['Facebook'] = df_leads_without_email['Facebook'].astype(str) # Ensure object type
else:
    df_leads_without_email['Facebook'] = df_leads_without_email['Facebook'].astype(str)

if 'Instagram' not in df_leads_without_email.columns:
    df_leads_without_email['Instagram'] = np.nan
    df_leads_without_email['Instagram'] = df_leads_without_email['Instagram'].astype(str) # Ensure object type
else:
    df_leads_without_email['Instagram'] = df_leads_without_email['Instagram'].astype(str)

# Filter for leads that still don't have an email
mask_no_email = df_leads_without_email['EXTRACTED_EMAIL'].isna()

# Filter for leads that don't have a Facebook link (or have 'no social found' placeholder)
mask_no_facebook = df_leads_without_email['Facebook'].isna() | (df_leads_without_email['Facebook'].astype(str).str.lower() == 'no social found')

# Filter for leads that don't have an Instagram link (or have 'no social found' placeholder)
mask_no_instagram = df_leads_without_email['Instagram'].isna() | (df_leads_without_email['Instagram'].astype(str).str.lower() == 'no social found')

# Combine masks: leads without email AND without Facebook AND without Instagram
leads_for_social_search = df_leads_without_email[mask_no_email & mask_no_facebook & mask_no_instagram].copy()

print(f"Found {len(leads_for_social_search)} leads that still need social media search.")

# Counter for newly found social media links
new_social_links_found_this_session = 0

# Iterate through each row of the leads_for_social_search DataFrame
for index, row in leads_for_social_search.iterrows():
    lead_name = row.get('Name', 'N/A')
    lead_address = row.get('Address', '')

    # Construct the search query
    query_parts = []
    if pd.notna(lead_name) and len(str(lead_name).strip()) > 2:
        query_parts.append(str(lead_name).strip())
    if pd.notna(lead_address) and len(str(lead_address).strip()) > 5:
        query_parts.append(str(lead_address).strip())
    query = ' '.join(query_parts)

    # If no meaningful query can be formed, skip
    if not query:
        print(f"   ⚠️ Skipping {lead_name}: Insufficient search terms (Name or Address missing).")
        # Mark as 'no social found' to avoid re-processing
        df_leads_without_email.loc[index, 'Facebook'] = 'no social found'
        df_leads_without_email.loc[index, 'Instagram'] = 'no social found'
        time.sleep(random.uniform(1, 3))
        continue

    print(f"\n🔍 Searching social media for: '{query}' (Lead: {lead_name})")

    # Call the trazi_social_linkove function (defined earlier)
    found_fb, found_insta = trazi_social_linkove(query)

    # Check if a new Facebook link was found and update df_leads_without_email
    if found_fb and (df_leads_without_email.loc[index, 'Facebook'] == 'nan' or df_leads_without_email.loc[index, 'Facebook'] == 'no social found'):
        df_leads_without_email.loc[index, 'Facebook'] = found_fb
        new_social_links_found_this_session += 1
        print(f"   ✅ Found Facebook: {found_fb}")
    elif df_leads_without_email.loc[index, 'Facebook'] == 'nan' or df_leads_without_email.loc[index, 'Facebook'] == 'no social found':
        df_leads_without_email.loc[index, 'Facebook'] = 'no social found'
        print("   😔 No new Facebook link found.")

    # Check if a new Instagram link was found and update df_leads_without_email
    if found_insta and (df_leads_without_email.loc[index, 'Instagram'] == 'nan' or df_leads_without_email.loc[index, 'Instagram'] == 'no social found'):
        df_leads_without_email.loc[index, 'Instagram'] = found_insta
        new_social_links_found_this_session += 1
        print(f"   ✅ Found Instagram: {found_insta}")
    elif df_leads_without_email.loc[index, 'Instagram'] == 'nan' or df_leads_without_email.loc[index, 'Instagram'] == 'no social found':
        df_leads_without_email.loc[index, 'Instagram'] = 'no social found'
        print("   😔 No new Instagram link found.")

    # Implement a time delay
    time.sleep(random.uniform(1, 3)) # Random delay between 1 and 3 seconds

print(f"\n🏁 Social media link extraction for leads without email and existing social links finished. Found {new_social_links_found_this_session} new social media links.")

# Save the modified df_leads_without_email DataFrame
output_file_without_email = 'LEADOVI_BEZ_EMAILA.csv'
df_leads_without_email.to_csv(output_file_without_email, index=False)
print(f"✅ Updated DataFrame saved to '{output_file_without_email}'.")

# Display the first 5 rows of the updated df_leads_without_email DataFrame
print("\nFirst 5 rows of df_leads_without_email after additional social media extraction:")
df_leads_without_email.head()

# Print a summary of the DataFrame's structure
print("\nDataFrame Info after additional social media extraction:")
df_leads_without_email.info()



======

import pandas as pd
import numpy as np
import os

print("## Reload Leads Without Email (Updated)\n")

# 1. Define the filename
leads_without_email_file = 'LEADOVI_BEZ_EMAILA.csv'

# 2. Load the CSV file into a pandas DataFrame, ensuring all columns are read as strings
df_leads_without_email = pd.read_csv(leads_without_email_file, dtype=str)

# 3. Check if the 'EXTRACTED_EMAIL' column exists
if 'EXTRACTED_EMAIL' not in df_leads_without_email.columns:
    # If it does not exist, create it and initialize all its values to np.nan
    df_leads_without_email['EXTRACTED_EMAIL'] = np.nan
    print("Created 'EXTRACTED_EMAIL' column and initialized with NaN values.")
else:
    # 4. If it exists, standardize it by converting it to string type and replacing any empty strings, 'nan' (as a string), or 'no email found' with np.nan.
    df_leads_without_email['EXTRACTED_EMAIL'] = df_leads_without_email['EXTRACTED_EMAIL'].astype(str)
    df_leads_without_email['EXTRACTED_EMAIL'] = df_leads_without_email['EXTRACTED_EMAIL'].replace(['', 'nan', 'no email found'], np.nan)
    print("Standardized 'EXTRACTED_EMAIL' column by replacing empty/placeholder values with NaN.")

# 5. Display the first 5 rows of the loaded DataFrame
print("\nFirst 5 rows of LEADOVI_BEZ_EMAILA.csv:")
df_leads_without_email.head()

# 6. Display a summary of the DataFrame's structure
print("\nDataFrame Info:")
df_leads_without_email.info()



=====


%load_ext cudf.pandas
import pandas as pd
import numpy as np

# Randomly generated dataset of parking violations-
# Define the number of rows
num_rows = 1000000

states = ["NY", "NJ", "CA", "TX"]
violations = ["Double Parking", "Expired Meter", "No Parking",
              "Fire Hydrant", "Bus Stop"]
vehicle_types = ["SUBN", "SDN"]

# Create a date range
start_date = "2022-01-01"
end_date = "2022-12-31"
dates = pd.date_range(start=start_date, end=end_date, freq='D')

# Generate random data
data = {
    "Registration State": np.random.choice(states, size=num_rows),
    "Violation Description": np.random.choice(violations, size=num_rows),
    "Vehicle Body Type": np.random.choice(vehicle_types, size=num_rows),
    "Issue Date": np.random.choice(dates, size=num_rows),
    "Ticket Number": np.random.randint(1000000000, 9999999999, size=num_rows)
}

# Create a DataFrame
df = pd.DataFrame(data)

# Which parking violation is most commonly committed by vehicles from various U.S states?

(df[["Registration State", "Violation Description"]]  # get only these two columns
 .value_counts()  # get the count of offences per state and per type of offence
 .groupby("Registration State")  # group by state
 .head(1)  # get the first row in each group (the type of offence with the largest count)
 .sort_index()  # sort by state name
 .reset_index()
)


======

%load_ext cudf.pandas
import pandas as pd
import numpy as np

# Randomly generated dataset of parking violations-
# Define the number of rows
num_rows = 1000000

states = ["NY", "NJ", "CA", "TX"]
violations = ["Double Parking", "Expired Meter", "No Parking",
              "Fire Hydrant", "Bus Stop"]
vehicle_types = ["SUBN", "SDN"]

# Create a date range
start_date = "2022-01-01"
end_date = "2022-12-31"
dates = pd.date_range(start=start_date, end=end_date, freq='D')

# Generate random data
data = {
    "Registration State": np.random.choice(states, size=num_rows),
    "Violation Description": np.random.choice(violations, size=num_rows),
    "Vehicle Body Type": np.random.choice(vehicle_types, size=num_rows),
    "Issue Date": np.random.choice(dates, size=num_rows),
    "Ticket Number": np.random.randint(1000000000, 9999999999, size=num_rows)
}

# Create a DataFrame
df = pd.DataFrame(data)

# Which parking violation is most commonly committed by vehicles from various U.S states?

(df[["Registration State", "Violation Description"]]  # get only these two columns
 .value_counts()  # get the count of offences per state and per type of offence
 .groupby("Registration State")  # group by state
 .head(1)  # get the first row in each group (the type of offence with the largest count)
 .sort_index()  # sort by state name
 .reset_index()
)



======

%load_ext cudf.pandas
import pandas as pd
import numpy as np

# Randomly generated dataset of parking violations-
# Define the number of rows
num_rows = 1000000

states = ["NY", "NJ", "CA", "TX"]
violations = ["Double Parking", "Expired Meter", "No Parking",
              "Fire Hydrant", "Bus Stop"]
vehicle_types = ["SUBN", "SDN"]

# Create a date range
start_date = "2022-01-01"
end_date = "2022-12-31"
dates = pd.date_range(start=start_date, end=end_date, freq='D')

# Generate random data
data = {
    "Registration State": np.random.choice(states, size=num_rows),
    "Violation Description": np.random.choice(violations, size=num_rows),
    "Vehicle Body Type": np.random.choice(vehicle_types, size=num_rows),
    "Issue Date": np.random.choice(dates, size=num_rows),
    "Ticket Number": np.random.randint(1000000000, 9999999999, size=num_rows)
}

# Create a DataFrame
df = pd.DataFrame(data)

# Which parking violation is most commonly committed by vehicles from various U.S states?

(df[["Registration State", "Violation Description"]]  # get only these two columns
 .value_counts()  # get the count of offences per state and per type of offence
 .groupby("Registration State")  # group by state
 .head(1)  # get the first row in each group (the type of offence with the largest count)
 .sort_index()  # sort by state name
 .reset_index()
)



======

%load_ext cudf.pandas
import pandas as pd
import numpy as np

# Randomly generated dataset of parking violations-
# Define the number of rows
num_rows = 1000000

states = ["NY", "NJ", "CA", "TX"]
violations = ["Double Parking", "Expired Meter", "No Parking",
              "Fire Hydrant", "Bus Stop"]
vehicle_types = ["SUBN", "SDN"]

# Create a date range
start_date = "2022-01-01"
end_date = "2022-12-31"
dates = pd.date_range(start=start_date, end=end_date, freq='D')

# Generate random data
data = {
    "Registration State": np.random.choice(states, size=num_rows),
    "Violation Description": np.random.choice(violations, size=num_rows),
    "Vehicle Body Type": np.random.choice(vehicle_types, size=num_rows),
    "Issue Date": np.random.choice(dates, size=num_rows),
    "Ticket Number": np.random.randint(1000000000, 9999999999, size=num_rows)
}

# Create a DataFrame
df = pd.DataFrame(data)

# Which parking violation is most commonly committed by vehicles from various U.S states?

(df[["Registration State", "Violation Description"]]  # get only these two columns
 .value_counts()  # get the count of offences per state and per type of offence
 .groupby("Registration State")  # group by state
 .head(1)  # get the first row in each group (the type of offence with the largest count)
 .sort_index()  # sort by state name
 .reset_index()
)


===========


%load_ext cudf.pandas
import pandas as pd
import numpy as np

# Randomly generated dataset of parking violations-
# Define the number of rows
num_rows = 1000000

states = ["NY", "NJ", "CA", "TX"]
violations = ["Double Parking", "Expired Meter", "No Parking",
              "Fire Hydrant", "Bus Stop"]
vehicle_types = ["SUBN", "SDN"]

# Create a date range
start_date = "2022-01-01"
end_date = "2022-12-31"
dates = pd.date_range(start=start_date, end=end_date, freq='D')

# Generate random data
data = {
    "Registration State": np.random.choice(states, size=num_rows),
    "Violation Description": np.random.choice(violations, size=num_rows),
    "Vehicle Body Type": np.random.choice(vehicle_types, size=num_rows),
    "Issue Date": np.random.choice(dates, size=num_rows),
    "Ticket Number": np.random.randint(1000000000, 9999999999, size=num_rows)
}

# Create a DataFrame
df = pd.DataFrame(data)

# Which parking violation is most commonly committed by vehicles from various U.S states?

(df[["Registration State", "Violation Description"]]  # get only these two columns
 .value_counts()  # get the count of offences per state and per type of offence
 .groupby("Registration State")  # group by state
 .head(1)  # get the first row in each group (the type of offence with the largest count)
 .sort_index()  # sort by state name
 .reset_index()
)


========

import pandas as pd
import numpy as np
import os
import ipywidgets as widgets
from IPython.display import display, clear_output
from urllib.parse import unquote, urlparse, parse_qs # Import necessary modules

print("🚀 POKREĆEM PAMETNI MANUALNI UNOS ZA LEADOVE SA DRUŠTVENIM MREŽAMA...")

# --- 1. POSTAVKE ---
# Use the df_leads_without_email DataFrame loaded in the previous step
df = df_leads_without_email.copy()
fajl_za_snimanje = 'LEADOVI_BEZ_EMAILA.csv' # Define output file for saving changes

# Ensure 'EXTRACTED_EMAIL' column exists and is standardized (replacing empty strings, 'nan', 'no email found' with actual NaN)
if 'EXTRACTED_EMAIL' not in df.columns:
    df['EXTRACTED_EMAIL'] = np.nan
else:
    df['EXTRACTED_EMAIL'] = df['EXTRACTED_EMAIL'].astype(str)
    df['EXTRACTED_EMAIL'] = df['EXTRACTED_EMAIL'].replace(['', 'nan', 'no email found'], np.nan)

# --- 2. LOGIKA ZA FILTRIRANJE ---
# Tražimo redove koji nemaju email, ali IMAJU Facebook, Instagram ili Website link
def ima_pravi_link(row):
    # Check for non-null and not 'no social found' or 'nan' values in 'Facebook' or 'Instagram' columns
    if pd.notna(row.get('Facebook')) and str(row.get('Facebook')).lower() not in ('no social found', 'nan'):
        return True
    if pd.notna(row.get('Instagram')) and str(row.get('Instagram')).lower() not in ('no social found', 'nan'):
        return True
    # Also include Website as a fallback if social links are not available/found
    if pd.notna(row.get('Website')) and str(row.get('Website')).lower() not in ('no social found', 'nan'):
        return True
    return False

# Filtriraj: Samo oni bez emaila I s pravim linkom
maska_bez_emaila = df['EXTRACTED_EMAIL'].isna()
maska_pravi_link = df.apply(ima_pravi_link, axis=1)

indeksi_za_obradu = df[maska_bez_emaila & maska_pravi_link].index.tolist()

print(f"✅ Pronađeno {len(indeksi_za_obradu)} redova s ispravnim linkovima za obradu.\n")

trenutni_korak = 0
rows_to_delete = set() # Set za čuvanje indeksa redova za brisanje

# Track emails added in this session for summary
initial_leads_without_email_count = len(indeksi_za_obradu)
session_emails_added = 0

# --- 3. WIDGETI ---
polje_email = widgets.Text(placeholder='Zalijepi email ovdje...', description='📧 Email:', layout=widgets.Layout(width='50%'))

gumb_spremi = widgets.Button(description="✅ SPREMI", button_style='success')
gumb_preskoci = widgets.Button(description="⏩ NEMA EMAILA / DALJE", button_style='warning')
gumb_izbrisi = widgets.Button(description="🗑️ OBRIŠI LEAD", button_style='danger')
gumb_kraj = widgets.Button(description="💾 KRAJ I SPREMI", button_style='danger')
output_area = widgets.Output()

def nadji_pravi_link(row):
    # Prioritet: Facebook > Instagram > Website
    fb_link = row.get('Facebook')
    insta_link = row.get('Instagram')
    website_link = row.get('Website')

    selected_link = None
    button_text = None
    button_style = None

    # Check and clean Facebook link
    if pd.notna(fb_link) and str(fb_link).lower() not in ('no social found', 'nan'):
        cleaned_fb_link = str(fb_link)
        if 'duckduckgo.com/l/?uddg=' in cleaned_fb_link:
            parsed_ddg_url = urlparse(cleaned_fb_link)
            query_params = parse_qs(parsed_ddg_url.query)
            if 'uddg' in query_params and query_params['uddg']:
                cleaned_fb_link = unquote(query_params['uddg'][0])
        if 'facebook.com' in cleaned_fb_link:
            selected_link = cleaned_fb_link
            button_text = "OTVORI FACEBOOK PROFIL"
            button_style = "background-color: #3b5998;"

    # Check and clean Instagram link (if Facebook not found or invalid)
    if selected_link is None and pd.notna(insta_link) and str(insta_link).lower() not in ('no social found', 'nan'):
        cleaned_insta_link = str(insta_link)
        if 'duckduckgo.com/l/?uddg=' in cleaned_insta_link:
            parsed_ddg_url = urlparse(cleaned_insta_link)
            query_params = parse_qs(parsed_ddg_url.query)
            if 'uddg' in query_params and query_params['uddg']:
                cleaned_insta_link = unquote(query_params['uddg'][0])
        if 'instagram.com' in cleaned_insta_link:
            selected_link = cleaned_insta_link
            button_text = "OTVORI INSTAGRAM PROFIL"
            button_style = "background-color: #C13584;"

    # Check and clean Website link (if neither Facebook nor Instagram found or invalid)
    if selected_link is None and pd.notna(website_link) and str(website_link).lower() not in ('no social found', 'nan'):
        cleaned_website_link = str(website_link)
        if not cleaned_website_link.startswith('http'):
            cleaned_website_link = 'http://' + cleaned_website_link
        selected_link = cleaned_website_link
        button_text = "🌐 OTVORI WEB STRANICU"
        button_style = "background-color: #ff9900;"

    return selected_link, button_text, button_style

def prikazi_sljedeci():
    global trenutni_korak, indeksi_za_obradu, rows_to_delete
    output_area.clear_output()

    # Preskači redove koji su označeni za brisanje
    while trenutni_korak < len(indeksi_za_obradu) and \
          (indeksi_za_obradu[trenutni_korak] in rows_to_delete):
        trenutni_korak += 1

    if trenutni_korak >= len(indeksi_za_obradu):
        with output_area:
            print("🎉 GOTOVO! Nema više linkova.")
        return

    idx = indeksi_za_obradu[trenutni_korak]
    row = df.loc[idx]

    link, button_text, button_style = nadji_pravi_link(row)
    ime = row.get('Name', 'N/A')

    with output_area:
        print("-" * 60)
        print(f"🏢 FIRMA: {ime}")
        print(f"📊 NAPREDAK: {trenutni_korak + 1} / {len(indeksi_za_obradu)}")
        print(f"Originalni indeks: {idx}")

        if link:
            html = f'''
            <div style="margin: 20px 0;">
                <a href="{link}" target="_blank" style="{button_style} color: white; padding: 15px 30px; text-decoration: none; font-size: 18px; border-radius: 5px; display: inline-block; font-family: sans-serif;">
                    {button_text}
                </a>
                <br><small style="color: grey;">Link: {link}</small>
            </div>
            '''
            display(widgets.HTML(html))
        else:
            print("⚠️ Greška: Ne mogu naći ispravan link u ovom redu.")

    polje_email.value = ""

def na_klik_spremi(b):
    global trenutni_korak, indeksi_za_obradu, session_emails_added
    if not indeksi_za_obradu or trenutni_korak >= len(indeksi_za_obradu): return

    idx = indeksi_za_obradu[trenutni_korak]
    email = polje_email.value.strip()

    if email and '@' in email:
        df.at[idx, 'EXTRACTED_EMAIL'] = email
        session_emails_added += 1 # Increment counter for summary
        # Autosave (only email changes are applied immediately to df, deletions are handled at the end)
        df.to_csv(fajl_za_snimanje, index=False)
        with output_area:
            print("✅ Email uspješno zapisan.")
        trenutni_korak += 1
        prikazi_sljedeci()
    else:
        with output_area:
            print("⚠️ Moraš upisati ispravan email (mora imati @)!")

def na_klik_preskoci(b):
    global trenutni_korak, indeksi_za_obradu
    if not indeksi_za_obradu or trenutni_korak >= len(indeksi_za_obradu): return

    idx = indeksi_za_obradu[trenutni_korak]
    df.at[idx, 'EXTRACTED_EMAIL'] = 'no email found'
    df.to_csv(fajl_za_snimanje, index=False) # Sačuvaj promenu u fajl

    with output_area:
        print("⏩ Red preskočen i označen kao 'no email found'.")
    trenutni_korak += 1
    prikazi_sljedeci()

def na_klik_izbrisi(b):
    global trenutni_korak, indeksi_za_obradu, rows_to_delete

    if not indeksi_za_obradu or trenutni_korak >= len(indeksi_za_obradu):
        with output_area:
            print("🎉 Nema više redova za obradu/brisanje.")
        return

    idx_to_mark = indeksi_za_obradu[trenutni_korak]
    rows_to_delete.add(idx_to_mark)

    with output_area:
        print(f"🗑️ Red sa originalnim indeksom {idx_to_mark} označen za brisanje.")

    trenutni_korak += 1
    prikazi_sljedeci()

def na_klik_kraj(b):
    global df, session_emails_added
    output_area.clear_output()

    if rows_to_delete:
        df_leads_final_processed = df.drop(index=list(rows_to_delete))
        with output_area:
            print(f"🗑️ Izbrisano {len(rows_to_delete)} redova iz glavnog fajla prije spremanja.")
    else:
        df_leads_final_processed = df.copy()

    df_leads_final_processed.to_csv(fajl_za_snimanje, index=False)
    with output_area:
        print(f"💾 SPREMLJENO! Ažurirani fajl '{fajl_za_snimanje}' je sačuvan.\n")
        print("👉 Sada možeš skinuti fajlove ili nastaviti sa sljedećim koracima.")

        remaining_leads_without_email = df_leads_final_processed['EXTRACTED_EMAIL'].isna().sum()

        print(f"\n--- Rezultati ručne obrade ---")
        print(f"Ukupno leadova za ručnu obradu na početku: {initial_leads_without_email_count}")
        print(f"Broj emaila dodanih ručno: {session_emails_added}")
        print(f"Broj leadova označenih za brisanje: {len(rows_to_delete)}")
        print(f"Preostalo leadova bez emaila (u '{fajl_za_snimanje}' nakon obrade): {remaining_leads_without_email}")
        print(f"Fajl '{fajl_za_snimanje}' je ažuriran i sadrži {len(df_leads_final_processed)} redova.")
    rows_to_delete.clear()


# Povezivanje dugmadi
gumb_spremi.on_click(na_klik_spremi)
gumb_preskoci.on_click(na_klik_preskoci)
gumb_izbrisi.on_click(na_klik_izbrisi)
gumb_kraj.on_click(na_klik_kraj)

# Prikaz
display(output_area)
display(widgets.HBox([polje_email]))
display(widgets.HBox([gumb_spremi, gumb_preskoci, gumb_izbrisi, gumb_kraj]))

prikazi_sljedeci()



================


import requests
import re
from requests.exceptions import RequestException, HTTPError

# --- Function to fetch website content ---
def fetch_website_content(url):
    if not url or not isinstance(url, str):
        return None

    # Add http if missing for robust request handling
    if not url.startswith('http://') and not url.startswith('https://'):
        url = 'http://' + url

    try:
        # Set a timeout for the request to avoid hanging indefinitely
        response = requests.get(url, timeout=10)
        # Raise an exception for HTTP errors (4xx or 5xx)
        response.raise_for_status()
        return response.text
    except HTTPError as e:
        print(f"HTTP Error fetching {url}: {e}")
        return None
    except RequestException as e:
        print(f"Request Error fetching {url}: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred while fetching {url}: {e}")
        return None

# --- Function to extract emails from content ---
def extract_emails_from_content(html_content):
    if not html_content:
        return []

    # Regular expression for email addresses
    # Corrected: Removed extra backslashes from the regex pattern
    email_regex = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'

    # Find all unique email addresses
    found_emails = set(re.findall(email_regex, html_content, re.IGNORECASE))

    return list(found_emails)

print("Web scraping functions (fetch_website_content, extract_emails_from_content) defined.")


======

import pandas as pd

# Define the input filename
input_file = 'Biznis Klima uređaji Hrvatska.csv'

# Load the CSV file into a pandas DataFrame, ensuring all columns are read as strings
df_master_leads = pd.read_csv(input_file, dtype=str)

# Display the first 5 rows of the df_master_leads DataFrame
print("First 5 rows of df_master_leads:")
df_master_leads.head()

# Print a summary of the DataFrame's structure and data types
print("\nDataFrame Info:")
df_master_leads.info()


=====

import numpy as np

# Create a new DataFrame for processed leads
df_processed_outreach = pd.DataFrame()

# Populate 'email' column
df_processed_outreach['email'] = df_master_leads['EXTRACTED_EMAIL'].replace(['nan', ''], np.nan)

# Populate 'website' column
df_processed_outreach['website'] = df_master_leads['Website'].replace(['nan', ''], np.nan)

# Populate 'category' column
df_processed_outreach['category'] = df_master_leads['Category'].replace(['nan', ''], np.nan)

# Display the first 5 rows and info of the new DataFrame
print("First 5 rows of df_processed_outreach:")
df_processed_outreach.head()

print("\nDataFrame Info of df_processed_outreach:")
df_processed_outreach.info()


========

import numpy as np

# Populate 'first_name' column by extracting the first word from the 'Name' column
df_processed_outreach['first_name'] = df_master_leads['Name'].astype(str).apply(lambda x: x.split(' ')[0] if pd.notna(x) and x.strip() != '' else np.nan)

# Populate 'location' column from the 'Address' column
df_processed_outreach['location'] = df_master_leads['Address'].replace(['nan', ''], np.nan)

# Display the first 5 rows and info of the updated DataFrame
print("First 5 rows of df_processed_outreach after adding 'first_name' and 'location':")
df_processed_outreach.head()

print("\nDataFrame Info of df_processed_outreach:")
df_processed_outreach.info()


========

import numpy as np
import os

# Populate 'pain_point' column based on 'email' and 'website' status
def get_pain_point(row):
    has_email = pd.notna(row['email'])
    has_website = pd.notna(row['website'])

    if not has_email and not has_website:
        return 'No Email, No Website'
    elif not has_email:
        return 'No Email'
    elif not has_website:
        return 'No Website'
    else:
        return np.nan # No specific pain point if both exist

df_processed_outreach['pain_point'] = df_processed_outreach.apply(get_pain_point, axis=1)

# Define the output filename
output_file_outreach = 'Processed_Leads_for_Outreach.csv'

# Save the processed DataFrame to a new CSV file
df_processed_outreach.to_csv(output_file_outreach, index=False)

print(f"✅ Processed leads for outreach saved to '{output_file_outreach}'.")

# Display the first 5 rows and info of the final DataFrame
print("\nFinal df_processed_outreach after adding 'pain_point' and saving:")
df_processed_outreach.head()

print("\nDataFrame Info of df_processed_outreach:")
df_processed_outreach.info()


=======

import pandas as pd
import numpy as np
import os
import ipywidgets as widgets
from IPython.display import display, clear_output

print("🚀 POKREĆEM ALAT ZA RUČNU PROVJERU I BRISANJE LEADOVA...")

# --- 1. POSTAVKE ---
input_file = 'Processed_Leads_for_Outreach.csv'

# Provjera postojanja ulaznog fajla
if not os.path.exists(input_file):
    print(f"❌ Nema fajla '{input_file}'! Molimo provjerite da li je uploadovan ili je prethodni korak uspješno generisao fajl.")
else:
    try:
        df_leads_for_deletion = pd.read_csv(input_file, dtype=str)

        # Standardize 'email', 'website', and 'pain_point' columns
        for col in ['email', 'website', 'pain_point']:
            if col in df_leads_for_deletion.columns:
                df_leads_for_deletion[col] = df_leads_for_deletion[col].replace(['', 'nan'], np.nan)
            else:
                df_leads_for_deletion[col] = np.nan # Create if not exists

        # Create a list of all DataFrame indices to process
        indeksi_za_obradu = df_leads_for_deletion.index.tolist()

        print(f"✅ Pronađeno {len(indeksi_za_obradu)} leadova za ručnu provjeru.")

        trenutni_korak = 0
        rows_to_delete = set() # Set za čuvanje indeksa redova za brisanje

        # --- 2. WIDGETI ---
        polje_email = widgets.Text(placeholder='Upišite/uredite email ovdje...', description='📧 Email:', layout=widgets.Layout(width='60%'))
        info_label = widgets.Output() # For displaying current email status and pain point

        gumb_spremi = widgets.Button(description="✅ SPREMI EMAIL", button_style='success', layout=widgets.Layout(width='200px'))
        gumb_preskoci = widgets.Button(description="⏩ PRESKOČI", button_style='warning', layout=widgets.Layout(width='180px'))
        gumb_izbrisi = widgets.Button(description="🗑️ IZBRIŠI LEAD", button_style='danger', layout=widgets.Layout(width='180px'))
        gumb_kraj = widgets.Button(description="💾 KRAJ I SPREMI", button_style='danger', layout=widgets.Layout(width='150px'))

        output_area = widgets.Output()

        # Function to re-calculate pain_point for a single row
        def get_pain_point_for_row(row):
            has_email = pd.notna(row['email'])
            has_website = pd.notna(row['website'])

            if not has_email and not has_website:
                return 'No Email, No Website'
            elif not has_email:
                return 'No Email'
            elif not has_website:
                return 'No Website'
            else:
                return np.nan

        def display_next_entry():
            global trenutni_korak, indeksi_za_obradu, rows_to_delete
            output_area.clear_output()
            info_label.clear_output()

            # Skip rows already marked for deletion
            while trenutni_korak < len(indeksi_za_obradu) and \
                  (indeksi_za_obradu[trenutni_korak] in rows_to_delete):
                trenutni_korak += 1

            if trenutni_korak >= len(indeksi_za_obradu):
                with output_area:
                    print("🎉 GOTOVO! Nema više leadova za ručnu provjeru.")
                polje_email.disabled = True
                gumb_spremi.disabled = True
                gumb_preskoci.disabled = True
                gumb_izbrisi.disabled = True
                return

            idx = indeksi_za_obradu[trenutni_korak]
            row = df_leads_for_deletion.loc[idx]

            # Display relevant info
            with output_area:
                print("-" * 60)
                print(f"🏢 FIRMA: {row.get('first_name', 'N/A')}")
                print(f"📊 NAPREDAK: {trenutni_korak + 1} / {len(indeksi_za_obradu)}")
                print(f"Originalni indeks: {idx}")
                print(f"📍 LOKACIJA: {row.get('location', 'N/A')}")
                print(f"🏷️ KATEGORIJA: {row.get('category', 'N/A')}")
                print(f"⚠️ PAIN POINT: {row.get('pain_point', 'N/A')}")

                if pd.notna(row['website']):
                    website_link = str(row['website'])
                    if not website_link.startswith('http'):
                        website_link = 'http://' + website_link
                    html = f'''
                    <div style="margin: 10px 0;">
                        <a href="{website_link}" target="_blank" style="background-color: #007bff; color: white; padding: 10px 20px; text-decoration: none; font-size: 16px; border-radius: 5px; display: inline-block; font-family: sans-serif;">
                            🌐 POSJETI WEBSTRANICU
                        </a>
                        <br><small style="color: grey; margin-top: 5px; display: block;">Link: {row['website']}</small>
                    </div>
                    '''
                    display(widgets.HTML(html))

            # Pre-populate email field and update info_label
            polje_email.value = str(row['email']) if pd.notna(row['email']) else ''
            with info_label:
                current_email_status = 'NEMA EMAILA' if pd.isna(row['email']) else f"Trenutni email: {row['email']}"
                print(f"✉️ {current_email_status}")

        def on_save_email_click(b):
            global trenutni_korak
            if trenutni_korak >= len(indeksi_za_obradu): return

            idx = indeksi_za_obradu[trenutni_korak]
            entered_email = polje_email.value.strip()

            with output_area:
                if entered_email and '@' in entered_email:
                    df_leads_for_deletion.at[idx, 'email'] = entered_email
                    print(f"✅ Email '{entered_email}' uspješno zapisan.")
                else:
                    df_leads_for_deletion.at[idx, 'email'] = np.nan # Clear invalid/empty email
                    print("⚠️ Email obrisan ili nevažeći, postavljen na NaN.")

            # Recalculate pain_point for the modified row immediately
            df_leads_for_deletion.at[idx, 'pain_point'] = get_pain_point_for_row(df_leads_for_deletion.loc[idx])

            trenutni_korak += 1
            display_next_entry()

        def on_skip_click(b):
            global trenutni_korak
            if trenutni_korak >= len(indeksi_za_obradu): return
            with output_area:
                print("⏩ Lead preskočen.")
            trenutni_korak += 1
            display_next_entry()

        def on_delete_click(b):
            global trenutni_korak, rows_to_delete
            if trenutni_korak >= len(indeksi_za_obradu): return

            idx_to_mark = indeksi_za_obradu[trenutni_korak]
            rows_to_delete.add(idx_to_mark)

            with output_area:
                print(f"🗑️ Lead sa originalnim indeksom {idx_to_mark} označen za brisanje.")

            trenutni_korak += 1
            display_next_entry()

        def on_finish_click(b):
            global df_leads_for_deletion
            output_area.clear_output()
            info_label.clear_output()

            initial_total_rows = len(df_leads_for_deletion)

            if rows_to_delete:
                df_leads_for_deletion = df_leads_for_deletion.drop(index=list(rows_to_delete)).reset_index(drop=True)
                with output_area:
                    print(f"🗑️ Izbrisano {len(rows_to_delete)} leadova iz glavnog fajla.")

            # Re-calculate pain_point for all remaining leads (in case some were edited or deleted)
            df_leads_for_deletion['pain_point'] = df_leads_for_deletion.apply(get_pain_point_for_row, axis=1)

            df_leads_for_deletion.to_csv(input_file, index=False)
            with output_area:
                print(f"💾 SPREMLJENO! Ažurirani fajl '{input_file}' je sačuvan.")
                print("👉 Sada možeš skinuti fajl ili nastaviti sa sljedećim koracima.")

                final_total_rows = len(df_leads_for_deletion)
                leads_with_email = df_leads_for_deletion['email'].notna().sum()
                leads_with_website = df_leads_for_deletion['website'].notna().sum()

                print(f"\n--- Rezultati obrade ---")
                print(f"Početni broj leadova: {initial_total_rows}")
                print(f"Broj označenih za brisanje: {len(rows_to_delete)}")
                print(f"Konačni broj leadova u fajlu: {final_total_rows}")
                print(f"Leadova sa emailom: {leads_with_email}")
                print(f"Leadova sa webstranicom: {leads_with_website}")
                print(f"Leadova bez emaila (pain_point 'No Email'): {df_leads_for_deletion[df_leads_for_deletion['pain_point'] == 'No Email'].shape[0]}")
                print(f"Leadova bez webstranice (pain_point 'No Website'): {df_leads_for_deletion[df_leads_for_deletion['pain_point'] == 'No Website'].shape[0]}")
                print(f"Leadova bez emaila i webstranice (pain_point 'No Email, No Website'): {df_leads_for_deletion[df_leads_for_deletion['pain_point'] == 'No Email, No Website'].shape[0]}")

            rows_to_delete.clear()
            polje_email.value = ''
            polje_email.disabled = True
            gumb_spremi.disabled = True
            gumb_preskoci.disabled = True
            gumb_izbrisi.disabled = True

        # Povezivanje dugmadi
        gumb_spremi.on_click(on_save_email_click)
        gumb_preskoci.on_click(on_skip_click)
        gumb_izbrisi.on_click(on_delete_click)
        gumb_kraj.on_click(on_finish_click)

        # Prikaz
        display(output_area, info_label)
        display(widgets.HBox([polje_email]))
        display(widgets.HBox([gumb_spremi, gumb_preskoci, gumb_izbrisi, gumb_kraj]))

        display_next_entry()

    except pd.errors.EmptyDataError:
        print(f"❌ Fajl '{input_file}' je prazan ili nema zaglavlja.")
    except Exception as e:
        print(f"Došlo je do greške prilikom učitavanja ili obrade fajla: {e}")


        ==========



        import pandas as pd
import numpy as np
import os

print("🚀 Pokrećem izdvajanje leadova bez emaila iz 'Processed_Leads_for_Outreach.csv'...")

input_file = 'Processed_Leads_for_Outreach.csv'
output_file_no_email = 'Leads_Without_Email_For_Outreach.csv'

# Provjera postojanja ulaznog fajla
if not os.path.exists(input_file):
    print(f"❌ Nema fajla '{input_file}'! Molimo provjerite da li je uploadovan.")
elif os.path.getsize(input_file) == 0:
    print(f"❌ Fajl '{input_file}' je prazan! Nema podataka za obradu.")
else:
    try:
        df_processed = pd.read_csv(input_file, dtype=str)

        # Standardize 'email' column to ensure proper filtering
        if 'email' in df_processed.columns:
            df_processed['email'] = df_processed['email'].replace(['', 'nan'], np.nan)
        else:
            print("⚠️ Kolona 'email' nije pronađena u fajlu. Pretpostavljam da svi leadovi nemaju email.")
            df_processed['email'] = np.nan # Treat all as no email if column missing

        # Filter leads that do not have an email
        df_no_email_leads = df_processed[df_processed['email'].isna()].copy()

        if not df_no_email_leads.empty:
            df_no_email_leads.to_csv(output_file_no_email, index=False)
            print(f"✅ Izdvojeno {len(df_no_email_leads)} leadova bez emaila i sačuvano u '{output_file_no_email}'.")
        else:
            print("😔 Nema pronađenih leadova bez emaila za izdvajanje.")
            # Ensure the file is created even if empty
            pd.DataFrame(columns=df_processed.columns).to_csv(output_file_no_email, index=False)

        print("🏁 Proces izdvajanja završen.")

    except pd.errors.EmptyDataError:
        print(f"❌ Fajl '{input_file}' je prazan ili nema zaglavlja.")
    except Exception as e:
        print(f"Došlo je do greške prilikom čitanja ili pisanja fajla: {e}")


====

import pandas as pd
import numpy as np
import os

print("🚀 Pokrećem izdvajanje leadova SA emailom iz 'Processed_Leads_for_Outreach.csv'...")

input_file = 'Processed_Leads_for_Outreach.csv'
output_file_with_email = 'Leads_With_Email_For_Outreach.csv'

# Provjera postojanja ulaznog fajla
if not os.path.exists(input_file):
    print(f"❌ Nema fajla '{input_file}'! Molimo provjerite da li je uploadovan.")
elif os.path.getsize(input_file) == 0:
    print(f"❌ Fajl '{input_file}' je prazan! Nema podataka za obradu.")
else:
    try:
        df_processed = pd.read_csv(input_file, dtype=str)

        # Standardize 'email' column to ensure proper filtering
        if 'email' in df_processed.columns:
            df_processed['email'] = df_processed['email'].replace(['', 'nan'], np.nan)
        else:
            print("⚠️ Kolona 'email' nije pronađena u fajlu. Nema leadova sa emailom za izdvajanje.")
            df_processed['email'] = np.nan # Ensure it's NaN if column missing

        # Filter leads that DO have an email
        df_with_email_leads = df_processed[df_processed['email'].notna()].copy()

        if not df_with_email_leads.empty:
            df_with_email_leads.to_csv(output_file_with_email, index=False)
            print(f"✅ Izdvojeno {len(df_with_email_leads)} leadova SA emailom i sačuvano u '{output_file_with_email}'.")
        else:
            print("😔 Nema pronađenih leadova SA emailom za izdvajanje.")
            # Ensure the file is created even if empty
            pd.DataFrame(columns=df_processed.columns).to_csv(output_file_with_email, index=False)

        print("🏁 Proces izdvajanja završen.")

    except pd.errors.EmptyDataError:
        print(f"❌ Fajl '{input_file}' je prazan ili nema zaglavlja.")
    except Exception as e:
        print(f"Došlo je do greške prilikom čitanja ili pisanja fajla: {e}")


==========

import pandas as pd
import numpy as np
import os

input_file = '/content/Biznis Klima uređaji Hrvatska.csv'
output_file = 'Facebook_Links.csv'

print(f"🚀 Loading '{input_file}' to extract Facebook links...")

if not os.path.exists(input_file):
    print(f"❌ Error: The file '{input_file}' does not exist. Please check the path.")
else:
    try:
        df = pd.read_csv(input_file, dtype=str)
        print(f"✅ Successfully loaded {len(df)} leads from '{input_file}'.")

        # Ensure 'Facebook' column exists and standardize it
        if 'Facebook' not in df.columns:
            print("⚠️ 'Facebook' column not found in the DataFrame. Creating an empty column.")
            df['Facebook'] = np.nan
        else:
            df['Facebook'] = df['Facebook'].astype(str)
            df['Facebook'] = df['Facebook'].replace(['', 'nan', 'no social found'], np.nan)

        # Filter for rows that have a valid Facebook link
        valid_facebook_links = df[df['Facebook'].notna()]['Facebook']

        if not valid_facebook_links.empty:
            # Convert the Series to a DataFrame and save it
            df_facebook_links = pd.DataFrame(valid_facebook_links.unique(), columns=['Facebook Link'])
            df_facebook_links.to_csv(output_file, index=False)
            print(f"✅ Successfully extracted and saved {len(df_facebook_links)} unique Facebook links to '{output_file}'.")
            print("\n--- First 5 extracted Facebook links ---")
            display(df_facebook_links.head())
        else:
            print("😔 No valid Facebook links found to extract.")
            # Create an empty file if no links are found
            pd.DataFrame(columns=['Facebook Link']).to_csv(output_file, index=False)
            print(f"⚠️ Empty '{output_file}' created as no Facebook links were found.")

    except pd.errors.EmptyDataError:
        print(f"❌ Error: The file '{input_file}' is empty or has no columns.")
    except Exception as e:
        print(f"❌ An unexpected error occurred: {e}")
