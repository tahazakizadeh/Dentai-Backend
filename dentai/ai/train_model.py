import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import LabelEncoder
import pickle

df = pd.read_json('combined_appointments.json', encoding='utf-8')

def time_to_minutes(t):
    try:
        if pd.isna(t) or not isinstance(t, str) or ':' not in t:
            return None
        h, m = map(int, t.split(':'))
        return h * 60 + m
    except Exception as e:
        print(f"Invalid time format: {t!r} -> {e}")
        return None

# یکسان‌سازی املای "نامرئی" برای جلوگیری از تفاوت‌ها (بدون چک اضافی)
df['نوع درمان'] = df['نوع درمان'].str.replace('نامرئی','نامریی' ).str.strip()

# فیکس دستی و صددرصدی طول درمان به 60 دقیقه برای تمام موارد "شروع ارتودنسی نامرئی" بدون هیچ شرطی
mask = df['نوع درمان'].str.contains('شروع ارتودنسی نامریی', na=False)
df.loc[mask, 'طول درمان (دقیقه)'] = 60

df = df[df['طول درمان (دقیقه)'] > 0].copy()

df['start_minutes'] = df['ساعت شروع'].apply(time_to_minutes)

le = LabelEncoder()
df['treatment_encoded'] = le.fit_transform(df['نوع درمان'])

X = df[['start_minutes', 'treatment_encoded']]
y = df['طول درمان (دقیقه)']

model = RandomForestRegressor(n_estimators=100, random_state=42)
model.fit(X, y)

with open('duration_predictor_model_v2.pkl', 'wb') as f:
    pickle.dump(model, f)

with open('treatment_encoder_v2.pkl', 'wb') as f:
    pickle.dump(le, f)

print("saved")
