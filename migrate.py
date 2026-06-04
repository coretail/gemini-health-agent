import os
import json
import csv
import httpx
from dotenv import load_dotenv
import pandas as pd

load_dotenv()

# 1. Tarik Amunisi Kredensial
SUPABASE_URL = os.getenv("SUPABASE_URL")
# Pake SERVICE_ROLE/SECRET_KEY biar dapet akses bypass admin pas migrasi
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE") or os.getenv("SUPABASE_KEY") 

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ Error: SUPABASE_URL atau KEY belum ada di .env, cuy!")
    exit()

headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

client = httpx.Client(headers=headers)

DB_FOLDER = "database"
WORKOUT_CSV = os.path.join(DB_FOLDER, "workout_history.csv")
NUTRITION_CSV = os.path.join(DB_FOLDER, "nutrition_history.csv")
PROFILE_JSON = os.path.join(DB_FOLDER, "user_profile.json")

def parse_nutrition_mentah(text_mentah):
    text = str(text_mentah).strip()
    kalori, protein, karbo, lemak, keterangan = 0, 0, 0, 0, "-"
    parts = text.split(' | ')
    for part in parts:
        part_clean = part.strip().replace('**', '')
        try:
            if "Kalori:" in part_clean:
                kalori = float(part_clean.split("Kalori:")[1].replace("kkal", "").strip())
            elif "Protein:" in part_clean:
                protein = float(part_clean.split("Protein:")[1].replace("gram", "").replace("g", "").strip())
            elif "Karbohidrat:" in part_clean:
                karbo = float(part_clean.split("Karbohidrat:")[1].replace("gram", "").replace("g", "").strip())
            elif "Lemak:" in part_clean:
                lemak = float(part_clean.split("Lemak:")[1].replace("gram", "").replace("g", "").strip())
            elif "Keterangan:" in part_clean:
                keterangan = part_clean.split("Keterangan:")[1].strip()
        except:
            pass
    return {"kalori": kalori, "protein": protein, "karbo": karbo, "lemak": lemak, "keterangan": keterangan}

# === 1. MIGRASI PROFIL ===
print("👤 Memulai migrasi data profil...")
if os.path.exists(PROFILE_JSON):
    with open(PROFILE_JSON, "r", encoding="utf-8") as f:
        p = json.load(f)
    
    # Bersihkan data kosong agar tipenya cocok dengan numeric/date di SQL
    payload_profile = {
        "nama": p.get("nama", "User"),
        "tempat_lahir": p.get("tempat_lahir"),
        "tanggal_lahir": p.get("tanggal_lahir") or None,
        "tinggi_badan": float(p["tinggi_badan"]) if p.get("tinggi_badan") else None,
        "berat_badan": float(p["berat_badan"]) if p.get("berat_badan") else None,
        "rhr": float(p["rhr"]) if p.get("rhr") else None,
        "max_hr": float(p["max_hr"]) if p.get("max_hr") else 200,
        "target_latihan": p.get("target_latihan"),
        "target_waktu": p.get("target_waktu"),
        "tanggal_race": p.get("tanggal_race") or None,
        "catatan_agent": p.get("catatan_agent")
    }
    res = client.post(f"{SUPABASE_URL}/rest/v1/profiles", json=payload_profile)
    if res.status_code in [200, 201]:
        print("✅ Profil berhasil di-upload ke Supabase!")
    else:
        print(f"⚠️ Gagal upload profil: {res.text}")

# === 2. MIGRASI WORKOUTS ===
print("\n🏃‍♂️ Memulai migrasi histori workout...")
if os.path.exists(WORKOUT_CSV):
    df_w = pd.read_csv(WORKOUT_CSV)
    records_w = []
    for _, row in df_w.iterrows():
        # Validasi HR & Pace agar gak bikin crash database
        hr = row.get('Avg HR (BPM)')
        hr_clean = float(hr) if pd.notna(hr) and str(hr).isdigit() else None
        
        records_w.append({
            "tanggal": row.get('Tanggal'),
            "jenis_olahraga": row.get('Jenis Olahraga'),
            "durasi_menit": float(row.get('Durasi (Menit)', 0)),
            "avg_hr": hr_clean,
            "avg_pace": str(row.get('Avg Pace (min/km)', '')) if pd.notna(row.get('Avg Pace (min/km)')) else None
        })
    
    if records_w:
        res = client.post(f"{SUPABASE_URL}/rest/v1/workouts", json=records_w)
        if res.status_code in [200, 201]:
            print(f"✅ Sukses migrasi {len(records_w)} data workout ke Supabase!")
        else:
            print(f"⚠️ Gagal upload workout: {res.text}")

# === 3. MIGRASI NUTRITION ===
print("\n🍳 Memulai migrasi histori nutrisi...")
if os.path.exists(NUTRITION_CSV):
    df_n = pd.read_csv(NUTRITION_CSV)
    records_n = []
    
    kolom_analisis = 'Hasil Analisis Mentah' if 'Hasil Analisis Mentah' in df_n.columns else df_n.columns[-1]
    kolom_catatan = 'Catatan User' if 'Catatan User' in df_n.columns else df_n.columns[1]
    kolom_tanggal = 'Tanggal' if 'Tanggal' in df_n.columns else df_n.columns[0]
    
    for _, row in df_n.iterrows():
        parsed = parse_nutrition_mentah(str(row[kolom_analisis]))
        records_n.append({
            "tanggal": row[kolom_tanggal],
            "catatan_user": row[kolom_catatan],
            "kalori": parsed["kalori"],
            "protein": parsed["protein"],
            "karbo": parsed["karbo"],
            "lemak": parsed["lemak"],
            "keterangan": parsed["keterangan"]
        })
        
    if records_n:
        res = client.post(f"{SUPABASE_URL}/rest/v1/nutrition", json=records_n)
        if res.status_code in [200, 201]:
            print(f"✅ Sukses migrasi {len(records_n)} data makanan ke Supabase!")
        else:
            print(f"⚠️ Gagal upload nutrisi: {res.text}")

print("\n🎉 Proses migrasi selesai!")