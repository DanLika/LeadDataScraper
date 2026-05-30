import pandas as pd
import numpy as np
import os
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


# CSV / formula injection guard. Lead names, company names, pain_points,
# email_hook etc. come from CSV uploads + Google-Maps scrapes — both
# attacker-controllable. Spreadsheet apps execute cells starting with
# `=`, `@`, `+`, `-`, `\t`, or `\r` as formulas the moment the operator
# opens the export. Prefix the cell with an apostrophe so the value is
# rendered as literal text.
_CSV_FORMULA_PREFIXES = ("=", "@", "+", "-", "\t", "\r")


def sanitize_csv_cell(value):
    if isinstance(value, str) and value and value[0] in _CSV_FORMULA_PREFIXES:
        return "'" + value
    return value


def sanitize_dataframe_for_csv(df):
    """Return a copy of `df` with every string cell neutralised against
    CSV / formula injection. Cheap on small exports; the dashboard caps
    leads at 200 per fetch and exports are operator-triggered."""
    out = df.copy()
    for col in out.select_dtypes(include=["object"]).columns:
        out[col] = out[col].map(sanitize_csv_cell)
    return out


def merge_and_deduplicate(dataframes):
    """
    Combines multiple DataFrames, ensures unique_key is present, and deduplicates.
    Consolidated from data_manager.py.
    """
    if not dataframes:
        return pd.DataFrame()

    try:
        combined = pd.concat(dataframes, ignore_index=True)
        logger.info("Combined leads total: %d", len(combined))

        if "unique_key" not in combined.columns:
            logger.warning(
                "'unique_key' missing in combined data. Generating fallback."
            )
            name_col = "Name" if "Name" in combined.columns else combined.columns[0]
            web_col = "Website" if "Website" in combined.columns else name_col
            combined["unique_key"] = (
                combined[name_col].fillna("") + "_" + combined[web_col].fillna("")
            )
            combined["unique_key"] = combined.apply(
                lambda row: (
                    row["unique_key"]
                    if str(row["unique_key"]).strip("_") != ""
                    else f"dedup_idx_{row.name}"
                ),
                axis=1,
            )

        final_df = combined.drop_duplicates(
            subset=["unique_key"], keep="first"
        ).reset_index(drop=True)
        logger.info(
            "Deduplication complete. Final unique leads: %d (Removed %d duplicates).",
            len(final_df),
            len(combined) - len(final_df),
        )

        return final_df
    except Exception as e:
        logger.error("Error during merge and deduplication: %s", e, exc_info=True)
        return pd.DataFrame()


def _read_or_initialize_csv(filepath, df_name, essential_cols):
    try:
        df = pd.read_csv(filepath, dtype=str)
        logger.info(
            "Successfully loaded %d leads from '%s' for %s.", len(df), filepath, df_name
        )
    except FileNotFoundError:
        logger.warning(
            "'%s' not found. Initializing empty DataFrame for %s.", filepath, df_name
        )
        df = pd.DataFrame(columns=essential_cols, dtype=str)
    except pd.errors.EmptyDataError as e:
        logger.warning(
            "'%s' is empty for %s: %s. Initializing empty DataFrame.",
            filepath,
            df_name,
            e,
        )
        df = pd.DataFrame(columns=essential_cols, dtype=str)
    except pd.errors.ParserError as e:
        # One bad row (typical: unquoted comma inside a value, formula
        # injection payload, extra delimiter) used to crash the whole
        # import — pandas' default `error_bad_lines=True` aborts on the
        # first malformed line and we'd fall back to an empty frame,
        # losing every preceding valid row. See BUGS.md Round 4 B.
        # Retry skipping bad lines so the good rows survive; only fall
        # back to empty if even the lenient parse fails.
        logger.warning(
            "Initial parse of '%s' failed (%s); retrying with on_bad_lines='skip'.",
            filepath,
            e,
        )
        try:
            df = pd.read_csv(filepath, dtype=str, on_bad_lines="skip")
            logger.info(
                "Recovered %d leads from '%s' (some malformed rows skipped).",
                len(df),
                filepath,
            )
        except Exception as e2:
            logger.error(
                "Recovery parse also failed for '%s' (%s). Initializing empty DataFrame.",
                filepath,
                e2,
            )
            df = pd.DataFrame(columns=essential_cols, dtype=str)
    return df


def _normalize_dataframe_columns(df):
    canonical_map = {
        "name": "Name",
        "website": "Website",
        "extracted_email": "email",
        "e-mail": "email",
        "email_address": "email",
        "unique_key": "unique_key",
        "unique_key_colab": "UNIQUE_KEY",
        "company": "company_name",
        "company_name": "company_name",
        "business_name": "company_name",
        "rating": "Rating",
        "reviews": "Reviews",
        "score": "Rating",
        "review_count": "Reviews",
    }

    current_cols = {c.lower(): c for c in df.columns}

    for lower_name, canonical in canonical_map.items():
        if lower_name in current_cols:
            actual_col = current_cols[lower_name]
            if actual_col != canonical:
                # Rename the source → canonical so the source column is gone.
                # The previous implementation did `df[canonical] = df[actual_col]`
                # which COPIES rather than renames — leaving the source column
                # in place. Once backend.main._load_and_standardize_csv lowercases
                # the columns afterwards, `name` and `Name` collapse to the same
                # name, producing duplicate columns. pandas to_dict('records')
                # then silently drops one of them (input-dependent which value
                # survives) and the upload is data-lossy without any warning.
                if canonical not in df.columns:
                    df.rename(columns={actual_col: canonical}, inplace=True)
                else:
                    # Both exist; fill nulls from source then drop it.
                    df[canonical] = df[canonical].fillna(df[actual_col])
                    df.drop(columns=[actual_col], inplace=True, errors="ignore")
    return df


def _ensure_unique_key(df, df_name):
    # Sync unique_key and UNIQUE_KEY
    if "UNIQUE_KEY" in df.columns and (
        "unique_key" not in df.columns or df["unique_key"].isnull().all()
    ):
        df["unique_key"] = df["UNIQUE_KEY"]

    if (
        "unique_key" not in df.columns
        or df["unique_key"].isnull().all()
        or (df["unique_key"] == "").all()
    ):
        logger.info("Generating 'unique_key' for %s...", df_name)

        # Helper to get value from canonical or fallback
        def get_val(row, col):
            val = row.get(col)
            return str(val) if pd.notna(val) and str(val).strip() != "" else ""

        def generate_row_key(row):
            w = get_val(row, "Website")
            e = get_val(row, "email")
            n = get_val(row, "Name")

            if w and e:
                return f"{w}_{e}"
            if w:
                return w
            if n:
                return n
            return f"idx_{row.name}"

        df["unique_key"] = df.apply(generate_row_key, axis=1)
        logger.info("Finished generating 'unique_key' for %s.", df_name)
    return df


def load_csv_with_unique_key(filepath, df_name="CSV"):
    """
    Loads a CSV file, initializes if not found/empty, and ensures a 'unique_key' column exists.
    Also ensures default essential columns are present if initializing.
    """
    essential_cols = ["Name", "Website", "email", "unique_key"]

    df = _read_or_initialize_csv(filepath, df_name, essential_cols)
    df = _normalize_dataframe_columns(df)

    # Ensure all essential columns exist
    for col in essential_cols:
        if col not in df.columns:
            df[col] = np.nan

    df = _ensure_unique_key(df, df_name)

    return df


def save_csv(df, filepath):
    """Saves DataFrame to CSV and ensures the directory exists.
    Sanitises every string cell against CSV / formula injection before
    writing — see `sanitize_dataframe_for_csv`."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True) if os.path.dirname(
        filepath
    ) else None
    sanitize_dataframe_for_csv(df).to_csv(filepath, index=False)
    logger.info("Data saved to '%s'.", filepath)


def export_outreach_ready_csv(df, output_path):
    """
    Formats and exports the lead data specifically for outreach platforms (Instantly, Apollo, etc.)
    Layout: email, website, category, first_name, location, pain_point
    """
    # Mapping table (source_column: target_column)
    mapping = {
        "email": "email",
        "Website": "website",
        "website": "website",
        "segment": "category",
        "first_name": "first_name",
        "location": "location",
        "Address": "location",
        "address": "location",
    }

    # Identify available columns
    available_cols = {}
    for src, target in mapping.items():
        if src in df.columns and target not in available_cols:
            available_cols[target] = src

    # Process pain points to a single descriptive string
    pain_point_col = None
    if "pain_points" in df.columns:
        pain_point_col = "pain_points"
    elif "PAIN_POINTS" in df.columns:
        pain_point_col = "PAIN_POINTS"

    # Create the outreach export dataframe
    export_df = pd.DataFrame()

    for target, src in available_cols.items():
        export_df[target] = df[src]

    if pain_point_col:
        # Convert list/dict to string if necessary
        export_df["pain_point"] = df[pain_point_col].apply(
            lambda x: (
                ", ".join(x) if isinstance(x, list) else str(x) if pd.notna(x) else ""
            )
        )
    else:
        export_df["pain_point"] = ""

    # Ensure all required columns exist even if empty
    required_cols = [
        "email",
        "website",
        "category",
        "first_name",
        "location",
        "pain_point",
    ]
    for col in required_cols:
        if col not in export_df.columns:
            export_df[col] = ""

    # Reorder columns
    export_df = export_df[required_cols]

    # Drop rows without email as they are not outreach-ready
    export_df = export_df[export_df["email"].notna() & (export_df["email"] != "")]

    save_csv(export_df, output_path)
    logger.info(
        "Outreach ready export created with %d leads: %s", len(export_df), output_path
    )
    return export_df


def export_facebook_links(df: pd.DataFrame, output_path: str):
    """
    Extracts unique Facebook links from the DataFrame and saves them to a CSV.
    Matches the logic in the user's Colab script.
    """
    if "facebook" not in df.columns:
        logger.warning("'facebook' column not found. Creating empty export.")
        df_fb = pd.DataFrame(columns=["Facebook Link"])
    else:
        # Standardize and filter
        fb_series = (
            df["facebook"]
            .astype(str)
            .replace(["", "nan", "no social found", "None"], np.nan)
        )
        valid_links = fb_series[fb_series.notna()].unique()
        df_fb = pd.DataFrame(valid_links, columns=["Facebook Link"])

    save_csv(df_fb, output_path)
    logger.info("Extracted %d unique Facebook links to '%s'.", len(df_fb), output_path)
    return df_fb
