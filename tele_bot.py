import os
import csv
from datetime import datetime, timedelta
from PIL import Image
import telebot
from stravalib.client import Client
import google.generativeai as genai
import requests
import threading 
import asyncio
import time

from dotenv import load_dotenv
load_dotenv()
# ─── BUNGKAM WARNING STRAVA ─────────────────────────────────
import logging
logging.getLogger('stravalib').setLevel(logging.CRITICAL)
# ──────────────────────────────────────────────────────────────────

# 1. KONFIGURASI TOKEN & API KEY (Pastikan API Key Gemini Lu Bener)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
STRAVA_CLIENT_ID = int(os.getenv("STRAVA_CLIENT_ID")) if os.getenv("STRAVA_CLIENT_ID") else 253476
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
STRAVA_REFRESH_TOKEN = os.getenv("STRAVA_REFRESH_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")  

# Inisialisasi API Bot & Gemini
bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)
genai.configure(api_key=GEMINI_API_KEY)

DB_FOLDER = "database"
WORKOUT_CSV = os.path.join(DB_FOLDER, "workout_history.csv")
NUTRITION_CSV = os.path.join(DB_FOLDER, "nutrition_history.csv")
os.makedirs(DB_FOLDER, exist_ok=True)

# 2. FUNGSI AUTO-REFRESH TOKEN INI:
def get_strava_access_token():
    """Fungsi sakti untuk menukar REFRESH_TOKEN abadi menjadi ACCESS_TOKEN baru"""
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
            # Ambil access token baru gres yang dikasih server Strava
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
        "<b>🏃‍♂️🤖 HEALTH TRACKER AGENT READY! 🤖🏃‍♂️</b>\n\n"
        "Halo Abdullah Dzaki! Gue adalah asisten AI personal lu.\n\n"
        "<b>Menu Perintah Sakti:</b>\n"
        "▶️ /sync_strava - Tarik otomatis aktivitas lari terakhir dari Strava\n"
        "▶️ /status - Cek status database latihan lu saat ini\n\n"
        "📷 <b>Fitur Vision AI:</b>\n"
        "Kirim foto makanan lu ke sini buat langsung di-analisis!"
    )
    bot.reply_to(message, welcome_text, parse_mode='HTML')


# ─── HANDLER COMMAND /SYNC_STRAVA (ANTI-DUPLIKAT) ───────────────────
# ==================================================================
# 🔄 1. FUNGSI INTI PENARIK DATA (Bisa dipanggil Bot maupun FastAPI)
# ==================================================================
async def strava_sync():
    """Fungsi murni untuk narik data Strava ke CSV tanpa butuh objek Telegram"""
    print("🔄 Memulai sinkronisasi data dari API Strava...")
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
    
    # Cek Duplikat
    if os.path.exists(WORKOUT_CSV):
        with open(WORKOUT_CSV, mode='r', encoding='utf-8') as f:
            reader = csv.reader(f)
            existing_dates = [row[0] for row in reader if row]
            if act_date in existing_dates:
                print(f"⚠️ Data tanggal {act_date} sudah ada.")
                return True # Tetap return true karena data aman

    # Ekstrak Metrik
    act_type = str(latest_run.type).replace("root='", "").replace("'", "")
    act_distance = round(float(latest_run.distance) / 1000, 2) if latest_run.distance else 0.0
    act_duration = int(latest_run.elapsed_time / 60) if latest_run.elapsed_time else 0
    
    avg_hr = int(latest_run.average_heartrate) if getattr(latest_run, 'average_heartrate', None) else "-"
    max_hr = int(latest_run.max_heartrate) if getattr(latest_run, 'max_heartrate', None) else "-"
    elevation = round(float(latest_run.total_elevation_gain), 1) if getattr(latest_run, 'total_elevation_gain', None) else 0.0
    
    avg_pace = "-"
    if getattr(latest_run, 'average_speed', None) and act_type == "Run":
        speed_ms = float(latest_run.average_speed)
        if speed_ms > 0:
            total_minutes = 16.6667 / speed_ms
            avg_pace = f"{int(total_minutes):02d}:{int((total_minutes - int(total_minutes)) * 60):02d}"

    intensity = "Sedang"
    if act_duration > 60: intensity = "Tinggi"
    elif act_duration < 20: intensity = "Rendah"

    # Simpan ke CSV
    file_exists = os.path.exists(WORKOUT_CSV)
    with open(WORKOUT_CSV, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["Tanggal", "Jenis Olahraga", "Durasi (Menit)", "Jarak (KM)", "Intensitas", "Avg HR (BPM)", "Max HR (BPM)", "Elevation (m)", "Avg Pace (min/km)"])
        writer.writerow([act_date, act_type, act_duration, act_distance, intensity, avg_hr, max_hr, elevation, avg_pace])
    
    print("✅ Data Strava berhasil diamankan ke CSV!")
    return True

# ==================================================================
# 🤖 2. HANDLER BOT TELEGRAM (Panggil fungsi di atas pake asyncio)
# ==================================================================
@bot.message_handler(commands=['sync_strava'])
def sync_strava_data(message):
    bot.reply_to(message, "🔄 Menghubungi server Strava... Tunggu bentar ya, cuy!")
    try:
        # Panggil pakai run murni karena nanti bot ini udah punya gerbong thread sendiri
        asyncio.run(strava_sync())
        bot.reply_to(message, "✅ <b>SINKRONISASI STRAVA SUKSES VIA CHAT!</b>\nDatabase lokal udah diperbarui, cuy. Cek dashboard gih!", parse_mode='HTML')
    except Exception as e:
        bot.reply_to(message, f"❌ Error Bot: {e}")

# ==================================================================
# 📅 3. FUNGSI TARIK DATA 1 BULAN (ON-DEMAND)
# ==================================================================
async def strava_sync_1_month():
    """Fungsi khusus buat narik data 30 hari terakhir dari Strava"""
    print("🔄 [Strava API] Menarik data 1 bulan terakhir...")
    fresh_token = get_strava_access_token()
    
    if not fresh_token:
        print("❌ Gagal dapetin akses token Strava.")
        return "Gagal dapet token Strava, cuy."

    # Hitung mundur 30 hari ke belakang (Unix Timestamp)
    tiga_puluh_hari_lalu = datetime.now() - timedelta(days=30)
    
    strava_client = Client(access_token=fresh_token)
    
    # Tarik aktivitas setelah tanggal tersebut
    try:
        activities = list(strava_client.get_activities(after=tiga_puluh_hari_lalu, limit=100))
        
        if not activities:
            return "📭 Kagak ada aktivitas sama sekali dalam 30 hari terakhir."
            
        print(f"✅ Sukses! Dapet {len(activities)} aktivitas dalam sebulan terakhir.")
        
        # Baca dulu database yang ada biar ga duplikat
        existing_dates = []
        if os.path.exists(WORKOUT_CSV):
            with open(WORKOUT_CSV, mode='r', encoding='utf-8') as f:
                reader = csv.reader(f)
                existing_dates = [row[0] for row in reader if row]
                
        file_exists = os.path.exists(WORKOUT_CSV)
        new_count = 0
        
        with open(WORKOUT_CSV, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["Tanggal", "Jenis Olahraga", "Durasi (Menit)", "Jarak (KM)", "Intensitas", "Avg HR (BPM)", "Max HR (BPM)", "Elevation (m)", "Avg Pace (min/km)"])
                
            # Looping dari yang paling lama ke yang terbaru
            for act in reversed(activities):
                act_date = act.start_date_local.strftime("%Y-%m-%d %H:%M")
                
                # Lewati kalau datanya udah ada di CSV
                if act_date in existing_dates:
                    continue
                    
                # Parsing data
                act_type = str(act.type).replace("root='", "").replace("'", "")
                act_distance = round(float(act.distance) / 1000, 2) if act.distance else 0.0
                act_duration = int(act.elapsed_time / 60) if act.elapsed_time else 0
                
                avg_hr = int(act.average_heartrate) if getattr(act, 'average_heartrate', None) else "-"
                max_hr = int(act.max_heartrate) if getattr(act, 'max_heartrate', None) else "-"
                elevation = round(float(act.total_elevation_gain), 1) if getattr(act, 'total_elevation_gain', None) else 0.0
                
                avg_pace = "-"
                if getattr(act, 'average_speed', None) and act_type == "Run":
                    speed_ms = float(act.average_speed)
                    if speed_ms > 0:
                        total_minutes = 16.6667 / speed_ms
                        avg_pace = f"{int(total_minutes):02d}:{int((total_minutes - int(total_minutes)) * 60):02d}"

                intensity = "Sedang"
                if act_duration > 60: intensity = "Tinggi"
                elif act_duration < 20: intensity = "Rendah"

                # Tulis baris baru
                writer.writerow([act_date, act_type, act_duration, act_distance, intensity, avg_hr, max_hr, elevation, avg_pace])
                new_count += 1
                
        return f"✅ Berhasil menarik {len(activities)} aktivitas 1 bulan terakhir.\n💾 Sebanyak {new_count} aktivitas BARU berhasil disimpan ke database tanpa duplikat!"
        
    except Exception as e:
        print(f"❌ Error narik 1 bulan: {repr(e)}")
        return f"❌ Terjadi error: {repr(e)}"

@bot.message_handler(commands=['sync_bulan'])
def sync_strava_bulan(message):
    bot.reply_to(message, "🔄 Menarik riwayat Strava lu 30 hari ke belakang... Agak lama nih, sabar ya cuy!")
    try:
        # Jalankan fungsi async di thread sinkron
        hasil = asyncio.run(strava_sync_1_month())
        bot.reply_to(message, hasil)
    except Exception as e:
        bot.reply_to(message, f"❌ Error eksekusi bot: {e}")

# ─── HANDLER FOTO MAKANAN + CAPTION PORSI (VISION AI) ─────────────────
@bot.message_handler(content_types=['photo'])
def handle_food_image(message):
    bot.reply_to(message, "📸 Foto makanan diterima! Coach Gemini lagi neropong kandungan nutrisinya... Tunggu ya, cuy! 🍳")
    try:
        user_caption = message.caption if message.caption else "Tidak ada catatan porsi tambahan."
        
        # Download Foto
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        temp_dir = "temp_images"
        os.makedirs(temp_dir, exist_ok=True)
        temp_image_path = os.path.join(temp_dir, "temp_food.jpg")
        
        with open(temp_image_path, 'wb') as new_file:
            new_file.write(downloaded_file)
            
        # Olah Gambar pake PIL
        img = Image.open(temp_image_path)
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        prompt = f"""
        Analisis foto makanan ini dengan mempertimbangkan catatan porsi dari user berikut: "{user_caption}".
        Berikan jawaban dalam bentuk format teks biasa seperti ini (wajib persis, jangan pakai format tebal bintang-bintang atau markdown):
        Nama Makanan: [Isi nama makanan]
        Kalori: [Angka] kkal
        Protein: [Angka] gram
        Karbohidrat: [Angka] gram
        Lemak: [Angka] gram
        Keterangan: [Berikan analisis singkat apakah makanan ini bagus untuk recovery protein latihan olahraga marathon atau tidak]
        """
        
        response = model.generate_content([prompt, img])
        hasil_analisis = response.text
        
        laporan_text = (
            f"📝 <b>HASIL ANALISIS NUTRISI AI</b> 📝\n\n"
            f"✍️ <b>Catatan Porsi Lu:</b> <i>{user_caption}</i>\n\n"
            f"{hasil_analisis}"
        )
        bot.reply_to(message, laporan_text, parse_mode='HTML')
        
        # Simpan ke Database Nutrisi
        file_exists = os.path.exists(NUTRITION_CSV)
        waktu_makan = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(NUTRITION_CSV, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["Tanggal", "Catatan User", "Hasil Analisis Mentah"])
            writer.writerow([waktu_makan, user_caption, hasil_analisis.replace('\n', ' | ')])
            
        img.close()

        if os.path.exists(temp_image_path):
            os.remove(temp_image_path)
            
    except Exception as e:
        bot.reply_to(message, f"❌ Gagal memproses foto makanan, cuy. Error: {e}")

# ─── RUN SERVER BOT STANDBY ─────────────────────────────────────────
if __name__ == '__main__':
    print("==================================================")
    print("🤖 TELEGRAM BOT ACTIVE & STANDBY IN BACKEND!")
    print("Silakan buka HP lu dan tes chat bot-nya, cuy.")
    print("==================================================")
    bot.infinity_polling()