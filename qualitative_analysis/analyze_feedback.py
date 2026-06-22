import pandas as pd
import re
import json

def is_meaningful(text):
    if pd.isna(text) or not isinstance(text, str):
        return False
    
    text = text.lower().strip()
    
    # List of generic/meaningless responses to filter out
    meaningless_words = {
        'ok', 'good', 'fine', 'nice', 'none', 'na', 'no comments', 'nil', 
        'super', 'excellent', 'satisfied', 'very good', 'yes', 'no', 'happy', 
        'very satisfied', 'ia m satisfied', 'ia m very satisfied', 'satisfied',
        'very', 'satisfied.', 'good.', 'very good.', 'nice.', 'ok.', 'fine.',
        'thank you', 'thanks', 'supper', 'good service', 'best', 'well',
        'good education', 'very happy', 'good school', 'iam satisified',
        'satisfaction', 'very well', 'all good', 'perfect', 'satisfied'
    }
    
    if text in meaningless_words:
        return False
    
    # Remove emojis and punctuation to check if anything is left
    clean_text = re.sub(r'[^\w\s]', '', text).strip()
    if not clean_text or len(clean_text) < 3:
        # Check if it's just numbers (ratings)
        if clean_text.isdigit():
            return False
        return False
        
    return True

def extract_theme(text, category):
    text = str(text).lower()
    
    themes = {
        'DRINKING_WATER_RATING': [
            (r'dirty|unclean|smell|bore|yellow|black', 'Dirty/Unclean Water'),
            (r'shortage|no water|access|available|available', 'Water Shortage'),
            (r'purifier|filter|ro|not working', 'Purifier/Filter Issues'),
            (r'repair|leakage|broken', 'Maintenance Issues')
        ],
        'DIET_MENU_RATING': [
            (r'quality|taste|bad|not good|worst', 'Poor Food Quality'),
            (r'quantity|less|not enough', 'Insufficient Quantity'),
            (r'menu|variety|daily|same', 'Lack of Menu Variety'),
            (r'insect|worm|clean|hygiene', 'Food Hygiene Issues'),
            (r'rice|dal|curry|egg', 'Specific Item Issues')
        ],
        'CLEANLINESS_RATING': [
            (r'toilet|bathroom|latrine', 'Poor Toilet Hygiene'),
            (r'smell|stink|odor', 'Bad Odor'),
            (r'cleaning|not cleaned|sweeping', 'Lack of Regular Cleaning'),
            (r'water', 'Lack of Water for Cleaning')
        ],
        'EDUCATION_QUALITY_RATING': [
            (r'teacher|teaching|method', 'Teaching Quality Concerns'),
            (r'english|speaking|language', 'English Speaking Issues'),
            (r'lab|library|computer', 'Lack of Academic Facilities'),
            (r'irregular|absent', 'Teacher Irregularity')
        ],
        'PRINCIPAL_SATISFACTION': [
            (r'leadership|behavior|rude|discipline', 'Leadership/Behavior issues'),
            (r'not available|absent', 'Principal Unavailability'),
            (r'misconduct|money', 'Serious Misconduct')
        ],
        'SAFETY_MEASURES_RATING': [
            (r'guard|security|gate', 'Security Gaps'),
            (r'fence|wall|boundary', 'Infrastructure Safety'),
            (r'night|dark', 'Night Safety')
        ],
        'HEALTHCARE_RATING': [
            (r'nurse|doctor|medicine|medical', 'Lack of Medical Support'),
            (r'sick|illness|hospital', 'Poor Response to Illness'),
            (r'first aid', 'Lack of First Aid')
        ]
    }
    
    if category in themes:
        for pattern, theme in themes[category]:
            if re.search(pattern, text):
                return theme
                
    return "Other Concerns"

def run_analysis():
    print("Loading data...")
    df = pd.read_csv('full_reasons_data.csv')
    
    # Fill missing values
    df['rating_reason'] = df['rating_reason'].fillna('')
    df['translated_message'] = df['translated_message'].fillna('')
    
    # Prefer translated message if available, else rating_reason
    df['comment'] = df['translated_message'].where(df['translated_message'] != '', df['rating_reason'])
    
    print("Filtering meaningful comments...")
    df['is_meaningful'] = df['comment'].apply(is_meaningful)
    meaningful_df = df[df['is_meaningful']].copy()
    
    print(f"Found {len(meaningful_df)} meaningful responses out of {len(df)} total rows.")
    
    # Focus on complaints (Rating 1, 2, 3)
    complaints_df = meaningful_df[meaningful_df['rating'].isin([1, 2, 3])].copy()
    print(f"Processing {len(complaints_df)} complaints (Rating 1-3).")
    
    # Identify themes
    complaints_df['theme'] = complaints_df.apply(lambda row: extract_theme(row['comment'], row['category']), axis=1)
    
    # 1. Issues or Complaints Across Category, School, District
    complaint_stats = complaints_df.groupby(['district', 'school_name', 'category', 'theme']).size().reset_index(name='count')
    complaint_stats = complaint_stats.sort_values(by='count', ascending=False)
    
    # Assign severity
    def assign_severity(count):
        if count >= 10: return 'High'
        if count >= 5: return 'Medium'
        return 'Low'
    
    complaint_stats['severity'] = complaint_stats['count'].apply(assign_severity)
    
    # 2. Top Recurring Issues
    recurring_issues = complaints_df.groupby(['theme', 'category']).agg({
        'district': 'nunique',
        'school_name': 'nunique',
        'id': 'count',
        'comment': lambda x: x.iloc[0] if not x.empty else ""
    }).reset_index()
    recurring_issues.columns = ['theme', 'category', 'districts_impacted', 'schools_impacted', 'frequency', 'sample_summary']
    recurring_issues = recurring_issues.sort_values(by='frequency', ascending=False)
    
    # 3. Red-flagged Schools
    red_flag_schools = complaints_df.groupby(['district', 'school_name']).agg({
        'id': 'count',
        'category': 'nunique',
        'rating': 'mean'
    }).reset_index()
    red_flag_schools.columns = ['district', 'school_name', 'complaint_count', 'categories_impacted', 'avg_rating']
    red_flag_schools = red_flag_schools.sort_values(by='complaint_count', ascending=False)
    
    # 4. Specific Red Flags (Principals, Caretakers, Water)
    principals = complaints_df[complaints_df['category'] == 'PRINCIPAL_SATISFACTION'].groupby(['district', 'school_name']).agg({
        'id': 'count',
        'theme': lambda x: ', '.join(x.unique())
    }).reset_index()
    
    caretakers = complaints_df[complaints_df['category'] == 'CARETAKER_SATISFACTION'].groupby(['district', 'school_name']).agg({
        'id': 'count',
        'theme': lambda x: ', '.join(x.unique())
    }).reset_index()
    
    water = complaints_df[complaints_df['category'] == 'DRINKING_WATER_RATING'].groupby(['district', 'school_name']).agg({
        'id': 'count',
        'theme': lambda x: ', '.join(x.unique())
    }).reset_index()
    
    # Save results
    results = {
        'complaint_stats': complaint_stats.head(50).to_dict('records'),
        'recurring_issues': recurring_issues.head(20).to_dict('records'),
        'red_flag_schools': red_flag_schools.head(20).to_dict('records'),
        'red_flag_principals': principals.head(20).to_dict('records'),
        'red_flag_caretakers': caretakers.head(20).to_dict('records'),
        'red_flag_water': water.head(20).to_dict('records'),
        'actionable_comments': meaningful_df[['district', 'school_name', 'category', 'rating', 'comment']].head(100).to_dict('records')
    }
    
    with open('analysis_summary.json', 'w') as f:
        json.dump(results, f, indent=4)
        
    print("Analysis complete. Results saved to analysis_summary.json")

if __name__ == "__main__":
    run_analysis()
