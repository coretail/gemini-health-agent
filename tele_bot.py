import os
import json
import httpx
from datetime import datetime, timedelta
from PIL import Image
import telebot
from stravalib.client import Client
import requests
import threading 
import asyncio
import time

# SDK Gemini Baru & Pydantic untuk Structured Output
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from dotenv import load_dotenv
load_dotenv()

# ─── BUNGKAM WARNING STRAVA ─────────────────────────────────
import logging
logging.getLogger('stravalib').setLevel(logging.CRITICAL)
# ──────────────────────────────────────────────────────────────────

# 1. KONFIGURASI TOKEN & KREDENSIAL CLOUD
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
STRAVA_CLIENT_ID = int(os.getenv("STRAVA_CLIENT_ID")) if os.getenv("STRAVA_CLIENT_ID") else 253476
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
STRAVA_REFRESH_TOKEN = os.getenv("STRAVA_REFRESH_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")  

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Inisialisasi API Bot, Gemini Client, dan HTTPX Supabase Client
bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

supabase_headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}
supabase_client = httpx.Client(base_url=f"{SUPABASE_URL or ''}/rest/v1", headers=supabase_headers)

# Skema JSON Gizi untuk Validasi Data Gemini
class NutritionLog(BaseModel):
    kalori: int = Field(description="Estimasi total kalori (kkal)")
    protein: int = Field(description="Estimasi protein (gram)")
    karbo: int = Field(description="Estimasi karbohidrat (gram)")
    lemak: int = Field(description="Estimasi lemak (gram)")
    keterangan: str = Field(description="Analisis singkat maksimal 2 kalimat apakah makanan ini bagus untuk recovery latihan marathon.")


# 2. FUNGSI AUTO-REFRESH TOKEN STRAVA
def get_strava_access_token():
    url = "https://www.strava.com/oauth/token"
    payload = {
        'client_id': STRAVA_CLIENT_ID,
        'client_secret': STRAVA_CLIENT_SECRET,
        'refresh_token': STRAVA_REFRESH_TOKEN,
        'grant_type': 'refresh_token'
    }
    try:
        response = requests.post(url, data=payload)
        response_json = response.json()
        if response.status_code == 200:
            return response_json['access_token']
        else:
            print(f"❌ Gagal refresh token Strava. Respon: {response_json}")
            return None
    except Exception as e:
        print(f"❌ Error saat melakukan request token: {e}")
        return None


# ─── HANDLER COMMAND /START & /HELP ─────────────────────────────────
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = (
        "<b>🏃‍♂️🤖 HEALTH TRACKER AGENT CLOUD READY! 🤖🏃‍♂️</b>\n\n"
        "Halo Abdullah Dzaki! Sistem bot lu sekarang udah terkoneksi ke Supabase Singapore.\n\n"
        "<b>Menu Perintah Sakti:</b>\n"
        "▶️ /sync_strava - Tarik aktivitas lari terakhir dari Strava langsung ke Cloud\n"
        "▶️ /sync_bulan  - Tarik seluruh aktivitas 30 hari ke belakang tanpa duplikat\n\n"
        "📷 <b>Fitur Vision AI:</b>\n"
        "Kirim foto makanan + caption porsi ke sini buat langsung di-analisis & disimpan ke Cloud database!"
    )
    bot.reply_to(message, welcome_text, parse_mode='HTML')


# ==================================================================
# 🔄 1. FUNGSI INTI PENARIK DATA STRAVA (CONNECTED TO SUPABASE)
# ==================================================================
async def strava_sync():
    """Fungsi murni untuk narik data Strava langsung ke Supabase Cloud"""
    print("🔄 Memulai sinkronisasi data dari API Strava ke Cloud...")
    fresh_token = get_strava_access_token()
    if not fresh_token:
        print("❌ Gagal dapetin akses token Strava.")
        return False
        
    strava_client = Client(access_token=fresh_token)
    activities = list(strava_client.get_activities(limit=5))
    
    if not activities:
        print("📭 Kagak ada riwayat aktivitas di Strava.")
        return False
        
    latest_run = activities[0]
    act_date = latest_run.start_date_local.strftime("%Y-%m-%d %H:%M")
    
    # Cek Duplikat di Supabase (Normalisasi string timestamp 'T' ke space untuk akurasi)
    try:
        res_check = supabase_client.get("/workouts?order=tanggal.desc&limit=10")
        existing_runs = res_check.json()
        existing_dates = [str(r.get('tanggal', '')).replace('T', ' ')[:16] for r in existing_runs]
        if act_date in existing_dates:
            print(f"⚠️ Data tanggal {act_date} sudah aman tersimpan di Supabase.")
            return True
    except Exception as e:
        print(f"⚠️ Gagal melakukan pengecekan duplikat di cloud: {e}")

    # Ekstrak Metrik Utama sesuai skema SQL
    act_type = str(latest_run.type).replace("root='", "").replace("'", "")
    act_duration = int(latest_run.elapsed_time / 60) if latest_run.elapsed_time else 0
    avg_hr = int(latest_run.average_heartrate) if getattr(latest_run, 'average_heartrate', None) else None
    
    avg_pace = None
    if getattr(latest_run, 'average_speed', None) and act_type == "Run":
        speed_ms = float(latest_run.average_speed)
        if speed_ms > 0:
            total_minutes = 16.6667 / speed_ms
            avg_pace = f"{int(total_minutes):02d}:{int((total_minutes - int(total_minutes)) * 60):02d}"

    # Pasang payload sesuai kolom PostgreSQL
    payload_workout = {
        "tanggal": act_date,
        "jenis_olahraga": act_type,
        "durasi_menit": float(act_duration),
        "avg_hr": avg_hr,
        "avg_pace": avg_pace,
        "jarak": jarak_km
    }
    
    try:
        supabase_client.post("/workouts", json=payload_workout)
        print("✅ Data Strava berhasil diterbangkan ke Supabase Cloud!")
        return True
    except Exception as e:
        print(f"❌ Gagal mengirim data workout ke Supabase: {e}")
        return False


@bot.message_handler(commands=['sync_strava'])
def sync_strava_data(message):
    bot.reply_to(message, "🔄 Menghubungi server Strava... Tunggu bentar ya, cuy!")
    try:
        asyncio.run(strava_sync())
        bot.reply_to(message, "✅ <b>SINKRONISASI STRAVA SUKSES VIA CHAT!</b>\nDatabase cloud Supabase udah diperbarui, cuy. Cek dashboard gih!", parse_mode='HTML')
    except Exception as e:
        bot.reply_to(message, f"❌ Error Bot: {e}")


# ==================================================================
# 📅 2. FUNGSI TARIK DATA 1 BULAN (BULK SYNC TO CLOUD)
# ==================================================================
def get_strava_access_token(refresh_token_user):
    url = "https://www.strava.com/oauth/token"
    payload = {
        'client_id': STRAVA_CLIENT_ID,
        'client_secret': STRAVA_CLIENT_SECRET,
        'refresh_token': refresh_token_user, # 👈 Pake token milik user terkait
        'grant_type': 'refresh_token'
    }
    try:
        response = requests.post(url, data=payload)
        response_json = response.json()
        if response.status_code == 200:
            return response_json['access_token']
        else:
            print(f"❌ Gagal refresh token Strava. Respon: {response_json}")
            return None
    except Exception as e:
        print(f"❌ Error saat melakukan request token: {e}")
        return None

# 2. Update fungsi sync harian agar menerima user_id & refresh_token
async def strava_sync(user_id: str, refresh_token: str):
    print(f"🔄 Memulai sinkronisasi data Strava untuk User: {user_id}...")
    fresh_token = get_strava_access_token(refresh_token)
    if not fresh_token:
        return False
        
    strava_client = Client(access_token=fresh_token)
    activities = list(strava_client.get_activities(limit=5))
    
    if not activities:
        return False
        
    latest_run = activities[0]
    act_date = latest_run.start_date_local.strftime("%Y-%m-%d %H:%M")
    
    # FILTER DUPLIKAT: Sekarang dicek berdasarkan user_id nya juga!
    try:
        res_check = supabase_client.get(f"/workouts?user_id=eq.{user_id}&order=tanggal.desc&limit=10")
        existing_runs = res_check.json()
        existing_dates = [str(r.get('tanggal', '')).replace('T', ' ')[:16] for r in existing_runs]
        if act_date in existing_dates:
            print(f"⚠️ Data tanggal {act_date} milik {user_id} sudah ada.")
            return True
    except Exception as e:
        print(f"⚠️ Gagal cek duplikat: {e}")

    act_type = str(latest_run.type).replace("root='", "").replace("'", "")
    act_duration = int(latest_run.elapsed_time / 60) if latest_run.elapsed_time else 0
    avg_hr = int(latest_run.average_heartrate) if getattr(latest_run, 'average_heartrate', None) else None
    jarak_km = round(float(latest_run.distance) / 1000, 2) if getattr(latest_run, 'distance', None) else 0.0
    
    avg_pace = None
    if getattr(latest_run, 'average_speed', None) and act_type == "Run":
        speed_ms = float(latest_run.average_speed)
        if speed_ms > 0:
            total_minutes = 16.6667 / speed_ms
            avg_pace = f"{int(total_minutes):02d}:{int((total_minutes - int(total_minutes)) * 60):02d}"

    payload_workout = {
        "user_id": user_id,          # 👈 Catat siapa pemilik data lari ini
        "tanggal": act_date,
        "jenis_olahraga": act_type,
        "durasi_menit": float(act_duration),
        "avg_hr": avg_hr,
        "avg_pace": avg_pace,
        "jarak": jarak_km
    }
    
    try:
        supabase_client.post("/workouts", json=payload_workout)
        print(f"✅ Data Strava milik {user_id} berhasil masuk cloud!")
        return True
    except Exception as e:
        print(f"❌ Gagal mengirim data workout ke Supabase: {e}")
        return False

# 3. Update fungsi Bulk Sync 1 Bulan
async def strava_sync_1_month(user_id: str, refresh_token: str):
    print(f"🔄 [Bulk] Menarik data 1 bulan untuk User: {user_id}...")
    fresh_token = get_strava_access_token(refresh_token)
    if not fresh_token:
        return "Gagal dapet token Strava, cuy."

    tiga_puluh_hari_lalu = datetime.now() - timedelta(days=30)
    strava_client = Client(access_token=fresh_token)
    
    try:
        activities = list(strava_client.get_activities(after=tiga_puluh_hari_lalu, limit=100))
        if not activities:
            return "📭 Kagak ada aktivitas sama sekali dalam 30 hari terakhir."
            
        res_all = supabase_client.get(f"/workouts?user_id=eq.{user_id}&select=tanggal")
        existing_dates = [str(r.get('tanggal', '')).replace('T', ' ')[:16] for r in res_all.json()]
        
        records_w = []
        new_count = 0
        
        for act in reversed(activities):
            act_date = act.start_date_local.strftime("%Y-%m-%d %H:%M")
            if act_date in existing_dates:
                continue
                
            act_type = str(act.type).replace("root='", "").replace("'", "")
            act_duration = int(act.elapsed_time / 60) if act.elapsed_time else 0
            avg_hr = int(act.average_heartrate) if getattr(act, 'average_heartrate', None) else None
            jarak_km = round(float(act.distance) / 1000, 2) if getattr(act, 'distance', None) else 0.0
            
            avg_pace = None
            if getattr(act, 'average_speed', None) and act_type == "Run":
                speed_ms = float(act.average_speed)
                if speed_ms > 0:
                    total_minutes = 16.6667 / speed_ms
                    avg_pace = f"{int(total_minutes):02d}:{int((total_minutes - int(total_minutes)) * 60):02d}"

            records_w.append({
                "user_id": user_id,  # 👈 Beri label pemilik
                "tanggal": act_date,
                "jenis_olahraga": act_type,
                "durasi_menit": float(act_duration),
                "avg_hr": avg_hr,
                "avg_pace": avg_pace,
                "jarak": jarak_km
            })
            new_count += 1
            
        if records_w:
            supabase_client.post("/workouts", json=records_w)
            
        return f"💾 Sebanyak {new_count} data baru berhasil di-push ke cloud Supabase!"
    except Exception as e:
        return f"❌ Terjadi error migrasi bulk: {repr(e)}"


@bot.message_handler(commands=['sync_bulan'])
def sync_strava_bulan(message):
    bot.reply_to(message, "🔄 Menarik riwayat Strava lu 30 hari ke belakang... Harap sabar ya cuy!")
    try:
        hasil = asyncio.run(strava_sync_1_month())
        bot.reply_to(message, hasil)
    except Exception as e:
        bot.reply_to(message, f"❌ Error eksekusi bot: {e}")


# ─── HANDLER FOTO MAKANAN + CAPTION PORSI (STRUCTURED VISION AI) ──────
@bot.message_handler(content_types=['photo'])
def handle_food_image(message):
    bot.reply_to(message, "📸 Foto makanan diterima! Coach Gemini lagi neropong kandungan gizi dan mencatat ke Cloud... 🍳")
    try:
        user_caption = message.caption if message.caption else "Tidak ada catatan porsi tambahan."
        
        # Download Foto ke lokal temporary
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        temp_dir = "temp_images"
        os.makedirs(temp_dir, exist_ok=True)
        temp_image_path = os.path.join(temp_dir, "temp_food.jpg")
        
        with open(temp_image_path, 'wb') as new_file:
            new_file.write(downloaded_file)
            
        # Ambil data profil dari Supabase untuk meracik prompt personalisasi gizi
        try:
            res_profile = supabase_client.get("/profiles?select=*")
            profile_list = res_profile.json()
            profile = profile_list[0] if profile_list else {}
        except:
            profile = {}

        # Membaca bytes file gambar
        with open(temp_image_path, 'rb') as f:
            image_bytes = f.read()

        prompt_text = f"""
Kamu adalah asisten gizi olahraga pribadi untuk {profile.get('nama', 'User')}.
Berikut adalah profil biometrik dan targetnya:
- Target Latihan: {profile.get('target_latihan', 'Maintain')}
- Target Waktu: {profile.get('target_waktu', '-')}
- Batas Jantung: RHR {profile.get('rhr', '-')} BPM, Max HR {profile.get('max_hr', '-')} BPM
- Biometrik Fisik: Tinggi {profile.get('tinggi_badan', '-')} cm, Berat {profile.get('berat_badan', '-')} kg
- Catatan Khusus & Preferensi: "{profile.get('catatan_agent', 'Tidak ada catatan khusus.')}"

TUGAS KAMU:
Analisis kandungan nutrisi makanan yang difoto ini dengan mempertimbangkan catatan porsi dari user berikut: "{user_caption}".

PENTING: Di bagian "keterangan", sesuaikan analisismu secara spesifik dengan Catatan Khusus, Target Latihan, dan Biometrik Fisiknya. 
Jika makanan ini tidak sejalan dengan targetnya, beri teguran suportif!
"""
        
        # Panggil Gemini API v2.5 dengan Output Terstruktur JSON
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type='image/jpeg'),
                prompt_text
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=NutritionLog,
            )
        )
        
        data_ai = json.loads(response.text)
        waktu_makan = datetime.now().isoformat()
        
        # Kirim laporan rapi balik ke user Telegram
        laporan_text = (
            f"📝 <b>HASIL ANALISIS NUTRISI CLOUD AI</b> 📝\n\n"
            f"✍️ <b>Porsi Lu:</b> <i>{user_caption}</i>\n\n"
            f"🍲 <b>Estimasi Kandungan:</b>\n"
            f"• 🔥 Kalori: {data_ai.get('kalori', 0)} kkal\n"
            f"• 🥚 Protein: {data_ai.get('protein', 0)} gram\n"
            f"• 🌾 Karbohidrat: {data_ai.get('karbo', 0)} gram\n"
            f"• 🥑 Lemak: {data_ai.get('lemak', 0)} gram\n\n"
            f"💡 <b>Analisis Coach:</b>\n<i>{data_ai.get('keterangan', '-')}</i>"
        )
        bot.reply_to(message, laporan_text, parse_mode='HTML')
        
        # Kirim data rapi terstruktur ke tabel nutrition di Supabase
        payload_nutrition = {
            "tanggal": waktu_makan,
            "catatan_user": user_caption,
            "kalori": float(data_ai.get('kalori', 0)),
            "protein": float(data_ai.get('protein', 0)),
            "karbo": float(data_ai.get('karbo', 0)),
            "lemak": float(data_ai.get('lemak', 0)),
            "keterangan": data_ai.get('keterangan', 'Dianalisis oleh AI.')
        }
        supabase_client.post("/nutrition", json=payload_nutrition)
        print("✅ Log makanan dari Telegram berhasil ter-upload ke Supabase!")

        if os.path.exists(temp_image_path):
            os.remove(temp_image_path)
            
    except Exception as e:
        bot.reply_to(message, f"❌ Gagal memproses foto makanan, cuy. Error: {e}")

# ─── RUN SERVER BOT STANDBY ─────────────────────────────────────────
if __name__ == '__main__':
    print("==================================================")
    print("🤖 TELEGRAM BOT ACTIVE & CONNECTED TO SUPABASE CLOUD!")
    print("Silakan buka HP lu dan tes chat bot-nya, cuy.")
    print("==================================================")
    bot.infinity_polling()