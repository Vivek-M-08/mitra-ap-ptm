import pandas as pd

# Load the xlsx (school list)
xlsx = pd.read_excel('List of 190 Schools_APSWREIS_21 May 2026 (1).xlsx')
print("XLSX columns:", xlsx.columns.tolist())

# Load the query CSV
csv = pd.read_csv('query_result_2026-06-02T12_58_43.498140365Z.csv')
print("CSV columns:", csv.columns.tolist())

# Normalize school codes to strings (strip whitespace, remove decimals if any)
xlsx['DISE Code'] = xlsx['DISE Code'].astype(str).str.strip().str.split('.').str[0]
csv['school_code'] = csv['school_code'].astype(str).str.strip().str.split('.').str[0]

xlsx_codes = set(xlsx['DISE Code'])
csv_codes = set(csv['school_code'])

# Schools in xlsx but missing from query CSV
missing_in_csv = xlsx_codes - csv_codes

print(f"\nTotal schools in XLSX (school list): {len(xlsx_codes)}")
print(f"Total schools in query CSV:          {len(csv_codes)}")
print(f"Schools missing from query CSV:      {len(missing_in_csv)}")

# Show the missing school details
missing_rows = xlsx[xlsx['DISE Code'].isin(missing_in_csv)][['S.No', 'DISTRICT', 'Dr. B. R. Ambedkar Gurukulam ', 'DISE Code']]
missing_rows = missing_rows.sort_values('DISTRICT')

print("\nMissing schools detail:")
print(missing_rows.to_string(index=False))

# Save to CSV for reference
missing_rows.to_csv('missing_schools.csv', index=False)
print("\nSaved missing schools to missing_schools.csv")
