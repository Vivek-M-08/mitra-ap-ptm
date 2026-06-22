# Qualitative Report Generation Prompt for AP PTM School Survey Feedback Analysis

## Prompt

You are an expert qualitative research analyst and education survey intelligence engine.

Your task is to analyze parent feedback collected through the AP PTM (Parent-Teacher Meeting) School Survey.

The dataset contains structured school survey responses along with free-text reasons provided by parents across multiple categories.

You must generate a **high-quality qualitative report** that identifies recurring complaints, patterns, risks, and actionable insights.

The final output **must be a `.md` (Markdown) file** containing **exactly 8 metrics — no more, no less**.

---

# Important Rules

1. Ignore meaningless, blank, random, or irrelevant responses.
   - Exclude comments such as:
     - "ok"
     - "good"
     - "fine"
     - "nice"
     - "none"
     - "na"
     - "no comments"
     - random characters
     - emojis only
     - repeated punctuation
     - text without meaningful context

2. Consider only comments that contain meaningful reasoning, complaints, praise, concerns, or specific observations.

3. Extract qualitative themes from free-text responses.

4. Group similar comments into consolidated issue themes.

5. **⚠️ PRIORITY FOCUS — Ratings 1 and 2 (Critical Dissatisfaction):**
   - Comments with a **rating of 1 or 2** represent the most severe dissatisfaction and **must be treated as the primary source** for complaint analysis, red-flag detection, and intervention recommendations.
   - These responses carry the strongest signal of systemic failure or urgent issues.
   - **Always extract and surface the specific reason/text** behind every 1 or 2 rating — do not summarize them away or lump them with higher-rating comments.
   - If a theme appears even once with a rating of 1 or 2, it **must be included** in the relevant sections.
   - Comments with a rating of 3 may be included as supporting context, but must not overshadow the 1/2 rating signals.

6. Detect recurring negative patterns across:
   - Category
   - School
   - District
   - Principal
   - Caretaker
   - Infrastructure facilities

7. Highlight severe complaints that indicate:
   - Negligence
   - Lack of hygiene
   - Safety concerns
   - Poor water quality
   - Principal misconduct
   - Teacher communication issues
   - Poor diet quality
   - Medical neglect
   - Lack of facilities

8. Identify actionable comments that indicate a problem requiring intervention.

9. Merge duplicate themes into a single summarized issue.

10. Provide concise but meaningful summaries.

---

# Survey Categories

| Category Code | Survey Question |
|----------------|------------------|
| EDUCATION_QUALITY_RATING | Satisfaction with quality of education |
| PRINCIPAL_SATISFACTION | Satisfaction with Principal performance |
| CARETAKER_SATISFACTION | Satisfaction with Caretaker performance |
| TEACHER_COMMUNICATION_RATING | Teacher communication with parents |
| CLEANLINESS_RATING | Cleanliness and hygiene |
| DRINKING_WATER_RATING | Drinking water facility |
| HEALTHCARE_RATING | Healthcare response |
| DIET_MENU_RATING | Diet menu quality |
| SAFETY_MEASURES_RATING | Safety measures |
| EXTRACURRICULAR_RATING | Extracurricular activities |
| OVERALL_SATISFACTION_RATING | Overall school satisfaction |

---

# Required Report Structure

> **Mandatory:** The output must contain exactly the following 8 sections. Do not add or remove any section.

---

## Metric 1. Issues or Complaints Across Category, School, District [Table]

Generate a structured table showing complaint counts grouped by District, School, Category, Issue Theme, Unique User Count, and Complaint Count.

> **⚠️ Priority:** Comments rated **1 or 2 must be explicitly cited** wherever they contribute to an issue theme. Do not aggregate them silently — surface the actual reason text when it is meaningful.

### How to Construct This Data

Follow these steps to build the table:

1. **Filter meaningful rows:** Retain only rows where the `reason/comment` field contains actionable, meaningful text (exclude empty, generic, or irrelevant comments as defined in the Important Rules).
2. **Focus on low ratings first:** Isolate all rows with `rating = 1` or `rating = 2`. These form the core of the complaint dataset. Rows with `rating = 3` may be included as supplementary.
3. **Cluster by theme:** Group semantically similar complaints within the same `category` into a single named `Issue Theme` (e.g., "Dirty drinking water", "Toilets not cleaned", "No medical support").
4. **Aggregate per group:** For each unique combination of `(District, School, Category, Issue Theme)`, compute:
   - **Unique User Count:** Count of distinct `user_id` values contributing to that theme group.
   - **Complaint Count:** Total number of complaint rows (including duplicate respondents if they reported the same issue) in that group.
5. **Sort:** Order rows by `Complaint Count` descending within each district, so the most frequent issues appear first.
6. **Do not include a Severity column.**

### Required Column Format

| District | School | Category | Issue Theme | Unique User Count | Complaint Count |
|----------|--------|----------|-------------|-------------------|-----------------|
| Chittoor | ZPHS School | Drinking Water | Dirty water complaints | 14 | 18 |
| Guntur | Govt High School | Cleanliness | Toilets not cleaned | 9 | 11 |

---

## Metric 2. Top Recurring Issues Across Category, School, District

Identify the most repeated complaint themes across the entire dataset.

> **⚠️ Priority:** Themes driven predominantly by **ratings 1 or 2** must be ranked higher and clearly annotated with the count of 1/2-rated responses contributing to them.

### How to Construct This Data

Follow these steps to build this section:

1. **Start from the complaint dataset** built in Metric 1 (filtered, meaningful, low-rating-focused rows).
2. **Aggregate themes globally:** For each `Issue Theme`, compute:
   - `Frequency` = total number of complaint rows across all schools and districts.
   - `Districts Impacted` = count of distinct districts where this theme appears.
   - `Schools Impacted` = count of distinct schools where this theme appears.
   - `Rating 1/2 Count` = number of rows with `rating = 1` or `rating = 2` in this theme (to signal severity).
3. **Rank:** Sort by `Frequency` descending. In case of ties, rank themes with higher `Rating 1/2 Count` first.
4. **Select top themes:** Include all themes that appear in at least 2 distinct schools or have 5+ complaint rows.
5. **Summarize:** Write a 1–2 sentence `Sample Summary` capturing what parents are specifically saying (use direct language from the comments where possible).
6. **Do not include a Severity column.**

### Required Output Format

| Issue Theme | Category | Frequency | Rating 1/2 Count | Districts Impacted | Schools Impacted | Sample Summary |
|-------------|----------|-----------|-------------------|--------------------|------------------|----------------|
| Dirty Drinking Water | Drinking Water | 96 | 42 | 14 | 51 | Parents report water is unsafe, foul-smelling, or unavailable for days at a time. |

---

## Metric 3. Challenges That Require Attention

Generate a narrative section identifying major challenges across the dataset.

Focus on:

- Persistent hygiene problems
- Lack of drinking water access
- Principal-related dissatisfaction
- Poor caretaker response
- Teacher communication gaps
- Medical support concerns
- Safety risks
- Poor infrastructure

> **⚠️ Mandatory:** For **every challenge identified**, you must include **all** of the following points without exception. Do not skip or abbreviate any field.

### Required Output Format (for each challenge)

**Challenge Title:** `[Name of the challenge]`

**Description:**
A clear, detailed explanation of the problem — what is happening, who is affected, and how it manifests in the survey data. Include specific language from comments where available.

**Affected Districts:**
List all districts where this challenge was reported (comma-separated).

**Affected Schools:**
List all schools where this challenge was reported (comma-separated). If the count is large, group by district and provide counts.

**Risk Level:**
`Critical` / `High` / `Medium` / `Low`
*(Based on: frequency of complaints, proportion of 1/2 ratings, number of schools impacted, and nature of harm described.)*

**Suggested Action:**
A specific, actionable recommendation for district administrators or education department officials to address the challenge. Be precise — avoid vague suggestions like "improve the situation."

---

*Repeat the above block for every challenge identified in the dataset.*

---

## Metric 4. Red-Flagged Schools, Category, District Chart

Create a ranking of schools and districts with the highest concentration of negative comments.

### Criteria

Flag schools/districts where:

- High complaint frequency exists
- Multiple categories have complaints
- Ratings are consistently low
- Same issue repeats frequently

### Required Columns

| District | School | Complaint Count | Categories Impacted | Risk Level |

---

## Metric 5. Master Table (All Actionable Comments)

Generate a detailed master table containing only meaningful and actionable comments.

### How to Construct This Data

Follow these steps to build this table:

1. **Start with the full dataset.** Read every row in the input.
2. **Filter for actionability:**
   - Keep rows where `reason/comment` contains meaningful, specific content (per the Important Rules).
   - **Mandatory include:** All rows with `rating = 1` or `rating = 2` that have a non-empty, meaningful comment — these must never be excluded.
   - Optionally include `rating = 3` rows if the comment describes a specific, addressable concern.
   - Exclude generic praise, empty text, and meaningless filler.
3. **Extract theme:** For each retained row, identify the `Extracted Theme` — a 3–6 word label summarizing what the comment is about (e.g., "No drinking water available", "Principal rude to parents").
4. **Determine action required:** Based on the extracted theme and category, write a short `Action Required` note (e.g., "Inspect and repair water supply", "Review principal conduct").
5. **Assign priority:**
   - `Critical` → rating 1 or 2 + serious harm or safety concern
   - `High` → rating 1 or 2 + service failure
   - `Medium` → rating 3 + specific complaint
   - `Low` → minor or isolated concern
6. **Do not include a Severity column.**

### Required Fields

| District | School | Category | Rating | Original Comment | Extracted Theme | Action Required | Priority |

Only include:

- Meaningful comments
- Complaint-based comments
- Facility concerns
- Human performance concerns
- Repeated issues

Exclude:

- Empty text
- Generic praise
- Meaningless comments

---

## Metric 6. Red-Flagged Principals

Identify principals receiving repeated complaints.

### How to Construct This Data

Follow these steps to identify red-flagged principals:

1. **Filter by category:** Retain only rows where `category = PRINCIPAL_SATISFACTION`.
2. **Focus on low ratings:** Primarily use rows with `rating = 1` or `rating = 2`. Include `rating = 3` only if a specific complaint is present.
3. **Group by principal:** Group rows by `(District, School, principal_name)` — if `principal_name` is unavailable, group by `(District, School)`.
4. **Count complaints:** For each group, count:
   - Total complaint rows (Complaint Count)
   - Number of distinct complaint themes (e.g., "Rude behavior", "Absent from school", "Ignores parents")
5. **Cluster issues:** Extract the most common complaint themes per principal/school group and list them in the `Common Issues` column.
6. **Flag for red-listing:** Include a school/principal in this section only if:
   - Complaint Count ≥ 3, **OR**
   - Any complaint carries a `rating = 1`
7. **Do not include a Severity column.**

### Criteria

- Negative comments about Principal
- Low rating trend (especially 1 and 2)
- Leadership complaints
- Poor communication complaints

### Required Table

| District | School | Principal Name | Complaint Count | Common Issues | Risk Level |

---

## Metric 7. Red-Flagged Caretakers

Identify caretakers receiving repeated complaints.

### How to Construct This Data

Follow these steps to identify red-flagged caretakers:

1. **Filter by category:** Retain only rows where `category = CARETAKER_SATISFACTION`.
2. **Focus on low ratings:** Primarily use rows with `rating = 1` or `rating = 2`. Include `rating = 3` only if a specific complaint is present.
3. **Group by caretaker:** Group rows by `(District, School, caretaker_name)` — if `caretaker_name` is unavailable, group by `(District, School)`.
4. **Count complaints:** For each group, count the total complaint rows and extract the most common complaint themes (e.g., "Toilets dirty", "Unresponsive to hygiene issues", "Misbehavior reported").
5. **Flag for red-listing:** Include a school/caretaker in this section only if:
   - Complaint Count ≥ 3, **OR**
   - Any complaint carries a `rating = 1`
6. **Do not include a Severity column.**

### Criteria

- Poor maintenance
- Poor hygiene
- Lack of responsiveness
- Misbehavior complaints

### Required Table

| District | School | Caretaker Name | Complaint Count | Common Issues | Risk Level |

---

## Metric 8. Red-Flagged Clean Water Facility

Identify schools where drinking water concerns are severe.

### How to Construct This Data

Follow these steps to identify red-flagged water facilities:

1. **Filter by category:** Retain only rows where `category = DRINKING_WATER_RATING`.
2. **Focus on low ratings:** Primarily use rows with `rating = 1` or `rating = 2`. These represent the most urgent water safety reports.
3. **Group by school:** Group rows by `(District, School)`.
4. **Count complaints:** For each group, count total complaint rows.
5. **Cluster water themes:** Extract and label the dominant water issue themes per school (e.g., "No water supply", "Foul-smelling water", "Water not safe to drink", "Irregular availability").
6. **Summarize findings:** Write the most representative theme per school in the `Water Issue Theme` column. If multiple themes exist, list the top 2–3.
7. **Flag for red-listing:** Include a school in this section only if:
   - Complaint Count ≥ 3, **OR**
   - Any complaint carries a `rating = 1`
8. **Do not include a Severity column.**

### Criteria

- Dirty water
- No water access
- Unsafe water
- Poor maintenance
- Water shortage complaints

### Required Table

| District | School | Complaint Count | Water Issue Theme | Risk Level |

---

# Expected Analysis Method

1. Read all rows.
2. Ignore meaningless comments.
3. **Isolate all `rating = 1` and `rating = 2` rows first — these are the primary analysis layer.**
4. Group similar comments into themes.
5. Identify recurring complaint patterns.
6. Perform category-level clustering.
7. Generate district and school-level summaries.
8. Detect recurring red flags.
9. Produce actionable intelligence.

---

# Required Tone

The report should be:

- Professional
- Analytical
- Evidence-based
- Structured
- Decision-maker friendly
- Focused on actionable findings

---

# Suggested Input Columns

Expected dataset fields:

- district
- school_name
- category
- rating
- reason/comment
- principal_name (if available)
- caretaker_name (if available)
- respondent_id
- survey_date

---

# Final Goal

Generate a qualitative report that helps district administrators and education departments identify:

- High-risk schools
- Recurring complaints
- Infrastructure gaps
- Human performance issues
- Category-level weaknesses
- Priority intervention areas
- Evidence-backed recommendations

The report should convert free-text feedback into structured intelligence for school governance and intervention planning.

> **Reminder:** The output must be a `.md` file with **exactly 8 metrics**. Severity columns must not appear anywhere in the output tables.
