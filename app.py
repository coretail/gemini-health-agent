import os
import re
import asyncio
import csv
import json
from fastapi import FastAPI, Request, Form, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime, timedelta
import pandas as pd
import threading
from tele_bot import bot, strava_sync

from dotenv import load_model, load_dotenv

# SDK Gemini
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# IMPORT SELEBRITI BARU UNTUK SCHEDULER
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Import fungsi sakti sinkronisasi dari kodingan bot lu kemarin
# Pastikan nama fungsi penarik data Strava di tele_bot.py lu bisa di-import (atau lu copas logikanya ke sini)
# Di sini gue asumsikan kita buat fungsi wrapper lokal untuk fetch data Strava
import httpx 

app = FastAPI(title="Personal Health Tracker Dashboard")
templates = Jinja2Templates(directory="templates")

# API Key Gemini Lu
GEMINI_API_KEY = "AQ.Ab8RN6ICT6ghrMxfmZMD4JzXMyp8s4vNUmKuFvUfoyKO9fj2vA"
ai_client = genai.Client(api_key=GEMINI_API_KEY)

class NutritionLog(BaseModel):
    kalori: int = Field(description="Estimasi total kalori (kkal)")
    protein: int = Field(description="Estimasi protein (gram)")
    karbo: int = Field(description="Estimasi karbohidrat (gram)")
    lemak: int = Field(description="Estimasi lemak (gram)")
    keterangan: str = Field(description="Analisis singkat maksimal 2 kalimat apakah makanan ini bagus untuk recovery latihan marathon.")

# ==========================================
# SKEMA AI UNTUK COACH LATIHAN & CACHE
# ==========================================
class ZoneItem(BaseModel):
    zone: str = Field(description="Nama zona, misal: 'Zone 2 (Aerobic)'")
    hr: str = Field(description="Rentang Heart Rate, misal: '135 - 148 BPM'")
    pace: str = Field(description="Target Pace, misal: '06:45 - 07:15 /km'")
    desc: str = Field(description="Penjelasan fungsi zona ini spesifik untuk profil dan target user.")
    color: str = Field(description="Warna Tailwind, pilih salah satu: 'text-gray-400', 'text-emerald-400 font-bold', 'text-amber-400', 'text-orange-500', 'text-rose-500'")

class TrainingZonesResult(BaseModel):
    zones: list[ZoneItem]

# Memori sementara biar web lu gak lemot manggil AI tiap di-refresh
ZONES_CACHE = {"tanggal_lari_terakhir": None, "data": []}
EVALUASI_CACHE = {"tanggal_lari_terakhir": None, "evaluasi_hari_ini": "", "evaluasi_minggu_lalu": ""}

DB_FOLDER = "database"
WORKOUT_CSV = os.path.join(DB_FOLDER, "workout_history.csv")
NUTRITION_CSV = os.path.join(DB_FOLDER, "nutrition_history.csv")

PROFILE_JSON = os.path.join(DB_FOLDER, "user_profile.json")

# Fungsi pembantu untuk load data profil dengan nilai default yang super pas buat lu!
def load_user_profile():
    if os.path.exists(PROFILE_JSON):
        with open(PROFILE_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    # Nilai default awal yang disesuaikan otomatis dengan profil lu, tinggal lu edit nanti di web
    default_profile = {
        "nama": "User",
        "tempat_lahir": "Jakarta",
        "tanggal_lahir": "2002-02-21",
        "tinggi_badan": "",  
        "berat_badan": "",
        "rhr": "",
        "max_hr": "200",
        "target_latihan": "Race - Target 4 Jam 30 Menit",
        "target_waktu": "04:30:00",
        "tanggal_race": "",
        "catatan_agent": ""
    }
    # Save default pertama kali
    with open(PROFILE_JSON, "w", encoding="utf-8") as f:
        json.dump(default_profile, f, indent=4)
    return default_profile

# ==========================================
# 🔄 ENGINE BACKGROUND AUTO-SYNC STRAVA
# ==========================================
async def auto_fetch_strava_job():
    """Fungsi yang akan berjalan otomatis di latar belakang untuk narik data Strava"""
    print("🔄 [Scheduler] Memulai pengecekan aktivitas baru ke server Strava...")
    try:
        # Panggil fungsi async yang baru tanpa parameter message
        await strava_sync() 
        print("✅ [Scheduler] Auto-sync Strava sukses dijalankan via background task!")
    except Exception as e:
        print(f"❌ [Scheduler] Gagal auto-sync Strava: {e}")
# Inisialisasi Scheduler bawaan AsyncIO
scheduler = AsyncIOScheduler()

@app.on_event("startup")
async def start_scheduler():
    """Fungsi bawaan FastAPI yang otomatis ke-trigger saat server web dinyalain"""
    # 1. Jalankan scheduler auto-sync Strava tiap 15 menit
    scheduler.add_job(auto_fetch_strava_job, 'interval', minutes=15)
    scheduler.start()
    print("⏰ [Scheduler] Engine Auto-Sync Strava tiap 15 menit telah AKTIF!")

    # 2. JALUR AMAN: Jalankan polling bot di thread terpisah tanpa interupsi
    def start_bot_polling():
        print("==================================================")
        print("🤖 TELEGRAM BOT RUNNING SAFELY IN BACKGROUND THREAD!")
        print("==================================================")
        # Semburkan polling lewat thread mandiri
        bot.infinity_polling(skip_pending=True)

    bot_thread = threading.Thread(target=start_bot_polling, daemon=True)
    bot_thread.start()

@app.on_event("shutdown")
async def stop_scheduler():
    scheduler.shutdown()
    bot.stop_polling()
    print("🛑 [Scheduler] Engine Auto-Sync dimatikan.")

# ==========================================
# PARSER & ROUTING UTAMA (SAMA KAYAK KEMARIN)
# ==========================================
def parse_nutrition_mentah(text_mentah):
    text = str(text_mentah).strip()
    kalori, protein, karbo, lemak, keterangan = "-", "-", "-", "-", "-"
    parts = text.split(' | ')
    for part in parts:
        part_clean = part.strip().replace('**', '')
        if "Kalori:" in part_clean:
            kalori = part_clean.split("Kalori:")[1].replace("kkal", "").strip()
        elif "Protein:" in part_clean:
            protein = part_clean.split("Protein:")[1].replace("gram", "").replace("g", "").strip()
        elif "Karbohidrat:" in part_clean:
            karbo = part_clean.split("Karbohidrat:")[1].replace("gram", "").replace("g", "").strip()
        elif "Lemak:" in part_clean:
            lemak = part_clean.split("Lemak:")[1].replace("gram", "").replace("g", "").strip()
        elif "Keterangan:" in part_clean:
            keterangan = part_clean.split("Keterangan:")[1].strip()
    return {"kalori": kalori, "protein": protein, "karbo": karbo, "lemak": lemak, "keterangan": keterangan}

# ==========================================
# 📝 FITUR CRUD: NUTRISI
# ==========================================
@app.post("/add_nutrition")
async def add_nutrition(
    catatan: str = Form("Tanpa catatan"), # Default kalau ga diisi
    foto: UploadFile = File(None)         # Foto bersifat opsional
):
    """Endpoint untuk Create Data Nutrisi via AI (Terima Foto/Teks)"""
    waktu_makan = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # 1. Siapkan konten untuk Gemini
    contents = []
    
    # Kalau user upload foto, baca bytes-nya
    if foto and foto.filename:
        image_bytes = await foto.read()
        mime_type = foto.content_type or 'image/jpeg'
        contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
        
    # Tambahkan instruksi teks (termasuk porsi dari user)
    # 1. Load Data Profil User
    profile = load_user_profile()
    
    # 2. Rakit System Prompt Super Personal (Update Fase 2)
    prompt_text = f"""
Kamu adalah asisten gizi olahraga pribadi untuk {profile.get('nama', 'User')}.
Berikut adalah profil biometrik dan targetnya:
- Target Latihan: {profile.get('target_latihan', 'Maintain')}
- Target Waktu: {profile.get('target_waktu', '-')}
- Batas Jantung: RHR {profile.get('rhr', '-')} BPM, Max HR {profile.get('max_hr', '-')} BPM
- Biometrik Fisik: Tinggi {profile.get('tinggi_badan', '-')} cm, Berat {profile.get('berat_badan', '-')} kg
- Catatan Khusus & Preferensi: "{profile.get('catatan_agent', 'Tidak ada catatan khusus.')}"

TUGAS KAMU:
Analisis kandungan nutrisi makanan yang difoto/diketik ini. 
Catatan porsi dari user: '{catatan}'.

PENTING: Di bagian "keterangan", sesuaikan analisismu secara spesifik dengan Catatan Khusus, Target Latihan, dan Biometrik Fisiknya. 
(Gunakan BB/TB untuk memperkirakan kebutuhan kalori harian/proteinnya secara implisit). Jika makanan ini tidak sejalan dengan targetnya, beri teguran suportif!
"""
    contents.append(prompt_text)

    try:
        # 2. Panggil AI Gemini
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=NutritionLog,
            )
        )
        
        # 3. Parse JSON dari AI
        data_ai = json.loads(response.text)
        
        # 4. Format jadi string sakti sesuai standar CSV lu biar ga error pas di-parse HTML
        hasil_mentah = (
            f"Kalori: {data_ai.get('kalori', 0)} kkal | "
            f"Protein: {data_ai.get('protein', 0)}g | "
            f"Karbohidrat: {data_ai.get('karbo', 0)}g | "
            f"Lemak: {data_ai.get('lemak', 0)}g | "
            f"Keterangan: {data_ai.get('keterangan', 'Dianalisis oleh AI.')}"
        )
        
        # 5. Save ke CSV
        with open(NUTRITION_CSV, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([waktu_makan, catatan, hasil_mentah])
            
    except Exception as e:
        print(f"❌ Error Gemini API: {e}")
        # Kalau AI gagal, masukin data dummy biar web ga crash
        with open(NUTRITION_CSV, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([waktu_makan, catatan, f"Kalori: 0 kkal | Protein: 0g | Karbohidrat: 0g | Lemak: 0g | Keterangan: Gagal dianalisis AI - {e}"])
        
    return RedirectResponse(url="/", status_code=303)

@app.post("/delete_nutrition")
async def delete_nutrition(tanggal: str = Form(...)):
    """Endpoint untuk Delete Data Nutrisi berdasarkan Tanggal"""
    if os.path.exists(NUTRITION_CSV):
        df = pd.read_csv(NUTRITION_CSV)
        # Filter (buang) baris yang tanggalnya sama dengan yang mau dihapus
        df_bersih = df[df['Tanggal'] != tanggal]
        # Timpa ulang file CSV-nya dengan data yang udah bersih
        df_bersih.to_csv(NUTRITION_CSV, index=False)
        
    return RedirectResponse(url="/", status_code=303)

@app.get("/", response_class=HTMLResponse)
async def read_dashboard(request: Request):
    workout_data = []
    strength_data = []
    graph_data = {"dates": [], "loads": []} # <--- Ganti paces/hrs jadi loads
    
    evaluasi_hari_ini = "Belum ada latihan tercatat hari ini, cuy. Jangan lupa gerak!"
    evaluasi_minggu_lalu = "Minggu lalu performa latihanmu stabil. Pertahankan aerobic base!"

    # === NILAI DEFAULT AMAN (ANTI CRASH) ===
    waktu_terakhir_lari = "Belum ada data"
    acwr_chart_data = {"dates": [], "acwr": []}
    readiness_score = 100
    readiness_msg = "Belum ada data latihan. Baterai full siap tempur!"
    readiness_color = "text-emerald-400"
    zones_data = []
    # =======================================

    if os.path.exists(WORKOUT_CSV):
        try:
            df_workout = pd.read_csv(WORKOUT_CSV)
            if not df_workout.empty:
                # Ambil 7 sesi terakhir untuk grafik Training Load
                df_graph = df_workout.tail(7)
                
                for _, row in df_graph.iterrows():
                    durasi = float(row.get('Durasi (Menit)', 0))
                    hr = float(row.get('Avg HR (BPM)', 0)) if str(row.get('Avg HR (BPM)')).isdigit() else 130
                    
                    # Hitung Skor Training Load (Durasi x HR / 100 biar angkanya ga kegedean di grafik)
                    training_load = round((durasi * hr) / 100, 1)
                    
                    graph_data["dates"].append(str(row.get('Tanggal', '-')).split()[0])
                    graph_data["loads"].append(training_load)

                # Filter data untuk tabel
                df_run = df_workout[df_workout['Jenis Olahraga'].str.lower() == 'run']
                workout_data = df_run.tail(5).to_dict(orient="records")
                
                df_strength = df_workout[df_workout['Jenis Olahraga'].str.lower() != 'run']
                strength_data = df_strength.tail(5).to_dict(orient="records")

                # ==========================================
                # ⚙️ LOGIKA BARU: KALKULASI ACWR (Acute: 7 hari, Chronic: 28 hari)
                # ==========================================
                acwr_chart_data = {"dates": [], "acwr": []}
                
                # 1. Bersihkan dan totalin load harian
                df_acwr = df_workout.copy()
                df_acwr['Date'] = pd.to_datetime(df_acwr['Tanggal']).dt.strftime('%Y-%m-%d')
                df_acwr['HR_Clean'] = pd.to_numeric(df_acwr['Avg HR (BPM)'], errors='coerce').fillna(130)
                df_acwr['Dur_Clean'] = pd.to_numeric(df_acwr['Durasi (Menit)'], errors='coerce').fillna(0)
                df_acwr['Daily_Load'] = (df_acwr['Dur_Clean'] * df_acwr['HR_Clean']) / 100
                
                daily_loads_dict = df_acwr.groupby('Date')['Daily_Load'].sum().to_dict()
                
                # 2. Bikin timeline 35 hari ke belakang tanpa ada hari yang bolong
                today_date = datetime.now()
                dates_35 = [(today_date - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(34, -1, -1)]
                loads_35 = [daily_loads_dict.get(d, 0.0) for d in dates_35]
                
                # 3. Hitung perbandingan untuk 7 hari terakhir (indeks ke-28 sampai 34)
                for i in range(28, 35):
                    tgl = dates_35[i]
                    acute_load = sum(loads_35[i-6 : i+1]) / 7     # Rata-rata 7 hari
                    chronic_load = sum(loads_35[i-27 : i+1]) / 28 # Rata-rata 28 hari
                    
                    acwr_score = round(acute_load / chronic_load, 2) if chronic_load > 0 else 0.0
                    
                    acwr_chart_data["dates"].append(tgl)
                    acwr_chart_data["acwr"].append(acwr_score)

                # ==========================================
                # 🛠️ AMBIL DATA LARI TERAKHIR 
                # ==========================================
                latest_pace = "-"
                latest_hr = "-"
                if not df_run.empty:
                    latest_run = df_run.iloc[-1]
                    latest_pace = latest_run.get('Avg Pace (min/km)', '-')
                    latest_hr = latest_run.get('Avg HR (BPM)', '-')
                    waktu_terakhir_lari = latest_run.get('Tanggal', 'Hari Ini')

                # ==========================================
                # 🤖 LOGIKA EVALUASI OTOMATIS VIA AGENT AI (DENGAN CACHE)
                # ==========================================
                global EVALUASI_CACHE
                profile = load_user_profile()
                latest_acwr = acwr_chart_data["acwr"][-1] if acwr_chart_data["acwr"] else 0.0
                
                # 📅 KALKULASI COUNTDOWN RACE (BARU)
                tanggal_race_str = profile.get('tanggal_race', '')
                sisa_hari_teks = "Belum set jadwal race"
                if tanggal_race_str:
                    try:
                        tgl_race = datetime.strptime(tanggal_race_str, "%Y-%m-%d")
                        sisa_hari = (tgl_race - datetime.now()).days
                        if sisa_hari > 0:
                            sisa_hari_teks = f"H-{sisa_hari} menuju Race Day"
                        elif sisa_hari == 0:
                            sisa_hari_teks = "🔥 RACE DAY! GASPOL! 🔥"
                        else:
                            sisa_hari_teks = "Race sudah terlewati"
                    except:
                        pass
                
                # 🛡️ GATEKEEPER CACHE
                if EVALUASI_CACHE["tanggal_lari_terakhir"] == waktu_terakhir_lari and EVALUASI_CACHE["evaluasi_hari_ini"]:
                    evaluasi_hari_ini = EVALUASI_CACHE["evaluasi_hari_ini"]
                    evaluasi_minggu_lalu = EVALUASI_CACHE["evaluasi_minggu_lalu"]
                else:
                    print("🤖 [Agent Evaluasi] Meracik evaluasi harian baru dari Gemini...")
                    
                    load_history_str = ", ".join([f"{d}: Load {l}" for d, l in zip(graph_data["dates"], graph_data["loads"])])

                    prompt_evaluasi_lari = f"""
Kamu adalah pelatih lari elit pribadi untuk {profile.get('nama')}.
Target Utama: {profile.get('target_latihan')} ({profile.get('target_waktu')})
Jadwal Race: {tanggal_race_str} ({sisa_hari_teks})
Gaya Komunikasi Klien: "{profile.get('catatan_agent')}"
Biometrik: TB {profile.get('tinggi_badan')} cm, BB {profile.get('berat_badan')} kg.

DATA METRIK LATIHAN HARI INI:
- Skor Readiness: {readiness_score}% (Kondisi: {readiness_msg})
- ACWR: {latest_acwr} (Aman 0.8-1.3)
- Histori Beban 7 Sesi: [{load_history_str}]
- Data Lari Terakhir: Pace {latest_pace} min/km, HR {latest_hr} BPM.

TUGAS KAMU (Maksimal 4 kalimat padat):
1. Evaluasi kondisi beban latihannya (ACWR & Readiness) hari ini.
2. Ingatkan soal "{sisa_hari_teks}" agar dia bisa mengatur pacing program latihannya (tapering jika sudah dekat).
3. Berikan "Prediksi Realistis Finish Time" berdasarkan pace rata-rata terakhirnya, bandingkan dengan target {profile.get('target_waktu')}, apakah dia *on-track* atau harus memperbaiki sesuatu?
"""
                    try:
                        resp_eval = ai_client.models.generate_content(
                            model='gemini-2.5-flash',
                            contents=prompt_evaluasi_lari
                        )
                        evaluasi_hari_ini = resp_eval.text.strip()
                        evaluasi_minggu_lalu = f"Rasio ACWR minggu ini ada di angka {latest_acwr}. Ikuti arahan Coach di sebelah kiri untuk jadwal latihan hari ini!"
                        
                        # 💾 SIMPAN KE CACHE BIAR WUSS WUSS PAS DI-REFRESH
                        EVALUASI_CACHE["tanggal_lari_terakhir"] = waktu_terakhir_lari
                        EVALUASI_CACHE["evaluasi_hari_ini"] = evaluasi_hari_ini
                        EVALUASI_CACHE["evaluasi_minggu_lalu"] = evaluasi_minggu_lalu
                        print("✅ [Agent Evaluasi] Evaluasi berhasil di-update dan masuk cache!")
                        
                    except Exception as e:
                        print(f"❌ Error Agent Evaluasi Lari: {e}")
                        evaluasi_hari_ini = f"Gagal memuat evaluasi AI. Kondisi ACWR saat ini: {latest_acwr}. Tetap pantau ritme latihanmu, cuy!"
                        evaluasi_minggu_lalu = "Gagal memuat histori."
                
                # ==========================================
                # 🎯 3. LOGIKA BARU: PACE & HR ZONE VIA AGENT AI
                # ==========================================
                global ZONES_CACHE
                # Kita load ulang profile buat mastiin data terbaru
                profile = load_user_profile() 
                
                # Cek apakah sesi lari ini udah pernah dianalisis AI sebelumnya
                if ZONES_CACHE["tanggal_lari_terakhir"] == waktu_terakhir_lari and ZONES_CACHE["data"]:
                    zones_data = ZONES_CACHE["data"]
                else:
                    print("🤖 [Agent Latihan] Sedang meracik zona latihan spesifik dari Gemini...")
                    
                    prompt_coach = f"""
Kamu adalah pelatih lari elit.
Klienmu: {profile.get('nama')}
Target Latihan: {profile.get('target_latihan')} dalam waktu {profile.get('target_waktu')}
Catatan Klien: "{profile.get('catatan_agent')}"
Max HR: {profile.get('max_hr')} BPM, Resting HR: {profile.get('rhr')} BPM.
Data Lari Terakhir: Pace rata-rata {latest_pace} min/km, HR rata-rata {latest_hr} BPM.

TUGAS:
Buatkan 5 Zona Latihan (Zone 1 sampai Zone 5) untuk klienmu.
Untuk setiap zona, berikan rentang Target HR dan Prediksi Target Pace yang masuk akal dan aman berdasarkan data lari terakhir dan batasan HR-nya.

PENTING: Di bagian deskripsi ('desc'), kamu WAJIB menghubungkan fungsi zona tersebut dengan target waktu {profile.get('target_waktu')} miliknya atau catatan profilnya. Jangan gunakan bahasa robotik, gunakan gaya bahasa suportif pelatih lari.
"""
                    try:
                        resp = ai_client.models.generate_content(
                            model='gemini-2.5-flash',
                            contents=prompt_coach,
                            config=types.GenerateContentConfig(
                                response_mime_type="application/json",
                                response_schema=TrainingZonesResult,
                            )
                        )
                        parsed_ai = json.loads(resp.text)
                        zones_data = parsed_ai.get("zones", [])
                        
                        # Simpan ke cache biar aman dari lemot
                        ZONES_CACHE["tanggal_lari_terakhir"] = waktu_terakhir_lari
                        ZONES_CACHE["data"] = zones_data
                        print("✅ [Agent Latihan] Zona latihan berhasil di-update!")
                        
                    except Exception as e:
                        print(f"❌ Error Coach AI: {e}")
                        zones_data = [] # Fallback kalau AI error


                # ==========================================
                # 🔋 LOGIKA BARU: TRAINING READINESS SCORE (0-100%)
                # ==========================================
                readiness_score = 100 # Default Full Power
                readiness_msg = "Siap tempur!"
                readiness_color = "text-emerald-400"
                
                # 1. Ambil data penting dari hari kemarin dan hari ini
                latest_acwr = acwr_chart_data["acwr"][-1] if acwr_chart_data["acwr"] else 0.0
                yesterday_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
                
                yesterday_load = daily_loads_dict.get(yesterday_date, 0.0)
                yesterday_protein = nutrisi_harian.get(yesterday_date, {}).get('protein', 0.0)

                # ... (lanjutan kodingan hukuman fatigue, pegal otot, dll) ...

                # 5. TERJEMAHKAN KE PESAN HUMANIS
                if readiness_score >= 80:
                    readiness_color = "text-emerald-400"
                    readiness_msg = "🔥 Kondisi Prima! Otot dan sendi udah recovery. Gaspol buat Interval atau Long Run hari ini."
                elif readiness_score >= 50:
                    readiness_color = "text-amber-400"
                    readiness_msg = "⚠️ Recovery setengah jalan. Hindari speed session, mending hajar Easy Run santai aja di Zone 2."
                else:
                    readiness_color = "text-rose-400"
                    readiness_msg = "🛑 Otot masih fatigue & butuh istirahat. Wajib Rest Day atau Active Recovery (jalan kaki santai) hari ini!"

        except Exception as e:
            print(f"❌ Error baca CSV Workout: {e}")

    # Bagian nutrisi tetap sama
    nutrition_data = []
    nutrisi_harian = {} # Wadah buat ngerekap total harian
    evaluasi_nutrisi_mingguan = "Belum ada data nutrisi minggu ini, cuy. Yuk mulai foto makananmu!"
    nutrisi_chart_data = {"dates": [], "kalori": []}
    macro_totals = [0, 0, 0] # [Protein, Karbo, Lemak]

    if os.path.exists(NUTRITION_CSV):
        try:
            df_nutrition = pd.read_csv(NUTRITION_CSV)
            if not df_nutrition.empty:
                kolom_analisis = 'Hasil Analisis Mentah' if 'Hasil Analisis Mentah' in df_nutrition.columns else df_nutrition.columns[-1]
                kolom_catatan = 'Catatan User' if 'Catatan User' in df_nutrition.columns else df_nutrition.columns[1]
                kolom_tanggal = 'Tanggal' if 'Tanggal' in df_nutrition.columns else df_nutrition.columns[0]
                
                for _, row in df_nutrition.iterrows():
                    # Parsing data satuan untuk tabel
                    tanggal_full = str(row[kolom_tanggal])
                    parsed = parse_nutrition_mentah(str(row[kolom_analisis]))
                    
                    # Kita masukin ke nutrition_data buat nampil di tabel HTML (Ambil 5 terakhir aja nanti)
                    nutrition_data.append({"tanggal": tanggal_full, "catatan": row[kolom_catatan], **parsed})
                    
                    # --- MULAI REKAP UNTUK GRAFIK ---
                    tanggal_only = tanggal_full.split()[0]
                    if tanggal_only not in nutrisi_harian:
                        nutrisi_harian[tanggal_only] = {'kalori': 0, 'protein': 0, 'karbo': 0, 'lemak': 0}
                    
                    # Konversi string ke angka dengan aman
                    try: nutrisi_harian[tanggal_only]['kalori'] += float(parsed['kalori'])
                    except: pass
                    try: nutrisi_harian[tanggal_only]['protein'] += float(parsed['protein'])
                    except: pass
                    try: nutrisi_harian[tanggal_only]['karbo'] += float(parsed['karbo'])
                    except: pass
                    try: nutrisi_harian[tanggal_only]['lemak'] += float(parsed['lemak'])
                    except: pass

                # Ambil 7 hari terakhir buat grafik
                sorted_dates = sorted(list(nutrisi_harian.keys()))[-7:]
                
                for d in sorted_dates:
                    nutrisi_chart_data["dates"].append(d)
                    nutrisi_chart_data["kalori"].append(nutrisi_harian[d]['kalori'])
                    
                    # Totalin makro selama seminggu buat grafik Donat
                    macro_totals[0] += nutrisi_harian[d]['protein']
                    macro_totals[1] += nutrisi_harian[d]['karbo']
                    macro_totals[2] += nutrisi_harian[d]['lemak']

                # Bikin Evaluasi Cerdas AI (Rule-based)
                if sorted_dates:
                    avg_cal = sum(nutrisi_chart_data["kalori"]) / len(sorted_dates)
                    avg_pro = macro_totals[0] / len(sorted_dates)
                    
                    if avg_pro > 60:
                        evaluasi_nutrisi_mingguan = f"🔥 Gila! Rata-rata kalori lu {avg_cal:.0f} kkal dengan asupan protein {avg_pro:.0f}g/hari. Otot lu dapet nutrisi VIP buat recovery paska long-run!"
                    elif avg_pro > 30:
                        evaluasi_nutrisi_mingguan = f"✅ Rata-rata kalori {avg_cal:.0f} kkal dengan protein {avg_pro:.0f}g/hari. Lumayan aman, tapi coba selipin dada ayam atau telur lagi biar recovery makin ngebut."
                    else:
                        evaluasi_nutrisi_mingguan = f"⚠️ Warning cuy! Rata-rata kalori {avg_cal:.0f} kkal tapi protein harian lu cuma {avg_pro:.0f}g. Awas otot lu nyusut karena kurang bahan baku recovery!"

                # Balik urutan buat nampilin di log nutrisi (terbaru di atas)
                nutrition_data = nutrition_data[::-1][:5]

                # ─── 📊 AMBIL DATA HARI TERAKHIR UNTUK DONAT HARIAN ───
                latest_date = sorted_dates[-1] if sorted_dates else None
                macro_today = [0, 0, 0]
                if latest_date:
                    macro_today = [
                        round(nutrisi_harian[latest_date]['protein'], 1),
                        round(nutrisi_harian[latest_date]['karbo'], 1),
                        round(nutrisi_harian[latest_date]['lemak'], 1)
                    ]

        except Exception as e:
            print(f"❌ Error baca CSV Nutrisi: {e}")

    # ==========================================
    # 2. TAMBAHIN VARIABEL BARU KE context_data
    # ==========================================
    context_data = {
        "request": request, 
        "workouts": workout_data, 
        "strengths": strength_data,
        "nutritions": nutrition_data,
        "graph_data": graph_data,
        "evaluasi_hari_ini": evaluasi_hari_ini,       
        "evaluasi_minggu_lalu": evaluasi_minggu_lalu, 
        "waktu_terakhir_lari": waktu_terakhir_lari,
        "acwr_chart_data": acwr_chart_data,
        "readiness_score": readiness_score,       #
        "readiness_msg": readiness_msg,           
        "readiness_color": readiness_color,
        "nutrisi_chart_data": nutrisi_chart_data,               # <--- Data grafik Kalori
        "macro_totals": macro_totals,                           # <--- Data grafik Makro
        "macro_today": macro_today,
        "evaluasi_nutrisi_mingguan": evaluasi_nutrisi_mingguan,  # <--- Teks Evaluasi
        "zones_data": zones_data
    }
    return templates.TemplateResponse(request, "dashboard.html", context_data)

# ==========================================
# 👤 FITUR PROFILE & AGENT PERSONALIZATION
# ==========================================
@app.get("/profile", response_class=HTMLResponse)
async def view_profile(request: Request):
    profile_data = load_user_profile()
    return templates.TemplateResponse(request, "profile.html", {"request": request, "profile": profile_data})

@app.post("/profile/update")
async def update_profile(
    nama: str = Form(...),
    tempat_lahir: str = Form(...),
    tanggal_lahir: str = Form(...),
    tinggi_badan: str = Form(""), 
    berat_badan: str = Form(""),
    rhr: str = Form(""),
    max_hr: str = Form(""),
    target_latihan: str = Form(...),
    target_waktu: str = Form(""),
    tanggal_race: str = Form(""),
    catatan_agent: str = Form("")
):
    updated_data = {
        "nama": nama,
        "tempat_lahir": tempat_lahir,
        "tanggal_lahir": tanggal_lahir,
        "tinggi_badan": tinggi_badan,
        "berat_badan": berat_badan,
        "rhr": rhr,
        "max_hr": max_hr,
        "target_latihan": target_latihan,
        "target_waktu": target_waktu,
        "tanggal_race": tanggal_race,
        "catatan_agent": catatan_agent
    }
    with open(PROFILE_JSON, "w", encoding="utf-8") as f:
        json.dump(updated_data, f, indent=4)
        
    return RedirectResponse(url="/profile", status_code=303)