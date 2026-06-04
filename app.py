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

from dotenv import load_dotenv
load_dotenv()

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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Global client HTTPX khusus untuk Supabase
supabase_headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}
# Pastikan SUPABASE_URL memakai string kosong sebagai fallback jika None
supabase_client = httpx.Client(base_url=f"{SUPABASE_URL or ''}/rest/v1", headers=supabase_headers)

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

# Fungsi pembantu untuk load data profil dengan nilai default
# Fungsi load profil Supabase Cloud
def load_user_profile():
    try:
        # Tarik data dari tabel profiles
        res = supabase_client.get("/profiles?select=*")
        data = res.json()
        
        # Kalau tabelnya masih kosong (belum ada user), balikin data default awal
        if not data:
            default_profile = {
                "nama": "User",
                "tempat_lahir": "Jakarta",
                "tanggal_lahir": "2002-02-21",
                "tinggi_badan": 170,  
                "berat_badan": 65,
                "rhr": 60,
                "max_hr": 200,
                "target_latihan": "Race - Target 10K 45 Menit",
                "target_waktu": "00:45:00",
                "tanggal_race": "2026-08-30",
                "catatan_agent": "gamau kalah"
            }
            # Insert data default pertama kali ke Supabase
            supabase_client.post("/profiles", json=default_profile)
            return default_profile
            
        # Kalau ada, ambil baris pertama
        return data[0]
    except Exception as e:
        print(f"❌ Gagal load profil dari Supabase: {e}")
        # Fallback aman biar web ga crash kalau koneksi internet putus
        return {"nama": "User", "max_hr": "200", "target_latihan": "Maintain", "target_waktu": "-", "catatan_agent": ""}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Menampilkan halaman login depan"""
    return templates.TemplateResponse(request, "login.html")

@app.get("/auth/login")
async def auth_login():
    """Mengandalkan Supabase untuk melempar user ke halaman login Google resmi"""
    # Ganti dengan URL project Supabase lu sendiri
    supabase_project_url = "https://reisjyudhsdiapezrqop.supabase.co"
    
    # Kita arahkan provider ke google. 
    # Setelah sukses login di Google, Supabase otomatis membalikkan user ke aplikasi lokal kita
    target_url = f"{supabase_project_url}/auth/v1/authorize?provider=google&redirect_to=http://localhost:8000/"
    
    return RedirectResponse(url=target_url)

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
    catatan: str = Form("Tanpa catatan"), 
    foto: UploadFile = File(None)         
):
    """Endpoint untuk Create Data Nutrisi via AI (Simpan ke Supabase Cloud)"""
    # Gunakan format ISO standar yang disukai PostgreSQL timestamptz
    waktu_makan = datetime.now().isoformat()
    
    contents = []
    if foto and foto.filename:
        image_bytes = await foto.read()
        mime_type = foto.content_type or 'image/jpeg'
        contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
        
    profile = load_user_profile()
    
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
Jika makanan ini tidak sejalan dengan targetnya, beri teguran suportif!
"""
    contents.append(prompt_text)

    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=NutritionLog,
            )
        )
        
        data_ai = json.loads(response.text)
        
        # Rakit payload sesuai nama kolom di tabel SQL Supabase lu
        payload_nutrition = {
            "tanggal": waktu_makan,
            "catatan_user": catatan,
            "kalori": float(data_ai.get('kalori', 0)),
            "protein": float(data_ai.get('protein', 0)),
            "karbo": float(data_ai.get('karbo', 0)),
            "lemak": float(data_ai.get('lemak', 0)),
            "keterangan": data_ai.get('keterangan', 'Dianalisis oleh AI.')
        }
        
        # Kirim data ke Supabase cloud!
        supabase_client.post("/nutrition", json=payload_nutrition)
        print("✅ Data nutrisi baru berhasil masuk cloud Supabase!")
            
    except Exception as e:
        print(f"❌ Error Gemini atau Supabase API: {e}")
        # Fallback payload darurat biar aplikasi kagak macet
        fallback_payload = {
            "tanggal": waktu_makan,
            "catatan_user": catatan,
            "kalori": 0, "protein": 0, "karbo": 0, "lemak": 0,
            "keterangan": f"Gagal dianalisis AI - {e}"
        }
        supabase_client.post("/nutrition", json=fallback_payload)
        
    return RedirectResponse(url="/", status_code=303)

@app.post("/delete_nutrition")
async def delete_nutrition(tanggal: str = Form(...)):
    """Endpoint untuk Delete Data Nutrisi berdasarkan Tanggal di Cloud"""
    try:
        # Kirim perintah DELETE dengan filter kolom tanggal harus sama (eq) dengan input
        supabase_client.delete(f"/nutrition?tanggal=eq.{tanggal}")
        print(f"🗑️ Log nutrisi tanggal {tanggal} berhasil dihapus dari cloud!")
    except Exception as e:
        print(f"❌ Gagal menghapus data di Supabase: {e}")
        
    return RedirectResponse(url="/", status_code=303)

@app.get("/", response_class=HTMLResponse)
async def read_dashboard(request: Request):
    token = request.cookies.get("sb_access_token")
    
    # Jika token kagak ketemu, langsung tendang user ke halaman login
    if not token:
        return RedirectResponse(url="/login", status_code=303)
    
    workout_data = []
    strength_data = []
    graph_data = {"dates": [], "loads": []}
    
    evaluasi_hari_ini = "Belum ada latihan tercatat hari ini, cuy. Jangan lupa gerak!"
    evaluasi_minggu_lalu = "Minggu lalu performa latihanmu stabil. Pertahankan aerobic base!"

    # === NILAI DEFAULT AMAN (ANTI CRASH) ===
    waktu_terakhir_lari = "Belum ada data"
    acwr_chart_data = {"dates": [], "acwr": []}
    readiness_score = 100
    readiness_msg = "Belum ada data latihan. Baterai full siap tempur!"
    readiness_color = "text-emerald-400"
    zones_data = []
    
    nutrition_data = []
    nutrisi_harian = {} 
    evaluasi_nutrisi_mingguan = "Belum ada data nutrisi minggu ini, cuy. Yuk mulai foto makananmu!"
    nutrisi_chart_data = {"dates": [], "kalori": []}
    macro_totals = [0, 0, 0] 
    macro_today = [0, 0, 0]
    # =======================================

    # ─── 🏃‍♂️ 1. AMBIL & PROSES DATA WORKOUT DARI SUPABASE ───
    try:
        # Tarik semua data workout diurutkan berdasarkan tanggal terlama ke terbaru (.asc)
        res_w = supabase_client.get("/workouts?order=tanggal.asc")
        workout_records = res_w.json()
    except Exception as e:
        print(f"❌ Gagal ambil data workout dari Supabase: {e}")
        workout_records = []

    if workout_records:
        try:
            # Konversi json dari Supabase ke DataFrame Pandas
            df_supabase_w = pd.DataFrame(workout_records)
            
            # Trik Sulap: Rename nama kolom agar COCOK 100% dengan kodingan & HTML lama lu!
            df_workout = df_supabase_w.rename(columns={
                'tanggal': 'Tanggal',
                'jenis_olahraga': 'Jenis Olahraga',
                'durasi_menit': 'Durasi (Menit)',
                'avg_hr': 'Avg HR (BPM)',
                'avg_pace': 'Avg Pace (min/km)'
            })

            # Ambil 7 sesi terakhir untuk grafik Training Load
            df_graph = df_workout.tail(7)
            for _, row in df_graph.iterrows():
                durasi = float(row.get('Durasi (Menit)', 0))
                hr = float(row.get('Avg HR (BPM)', 0)) if pd.notna(row.get('Avg HR (BPM)')) else 130
                training_load = round((durasi * hr) / 100, 1)
                
                # Format tanggal agar tidak kepanjangan di grafik
                graph_data["dates"].append(str(row.get('Tanggal', '-')).split()[0].split('T')[0])
                graph_data["loads"].append(training_load)

            # Filter data untuk tabel Run & Strength
            df_run = df_workout[df_workout['Jenis Olahraga'].str.lower() == 'run']
            workout_data = df_run.tail(5).to_dict(orient="records")
            
            df_strength = df_workout[df_workout['Jenis Olahraga'].str.lower() != 'run']
            strength_data = df_strength.tail(5).to_dict(orient="records")

            # === KALKULASI ACWR (Acute vs Chronic Workload Ratio) ===
            df_acwr = df_workout.copy()
            df_acwr['Date'] = pd.to_datetime(df_acwr['Tanggal']).dt.strftime('%Y-%m-%d')
            df_acwr['HR_Clean'] = pd.to_numeric(df_acwr['Avg HR (BPM)'], errors='coerce').fillna(130)
            df_acwr['Dur_Clean'] = pd.to_numeric(df_acwr['Durasi (Menit)'], errors='coerce').fillna(0)
            df_acwr['Daily_Load'] = (df_acwr['Dur_Clean'] * df_acwr['HR_Clean']) / 100
            
            daily_loads_dict = df_acwr.groupby('Date')['Daily_Load'].sum().to_dict()
            
            today_date = datetime.now()
            dates_35 = [(today_date - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(34, -1, -1)]
            loads_35 = [daily_loads_dict.get(d, 0.0) for d in dates_35]
            
            for i in range(28, 35):
                tgl = dates_35[i]
                acute_load = sum(loads_35[i-6 : i+1]) / 7     
                chronic_load = sum(loads_35[i-27 : i+1]) / 28 
                acwr_score = round(acute_load / chronic_load, 2) if chronic_load > 0 else 0.0
                
                acwr_chart_data["dates"].append(tgl)
                acwr_chart_data["acwr"].append(acwr_score)

            # AMBIL DATA LARI TERAKHIR
            latest_pace = "-"
            latest_hr = "-"
            if not df_run.empty:
                latest_run = df_run.iloc[-1]
                latest_pace = latest_run.get('Avg Pace (min/km)', '-')
                latest_hr = latest_run.get('Avg HR (BPM)', '-')
                waktu_terakhir_lari = str(latest_run.get('Tanggal', 'Hari Ini')).replace('T', ' ')[:16]

        except Exception as e:
            print(f"❌ Error kalkulasi metrik lari dari data Supabase: {e}")

    # ─── 🍳 2. AMBIL & PROSES DATA NUTRISI DARI SUPABASE ───
    try:
        # Ambil data makanan urut tanggal terlama ke terbaru
        res_n = supabase_client.get("/nutrition?order=tanggal.asc")
        nutrition_records = res_n.json()
    except Exception as e:
        print(f"❌ Gagal ambil data nutrisi dari Supabase: {e}")
        nutrition_records = []

    if nutrition_records:
        try:
            for row in nutrition_records:
                # Ambil tanggal dan hilangkan format T/Z bawaan database agar estetik di tabel HTML
                tanggal_full = str(row.get('tanggal', '-')).replace('T', ' ')[:16]
                catatan = row.get('catatan_user', '-')
                
                parsed = {
                    "kalori": str(int(float(row.get('kalori', 0)))),
                    "protein": str(int(float(row.get('protein', 0)))),
                    "karbo": str(int(float(row.get('karbo', 0)))),
                    "lemak": str(int(float(row.get('lemak', 0)))),
                    "keterangan": row.get('keterangan', '-')
                }
                
                nutrition_data.append({"tanggal": tanggal_full, "catatan": catatan, **parsed})
                
                # Rekap total kalori harian untuk grafik tren
                tanggal_only = str(row.get('tanggal', '-')).split('T')[0]
                if tanggal_only not in nutrisi_harian:
                    nutrisi_harian[tanggal_only] = {'kalori': 0, 'protein': 0, 'karbo': 0, 'lemak': 0}
                
                try: nutrisi_harian[tanggal_only]['kalori'] += float(parsed['kalori'])
                except: pass
                try: nutrisi_harian[tanggal_only]['protein'] += float(parsed['protein'])
                except: pass
                try: nutrisi_harian[tanggal_only]['karbo'] += float(parsed['karbo'])
                except: pass
                try: nutrisi_harian[tanggal_only]['lemak'] += float(parsed['lemak'])
                except: pass

            # Ambil 7 hari terakhir buat grafik batang kalori
            sorted_dates = sorted(list(nutrisi_harian.keys()))[-7:]
            for d in sorted_dates:
                nutrisi_chart_data["dates"].append(d)
                nutrisi_chart_data["kalori"].append(nutrisi_harian[d]['kalori'])
                
                macro_totals[0] += nutrisi_harian[d]['protein']
                macro_totals[1] += nutrisi_harian[d]['karbo']
                macro_totals[2] += nutrisi_harian[d]['lemak']

            # Evaluasi Cerdas AI (Rule-based)
            if sorted_dates:
                avg_cal = sum(nutrisi_chart_data["kalori"]) / len(sorted_dates)
                avg_pro = macro_totals[0] / len(sorted_dates)
                if avg_pro > 60:
                    evaluasi_nutrisi_mingguan = f"🔥 Gila! Rata-rata kalori lu {avg_cal:.0f} kkal dengan asupan protein {avg_pro:.0f}g/hari. Otot lu dapet nutrisi VIP buat recovery paska long-run!"
                elif avg_pro > 30:
                    evaluasi_nutrisi_mingguan = f"✅ Rata-rata kalori {avg_cal:.0f} kkal dengan protein {avg_pro:.0f}g/hari. Lumayan aman, tapi coba selipin dada ayam atau telur lagi biar recovery makin ngebut."
                else:
                    evaluasi_nutrisi_mingguan = f"⚠️ Warning cuy! Rata-rata kalori {avg_cal:.0f} kkal tapi protein harian lu cuma {avg_pro:.0f}g. Awas otot lu nyusut karena kurang bahan baku recovery!"

            # Balik urutan log nutrisi harian (makanan terbaru nangkring di atas)
            nutrition_data = nutrition_data[::-1][:5]

            # Ambil data hari terakhir untuk Donat Harian
            latest_date = sorted_dates[-1] if sorted_dates else None
            if latest_date:
                macro_today = [
                    round(nutrisi_harian[latest_date]['protein'], 1),
                    round(nutrisi_harian[latest_date]['karbo'], 1),
                    round(nutrisi_harian[latest_date]['lemak'], 1)
                ]
        except Exception as e:
            print(f"❌ Error pemrosesan data gizi: {e}")

    # ─── 🤖 3. GENERATE JADWAL ZONA LATIHAN & EVALUASI AGENT AI ───
    profile = load_user_profile()
    latest_acwr = acwr_chart_data["acwr"][-1] if acwr_chart_data["acwr"] else 0.0
    
    # Kalkulasi Countdown Race
    tanggal_race_str = profile.get('tanggal_race', '')
    sisa_hari_teks = "Belum set jadwal race"
    if tanggal_race_str:
        try:
            tgl_race = datetime.strptime(tanggal_race_str.split('T')[0], "%Y-%m-%d")
            sisa_hari = (tgl_race - datetime.now()).days
            if sisa_hari > 0: sisa_hari_teks = f"H-{sisa_hari} menuju Race Day"
            elif sisa_hari == 0: sisa_hari_teks = "🔥 RACE DAY! GASPOL! 🔥"
            else: sisa_hari_teks = "Race sudah terlewati"
        except: pass

    # Inisialisasi Score Readiness Baterai Tubuh
    if workout_records:
        yesterday_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        yesterday_load = daily_loads_dict.get(yesterday_date, 0.0)
        
        if latest_acwr > 1.5 or yesterday_load > 150:
            readiness_score = 45
        elif latest_acwr > 1.3 or yesterday_load > 90:
            readiness_score = 70
        else:
            readiness_score = 100

    if readiness_score >= 80:
        readiness_color, readiness_msg = "text-emerald-400", "🔥 Kondisi Prima! Otot dan sendi udah recovery. Gaspol buat Interval atau Long Run hari ini."
    elif readiness_score >= 50:
        readiness_color, readiness_msg = "text-amber-400", "⚠️ Recovery setengah jalan. Hindari speed session, mending hajar Easy Run santai aja di Zone 2."
    else:
        readiness_color, readiness_msg = "text-rose-400", "🛑 Otot masih fatigue & butuh istirahat. Wajib Rest Day atau Active Recovery hari ini!"

    # GATEKEEPER CACHE AGENT EVALUASI
    global EVALUASI_CACHE, ZONES_CACHE
    if EVALUASI_CACHE["tanggal_lari_terakhir"] == waktu_terakhir_lari and EVALUASI_CACHE["evaluasi_hari_ini"]:
        evaluasi_hari_ini = EVALUASI_CACHE["evaluasi_hari_ini"]
        evaluasi_minggu_lalu = EVALUASI_CACHE["evaluasi_minggu_lalu"]
    else:
        print("🤖 [Agent Evaluasi] Meracik analisis performa baru via Gemini...")
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
- Histori Beban Sesi: [{load_history_str}]
- Data Lari Terakhir: Pace {latest_pace} min/km, HR {latest_hr} BPM.

TUGAS KAMU (Maksimal 4 kalimat padat):
1. Evaluasi kondisi beban latihannya (ACWR & Readiness) hari ini.
2. Ingatkan soal "{sisa_hari_teks}" agar dia bisa mengatur pacing program latihannya.
3. Berikan "Prediksi Realistis Finish Time" berdasarkan pace rata-rata terakhirnya, bandingkan dengan target {profile.get('target_waktu')}, apakah dia *on-track* atau harus memperbaiki sesuatu?
"""
        try:
            resp_eval = ai_client.models.generate_content(model='gemini-2.5-flash', contents=prompt_evaluasi_lari)
            evaluasi_hari_ini = resp_eval.text.strip()
            evaluasi_minggu_lalu = f"Rasio ACWR minggu ini ada di angka {latest_acwr}. Ikuti arahan Coach di sebelah kiri untuk jadwal latihan hari ini!"
            EVALUASI_CACHE = {"tanggal_lari_terakhir": waktu_terakhir_lari, "evaluasi_hari_ini": evaluasi_hari_ini, "evaluasi_minggu_lalu": evaluasi_minggu_lalu}
        except Exception as e:
            print(f"❌ Error Agent Evaluasi Lari: {e}")
            evaluasi_hari_ini = f"Gagal memuat evaluasi AI. Kondisi ACWR: {latest_acwr}."

    # GATEKEEPER CACHE AGENT ZONA TRAINING
    if ZONES_CACHE["tanggal_lari_terakhir"] == waktu_terakhir_lari and ZONES_CACHE["data"]:
        zones_data = ZONES_CACHE["data"]
    else:
        print("🤖 [Agent Latihan] Menghitung ulang zona target pace via Gemini...")
        prompt_coach = f"""
Kamu adalah pelatih lari elit. Klienmu: {profile.get('nama')}
Target Latihan: {profile.get('target_latihan')} dalam waktu {profile.get('target_waktu')}
Catatan Klien: "{profile.get('catatan_agent')}"
Max HR: {profile.get('max_hr')} BPM, Resting HR: {profile.get('rhr')} BPM.
Data Lari Terakhir: Pace rata-rata {latest_pace} min/km, HR rata-rata {latest_hr} BPM.

TUGAS: Buatkan 5 Zona Latihan (Zone 1 sampai Zone 5) untuk klienmu.
Untuk setiap zona, berikan rentang Target HR dan Prediksi Target Pace yang masuk akal dan aman berdasarkan data lari terakhir dan batasan HR-nya.
PENTING: Di bagian deskripsi ('desc'), hubungkan fungsi zona tersebut dengan target waktu {profile.get('target_waktu')} miliknya atau catatan profilnya. Jangan gunakan bahasa robotik.
"""
        try:
            resp = ai_client.models.generate_content(
                model='gemini-2.5-flash', contents=prompt_coach,
                config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=TrainingZonesResult)
            )
            zones_data = json.loads(resp.text).get("zones", [])
            ZONES_CACHE = {"tanggal_lari_terakhir": waktu_terakhir_lari, "data": zones_data}
        except Exception as e:
            print(f"❌ Error Coach AI: {e}")

    context_data = {
        "request": request, "workouts": workout_data, "strengths": strength_data, "nutritions": nutrition_data,
        "graph_data": graph_data, "evaluasi_hari_ini": evaluasi_hari_ini, "evaluasi_minggu_lalu": evaluasi_minggu_lalu,
        "waktu_terakhir_lari": waktu_terakhir_lari, "acwr_chart_data": acwr_chart_data, "readiness_score": readiness_score,
        "readiness_msg": readiness_msg, "readiness_color": readiness_color, "nutrisi_chart_data": nutrisi_chart_data,
        "macro_totals": macro_totals, "macro_today": macro_today, "evaluasi_nutrisi_mingguan": evaluasi_nutrisi_mingguan,
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
    try:
        # Kita cek dulu ID profilnya biar bisa kita update datanya tepat sasaran
        res_check = supabase_client.get("/profiles?select=id")
        check_data = res_check.json()
        
        if check_data:
            profile_id = check_data[0]['id']
            # Update baris profil yang sudah ada berdasarkan ID nya
            supabase_client.patch(f"/profiles?id=eq.{profile_id}", json=updated_data)
            print("✅ Profil berhasil diupdate di Supabase cloud!")
        else:
            supabase_client.post("/profiles", json=updated_data)
    except Exception as e:
        print(f"❌ Gagal mengupdate profil ke Supabase: {e}")
        
    return RedirectResponse(url="/profile", status_code=303)