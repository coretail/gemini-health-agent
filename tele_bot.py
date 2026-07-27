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

# State lock untuk mencegah konflik sinkronisasi paralel
STRAVA_SYNC_LOCK = False

# Skema JSON Gizi untuk Validasi Data Gemini
class NutritionLog(BaseModel):
    kalori: int = Field(description="Estimasi total kalori (kkal)")
    protein: int = Field(description="Estimasi protein (gram)")
    karbo: int = Field(description="Estimasi karbohidrat (gram)")
    lemak: int = Field(description="Estimasi lemak (gram)")
    keterangan: str = Field(description="Analisis singkat maksimal 2 kalimat apakah makanan ini bagus untuk recovery latihan marathon.")


# 2. FUNGSI AUTO-REFRESH TOKEN STRAVA (SMART VERSION)
def get_strava_access_token(refresh_token_input=None):
    """
    Mengambil access token baru menggunakan refresh token.
    Jika refresh_token_input tidak diberikan, pakai STRAVA_REFRESH_TOKEN dari .env
    """
    token_to_use = refresh_token_input if refresh_token_input else STRAVA_REFRESH_TOKEN
    
    if not token_to_use:
        print("❌ Error: Tidak ada refresh token yang tersedia.")
        return None

    url = "https://www.strava.com/oauth/token"
    payload = {
        'client_id': STRAVA_CLIENT_ID,
        'client_secret': STRAVA_CLIENT_SECRET,
        'refresh_token': token_to_use,
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
    telegram_id = str(message.from_user.id)
    welcome_text = (
        "<b>🏃‍♂️🤖 HEALTH TRACKER AGENT</b>\n\n"
        f"Telegram ID kamu: <code>{telegram_id}</code>\n"
        "Copy ID di atas ke halaman Profil di web untuk menghubungkan akun!\n\n"
        "<b>Menu:</b>\n"
        "▶️ /sync_strava - Sync aktivitas terbaru\n"
        "▶️ /sync_bulan - Sync 30 hari terakhir\n"
        "📷 Kirim foto makanan untuk analisis nutrisi!"
    )
    bot.reply_to(message, welcome_text, parse_mode='HTML')


# ==================================================================
# 🔄 1. FUNGSI INTI PENARIK DATA STRAVA (REFACTORED)
# ==================================================================
async def strava_sync(user_id=None, refresh_token=None):
    """Fungsi sakti untuk narik data Strava langsung ke Supabase Cloud"""
    print(f"🔄 Memulai sinkronisasi data Strava... (User ID: {user_id or 'Default'})")
    
    # Gunakan refresh token spesifik atau fallback ke .env
    fresh_token = get_strava_access_token(refresh_token)
    if not fresh_token:
        return False
        
    strava_client = Client(access_token=fresh_token)
    try:
        activities = list(strava_client.get_activities(limit=5))
    except Exception as e:
        print(f"❌ Gagal ambil aktivitas dari Strava: {e}")
        return False
    
    if not activities:
        print("📭 Kagak ada riwayat aktivitas di Strava.")
        return False
        
    latest_run = activities[0]
    act_date = latest_run.start_date_local.strftime("%Y-%m-%d %H:%M")
    
    # Cek Duplikat di Supabase
    try:
        query_path = f"/workouts?order=tanggal.desc&limit=10"
        if user_id:
            query_path += f"&user_id=eq.{user_id}"
            
        res_check = supabase_client.get(query_path)
        existing_runs = res_check.json()
        existing_dates = [str(r.get('tanggal', '')).replace('T', ' ')[:16] for r in existing_runs]
        
        if act_date in existing_dates:
            print(f"⚠️ Data tanggal {act_date} sudah aman tersimpan.")
            return True
    except Exception as e:
        print(f"⚠️ Gagal melakukan pengecekan duplikat di cloud: {e}")

    # Ekstrak Metrik Utama
    act_type = str(latest_run.type).replace("root='", "").replace("'", "")
    nama_sesi = str(latest_run.name) if getattr(latest_run, 'name', None) else "Untitled Run"
    act_duration = int(latest_run.elapsed_time / 60) if latest_run.elapsed_time else 0
    avg_hr = int(latest_run.average_heartrate) if getattr(latest_run, 'average_heartrate', None) else None
    # Kalkulasi jarak dalam KM
    jarak_km = round(float(latest_run.distance) / 1000, 2) if getattr(latest_run, 'distance', None) else 0.0
    
    avg_pace = None
    if getattr(latest_run, 'average_speed', None) and act_type == "Run":
        speed_ms = float(latest_run.average_speed)
        if speed_ms > 0:
            total_minutes = 16.6667 / speed_ms
            avg_pace = f"{int(total_minutes):02d}:{int((total_minutes - int(total_minutes)) * 60):02d}"

    # Pasang payload sesuai kolom PostgreSQL
    payload_workout = {
        "tanggal": act_date,
        "nama_sesi": nama_sesi,
        "jenis_olahraga": act_type,
        "durasi_menit": float(act_duration),
        "avg_hr": avg_hr,
        "avg_pace": avg_pace,
        "jarak": jarak_km
    }
    
    # Tambahkan user_id ke payload jika tersedia
    if user_id:
        payload_workout["user_id"] = user_id
    
    try:
        res_post = supabase_client.post("/workouts", json=payload_workout, headers={"Prefer": "return=representation"})
        print(f"🔍 Status: {res_post.status_code}")
        print(f"🔍 Response: {res_post.text}")
        if res_post.status_code not in [200, 201]:
            print(f"❌ Gagal simpan workout utama: {res_post.text}")
            return False
            
        workout_id = None
        # Supabase biasanya mengembalikan data yang diinsert jika pakai header Prefer: return=representation
        # Tapi kita coba ambil dari response JSON jika tersedia
        try:
            res_data = res_post.json()
            if isinstance(res_data, list) and len(res_data) > 0:
                workout_id = res_data[0].get("id")
            elif isinstance(res_data, dict):
                workout_id = res_data.get("id")
        except:
            pass

        # Jika ID belum dapat, kita fetch manual lari terakhir user ini
        if not workout_id and user_id:
            res_latest = supabase_client.get(f"/workouts?user_id=eq.{user_id}&order=tanggal.desc&limit=1")
            latest_data = res_latest.json()
            if latest_data:
                workout_id = latest_data[0].get("id")

        print(f"✅ Data Strava utama berhasil (ID: {workout_id})")

        # --- SYNC SPLITS (KM) ---
        if act_type == "Run" and workout_id:
            try:
                detail_activity = strava_client.get_activity(latest_run.id)
                if hasattr(detail_activity, 'splits_metric') and detail_activity.splits_metric:
                    splits_payload = []
                    for idx, split in enumerate(detail_activity.splits_metric, 1):
                        # Kalkulasi Pace MM:SS dari m/s secara presisi
                        s_pace = "-"
                        avg_speed = getattr(split, 'average_speed', None)
                        if avg_speed is not None and float(avg_speed) > 0:
                            total_seconds = 1000.0 / float(avg_speed)
                            m_min, s_sec = divmod(total_seconds, 60)
                            s_pace = f"{int(m_min):02d}:{int(s_sec):02d}"
                        
                        splits_payload.append({
                            "workout_id": workout_id,
                            "lap_index": idx,
                            "split_distance": round(float(split.distance) / 1000, 2) if getattr(split, 'distance', None) is not None else 0.0,
                            "split_elapsed_time": int(split.elapsed_time) if getattr(split, 'elapsed_time', None) is not None else 0,
                            "split_pace": s_pace,
                            "avg_hr": int(split.average_heartrate) if getattr(split, 'average_heartrate', None) is not None else None
                        })
                    
                    if splits_payload:
                        async with httpx.AsyncClient(base_url=f"{SUPABASE_URL or ''}/rest/v1", headers=supabase_headers) as client:
                            res_splits = await client.post("/workout_splits", json=splits_payload)
                        print(f"✅ Berhasil simpan {len(splits_payload)} splits ke cloud secara asinkron! Status: {res_splits.status_code}")
                
                # 🔥 LANGSUNG TRIGER AGENT AI UNTUK EVALUASI LATIHAN DAN SIMPAN KE DATABASE
                try:
                    await generate_and_save_workout_evaluation(user_id, workout_id)
                except Exception as e_agent:
                    print(f"⚠️ Gagal generate/simpan evaluasi otomatis paska sync lari: {e_agent}")
            except Exception as e_split:
                print(f"⚠️ Gagal tarik/simpan detail splits secara asinkron: {e_split}")

        return True
    except Exception as e:
        print(f"❌ Gagal mengirim data workout ke Supabase: {e}")
        return False


@bot.message_handler(commands=['sync_strava'])
def sync_strava_data(message):
    global STRAVA_SYNC_LOCK
    if STRAVA_SYNC_LOCK:
        bot.reply_to(message, "⚠️ <b>Sabar cuy!</b> Proses sinkronisasi Strava sedang berjalan. Tunggu sampai selesai ya!", parse_mode='HTML')
        return

    STRAVA_SYNC_LOCK = True
    bot.reply_to(message, "🔄 Menghubungi server Strava... Tunggu bentar ya, cuy!")
    try:
        # Untuk bot pribadi, kita coba ambil profil pertama dari Supabase untuk dapat User ID-nya
        u_id, r_token = None, None
        try:
            telegram_id = str(message.from_user.id)
            res_p = supabase_client.get(f"/profiles?telegram_id=eq.{telegram_id}")
            p_data = res_p.json()
            if not p_data:
                bot.reply_to(message, "⚠️ Akun kamu belum terhubung! Daftar dulu di web, lalu masukkan Telegram ID kamu di halaman Profil.")
                STRAVA_SYNC_LOCK = False
                return
            u_id = p_data[0].get("id")
            r_token = p_data[0].get("strava_refresh_token")
        except:
            pass

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(strava_sync(u_id, r_token))
        finally:
            loop.close()
        bot.reply_to(message, "✅ <b>SINKRONISASI STRAVA SUKSES VIA CHAT!</b>\nDatabase cloud Supabase udah diperbarui, cuy. Cek dashboard gih!", parse_mode='HTML')
    except Exception as e:
        bot.reply_to(message, f"❌ Error Bot: {e}")
    finally:
        STRAVA_SYNC_LOCK = False


# ==================================================================
# 📅 2. FUNGSI TARIK DATA 1 BULAN (BULK SYNC TO CLOUD)
# ==================================================================
async def strava_sync_1_month(user_id=None, refresh_token=None):
    print(f"🔄 [Bulk] Menarik data 1 bulan... (User ID: {user_id or 'Default'})")
    
    
    fresh_token = get_strava_access_token(refresh_token)
    if not fresh_token:
        return "Gagal dapet token Strava, cuy."

    tiga_puluh_hari_lalu = datetime.now() - timedelta(days=30)
    strava_client = Client(access_token=fresh_token)
    
    try:
        activities = list(strava_client.get_activities(after=tiga_puluh_hari_lalu, limit=100))
        if not activities:
            return "📭 Kagak ada aktivitas sama sekali dalam 30 hari terakhir."
            
        # Ambil data yang sudah ada untuk filter duplikat
        query_path = f"/workouts?select=tanggal"
        if user_id:
            query_path += f"&user_id=eq.{user_id}"
            
        res_all = supabase_client.get(query_path)
        existing_dates = [str(r.get('tanggal', '')).replace('T', ' ')[:16] for r in res_all.json()]
        
        records_w = []
        new_count = 0
        for act in reversed(activities):
            print(f"🔍 Nama: {act.name} | Tanggal: {act.start_date_local}")
        for act in reversed(activities):
            act_date = act.start_date_local.strftime("%Y-%m-%d %H:%M")
            if act_date in existing_dates:
                continue
                
            act_type = str(act.type).replace("root='", "").replace("'", "")
            nama_sesi = str(act.name) if getattr(act, 'name', None) else "Untitled Run"
            act_duration = int(act.elapsed_time / 60) if act.elapsed_time else 0
            avg_hr = int(act.average_heartrate) if getattr(act, 'average_heartrate', None) else None
            jarak_km = round(float(act.distance) / 1000, 2) if getattr(act, 'distance', None) else 0.0
            
            avg_pace = None
            if getattr(act, 'average_speed', None) and act_type == "Run":
                speed_ms = float(act.average_speed)
                if speed_ms > 0:
                    total_minutes = 16.6667 / speed_ms
                    avg_pace = f"{int(total_minutes):02d}:{int((total_minutes - int(total_minutes)) * 60):02d}"

            payload = {
                "tanggal": act_date,
                "nama_sesi": nama_sesi,
                "jenis_olahraga": act_type,
                "durasi_menit": float(act_duration),
                "avg_hr": avg_hr,
                "avg_pace": avg_pace,
                "jarak": jarak_km
            }
            if user_id:
                payload["user_id"] = user_id
                
            records_w.append(payload)
            new_count += 1
            
            
        if records_w:
            # Kita post satu-satu atau bulk? Kalau bulk kita susah dapet ID per baris untuk splits.
            # Agar simpel & akurat untuk splits, kita post satu-satu saja di dalam loop ini
            # Tapi untuk efisiensi, kita gunakan payload records_w yang sudah ada dan modifikasi loop di atas
            pass

        # RE-LOGIC: Loop ulang untuk insert per baris agar bisa narik splits
        new_count_final = 0
        for act in reversed(activities):
            act_date = act.start_date_local.strftime("%Y-%m-%d %H:%M")
            if act_date in existing_dates:
                continue
                
            act_type = str(act.type).replace("root='", "").replace("'", "")
            
            # PEMBERSIH BUG: Nama sesi sekarang di-update tiap iterasi biar gak seragam semua
            nama_sesi = str(act.name) if getattr(act, 'name', None) else "Untitled Run"
            
            act_duration = int(act.elapsed_time / 60) if act.elapsed_time else 0
            avg_hr = int(act.average_heartrate) if getattr(act, 'average_heartrate', None) else None
            jarak_km = round(float(act.distance) / 1000, 2) if getattr(act, 'distance', None) else 0.0
            
            avg_pace = None
            if getattr(act, 'average_speed', None) and act_type == "Run":
                speed_ms = float(act.average_speed)
                if speed_ms > 0:
                    total_minutes = 16.6667 / speed_ms
                    avg_pace = f"{int(total_minutes):02d}:{int((total_minutes - int(total_minutes)) * 60):02d}"

            payload = {
                "tanggal": act_date,
                "nama_sesi": nama_sesi,
                "jenis_olahraga": act_type,
                "durasi_menit": float(act_duration),
                "avg_hr": avg_hr,
                "avg_pace": avg_pace,
                "jarak": jarak_km
            }
            if user_id:
                payload["user_id"] = user_id
            
            # Insert Workout Utama
            res_p = supabase_client.post("/workouts", json=payload, headers={"Prefer": "return=representation"})
            if res_p.status_code in [200, 201]:
                new_count_final += 1
                # Ambil ID untuk splits
                w_id = None
                try:
                    r_json = res_p.json()
                    w_id = r_json[0].get("id") if isinstance(r_json, list) else r_json.get("id")
                except: pass

                if not w_id and user_id:
                    # Fallback ambil ID berdasarkan tanggal & user
                    res_f = supabase_client.get(f"/workouts?user_id=eq.{user_id}&tanggal=eq.{act_date}")
                    f_data = res_f.json()
                    if f_data: w_id = f_data[0].get("id")

                # Tarik Splits jika Lari
                if act_type == "Run" and w_id:
                    try:
                        detail = strava_client.get_activity(act.id)
                        if hasattr(detail, 'splits_metric') and detail.splits_metric:
                            s_list = []
                            for idx, s in enumerate(detail.splits_metric, 1):
                                sp_pace = "-"
                                avg_speed = getattr(s, 'average_speed', None)
                                if avg_speed is not None and float(avg_speed) > 0:
                                    total_seconds = 1000.0 / float(avg_speed)
                                    m_min, s_sec = divmod(total_seconds, 60)
                                    sp_pace = f"{int(m_min):02d}:{int(s_sec):02d}"
                                
                                s_list.append({
                                    "workout_id": w_id,
                                    "lap_index": idx,
                                    "split_distance": round(float(s.distance) / 1000, 2) if getattr(s, 'distance', None) is not None else 0.0,
                                    "split_elapsed_time": int(s.elapsed_time) if getattr(s, 'elapsed_time', None) is not None else 0,
                                    "split_pace": sp_pace,
                                    "avg_hr": int(s.average_heartrate) if getattr(s, 'average_heartrate', None) is not None else None
                                })
                            if s_list:
                                async with httpx.AsyncClient(base_url=f"{SUPABASE_URL or ''}/rest/v1", headers=supabase_headers) as client:
                                    await client.post("/workout_splits", json=s_list)
                        
                        # 🔥 TRIGER AGENT AI EVALUASI SETIAP WORKOUT BARU SUKSES DI-BULK SINKRONISASI
                        try:
                            await generate_and_save_workout_evaluation(user_id, w_id)
                        except Exception as e_agent:
                            print(f"⚠️ Gagal generate/simpan evaluasi otomatis paska sync bulk lari: {e_agent}")
                    except Exception as e_split:
                        print(f"⚠️ Gagal tarik/simpan detail splits bulk: {e_split}")
            
        return f"💾 Sebanyak {new_count_final} data baru (beserta splits) berhasil di-push ke cloud Supabase!"
    except Exception as e:
        return f"❌ Terjadi error migrasi bulk: {repr(e)}"


@bot.message_handler(commands=['sync_bulan'])
def sync_strava_bulan(message):
    global STRAVA_SYNC_LOCK
    if STRAVA_SYNC_LOCK:
        bot.reply_to(message, "⚠️ <b>Sabar cuy!</b> Proses sinkronisasi Strava sedang berjalan. Tunggu sampai selesai ya!", parse_mode='HTML')
        return

    STRAVA_SYNC_LOCK = True
    bot.reply_to(message, "🔄 Menarik riwayat Strava lu 30 hari ke belakang... Harap sabar ya cuy!")
    try:
        # Ambil user_id dan token dari profil pertama (untuk bot pribadi)
        u_id, r_token = None, None
        try:
            telegram_id = str(message.from_user.id)
            res_p = supabase_client.get(f"/profiles?telegram_id=eq.{telegram_id}")
            p_data = res_p.json()
            if not p_data:
                bot.reply_to(message, "⚠️ Akun kamu belum terhubung! Daftar dulu di web, lalu masukkan Telegram ID kamu di halaman Profil.")
                STRAVA_SYNC_LOCK = False
                return
            u_id = p_data[0].get("id")
            r_token = p_data[0].get("strava_refresh_token")
        except:
            pass

        # sync_strava_data
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            hasil = loop.run_until_complete(strava_sync_1_month(u_id, r_token))
        finally:
            loop.close()
            
        bot.reply_to(message, hasil)

    except Exception as e:
        bot.reply_to(message, f"❌ Error eksekusi bot: {e}")
    finally:
        STRAVA_SYNC_LOCK = False



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
        user_id = profile.get("id")
        if user_id:
            payload_nutrition["user_id"] = user_id
            
        supabase_client.post("/nutrition", json=payload_nutrition)
        print("✅ Log makanan dari Telegram berhasil ter-upload ke Supabase!")

        if os.path.exists(temp_image_path):
            os.remove(temp_image_path)
            
    except Exception as e:
        bot.reply_to(message, f"❌ Gagal memproses foto makanan, cuy. Error: {e}")

# ==================================================================
# 🤖 3. FUNGSI GENERATE EVALUASI AGENT AI & SIMPAN KE DATABASE
# ==================================================================
async def generate_and_save_workout_evaluation(user_id, workout_id):
    """Fungsi mandiri untuk menembak Agent AI dan menyimpan evaluasi lari ke database Supabase"""
    print(f"🤖 [Agent Evaluasi] Mulai menembak Agent AI untuk User: {user_id}, Workout: {workout_id}...")
    try:
        # 1. Ambil data profil user
        res_p = supabase_client.get(f"/profiles?id=eq.{user_id}")
        p_data = res_p.json()
        profile = p_data[0] if p_data else {}
        
        # 2. Ambil histori workout user
        res_w = supabase_client.get(f"/workouts?user_id=eq.{user_id}&order=tanggal.asc")
        workout_records = res_w.json()
        
        if not workout_records:
            print("⚠️ Tidak ada riwayat workout untuk meracik evaluasi.")
            return
            
        # Konversi ke DataFrame Pandas untuk memproses ACWR, Readiness, & Best Effort
        import pandas as pd
        df_supabase_w = pd.DataFrame(workout_records)
        df_workout = df_supabase_w.rename(columns={
            'tanggal': 'Tanggal',
            'jenis_olahraga': 'Jenis Olahraga',
            'durasi_menit': 'Durasi (Menit)',
            'avg_hr': 'Avg HR (BPM)',
            'avg_pace': 'Avg Pace (min/km)',
            'jarak': 'Jarak'
        })
        
        # Ambil 7 sesi terakhir untuk grafik/beban
        df_graph = df_workout.tail(7)
        dates_graph = []
        loads_graph = []
        for _, row in df_graph.iterrows():
            durasi = float(row.get('Durasi (Menit)', 0))
            hr = float(row.get('Avg HR (BPM)', 0)) if pd.notna(row.get('Avg HR (BPM)')) else 130
            training_load = round((durasi * hr) / 100, 1)
            dates_graph.append(str(row.get('Tanggal', '-')).split()[0].split('T')[0])
            loads_graph.append(training_load)
            
        load_history_str = ", ".join([f"{d}: Load {l}" for d, l in zip(dates_graph, loads_graph)])
        
        # Filter lari
        df_run = df_workout[df_workout['Jenis Olahraga'].str.lower() == 'run'].copy()
        
        # === KALKULASI ACWR ===
        df_acwr = df_workout.copy()
        df_acwr['Date'] = pd.to_datetime(df_acwr['Tanggal']).dt.strftime('%Y-%m-%d')
        df_acwr['HR_Clean'] = pd.to_numeric(df_acwr['Avg HR (BPM)'], errors='coerce').fillna(130)
        df_acwr['Dur_Clean'] = pd.to_numeric(df_acwr['Durasi (Menit)'], errors='coerce').fillna(0)
        df_acwr['Daily_Load'] = (df_acwr['Dur_Clean'] * df_acwr['HR_Clean']) / 100
        
        daily_loads_dict = df_acwr.groupby('Date')['Daily_Load'].sum().to_dict()
        
        today_date = datetime.now()
        dates_35 = [(today_date - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(34, -1, -1)]
        loads_35 = [daily_loads_dict.get(d, 0.0) for d in dates_35]
        
        acwr_vals = []
        for i in range(28, 35):
            acute_load = sum(loads_35[i-6 : i+1]) / 7     
            chronic_load = sum(loads_35[i-27 : i+1]) / 28 
            acwr_score = round(acute_load / chronic_load, 2) if chronic_load > 0 else 0.0
            acwr_vals.append(acwr_score)
            
        latest_acwr = acwr_vals[-1] if acwr_vals else 0.0
        
        # Ambil lari terakhir
        latest_pace = "-"
        latest_hr = "-"
        waktu_terakhir_lari = "Belum ada data"
        if not df_run.empty:
            latest_run = df_run.iloc[-1]
            latest_pace = latest_run.get('Avg Pace (min/km)', '-')
            latest_hr = latest_run.get('Avg HR (BPM)', '-')
            waktu_terakhir_lari = str(latest_run.get('Tanggal', 'Hari Ini')).replace('T', ' ')[:16]
            
        # Readiness
        yesterday_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        yesterday_load = daily_loads_dict.get(yesterday_date, 0.0)
        if latest_acwr > 1.5 or yesterday_load > 150:
            readiness_score = 45
        elif latest_acwr > 1.3 or yesterday_load > 90:
            readiness_score = 70
        else:
            readiness_score = 100
            
        if readiness_score >= 80:
            readiness_msg = "🔥 Kondisi Prima! Otot dan sendi udah recovery. Gaspol buat Interval atau Long Run hari ini."
        elif readiness_score >= 50:
            readiness_msg = "⚠️ Recovery setengah jalan. Hindari speed session, mending hajar Easy Run santai aja di Zone 2."
        else:
            readiness_msg = "🛑 Otot masih fatigue & butuh istirahat. Wajib Rest Day atau Active Recovery hari ini!"
            
        # Riegel Formula Best Effort
        def pace_to_seconds(pace_str):
            if not pace_str or pace_str == "-":
                return float('inf')
            try:
                parts = str(pace_str).split(':')
                if len(parts) == 2:
                    return int(parts[0]) * 60 + int(parts[1])
            except:
                pass
            return float('inf')

        best_run = None
        best_pace_secs = float('inf')
        df_run_history = df_run.copy() if not df_run.empty else pd.DataFrame()
        if not df_run_history.empty:
            df_run_history = df_run_history.tail(30)
            df_run_5km = df_run_history[df_run_history['Jarak'] >= 5.0]
            for idx_h, row_h in df_run_5km.iterrows():
                p_str = row_h.get('Avg Pace (min/km)')
                secs = pace_to_seconds(p_str)
                if secs < best_pace_secs:
                    best_pace_secs = secs
                    best_run = row_h

        if best_run is not None:
            acuan_nama = "Aktivitas Terbaik (Best Effort >= 5 KM)"
            d1 = float(best_run.get('Jarak', 10.0))
            t1 = float(best_run.get('Durasi (Menit)', 60.0))
            best_pace_val = best_run.get('Avg Pace (min/km)', '-')
            best_hr_val = best_run.get('Avg HR (BPM)', '-')
        else:
            acuan_nama = "Aktivitas Terbaru (Fallback)"
            if not df_run.empty:
                latest_run_fb = df_run.iloc[-1]
                d1 = float(latest_run_fb.get('Jarak', 10.0))
                t1 = float(latest_run_fb.get('Durasi (Menit)', 60.0))
                best_pace_val = latest_run_fb.get('Avg Pace (min/km)', '-')
                best_hr_val = latest_run_fb.get('Avg HR (BPM)', '-')
            else:
                acuan_nama = "Default Fallback"
                d1 = 10.0
                t1 = 60.0
                best_pace_val = "05:00"
                best_hr_val = "150"

        try:
            t2 = t1 * ((42.195 / d1) ** 1.07)
            total_seconds_t2 = int(t2 * 60)
            hours_t2 = total_seconds_t2 // 3600
            minutes_t2 = (total_seconds_t2 % 3600) // 60
            seconds_t2 = total_seconds_t2 % 60
            predicted_marathon_time = f"{hours_t2:02d}:{minutes_t2:02d}:{seconds_t2:02d}"
        except Exception as e_riegel:
            print(f"⚠️ Gagal kalkulasi Riegel: {e_riegel}")
            predicted_marathon_time = "Gagal dihitung"

        # Race Countdown
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

ACUAN PREDIKSI MARATHON (FORMULA RIEGEL):
- Tipe Acuan: {acuan_nama}
- Jarak Acuan (D1): {d1:.2f} KM
- Waktu Acuan (T1): {t1:.1f} Menit (Pace {best_pace_val} min/km, HR {best_hr_val} BPM)
- Jarak Target (D2): 42.195 KM (Full Marathon)
- Hasil Prediksi Waktu Finish Riegel (T2): {predicted_marathon_time}

TUGAS KAMU (Maksimal 4 kalimat padat):
1. Evaluasi kondisi beban latihannya (ACWR & Readiness) hari ini.
2. Ingatkan soal "{sisa_hari_teks}" agar dia bisa mengatur pacing program latihannya.
3. Sebutkan hasil "Prediksi Realistis Finish Time" ({predicted_marathon_time}) yang dihitung menggunakan Formula Riegel dari {acuan_nama} miliknya, lalu bandingkan secara langsung dengan target {profile.get('target_waktu')}, apakah dia *on-track* atau harus memperbaiki sesuatu?
"""
        
        # Panggil Gemini API v2.5 untuk evaluasi
        resp_eval = ai_client.models.generate_content(model='gemini-2.5-flash', contents=prompt_evaluasi_lari)
        evaluasi_hari_ini = resp_eval.text.strip()
        print(f"✅ Evaluasi baru berhasil diracik: '{evaluasi_hari_ini}'")
        
        # Simpan ke Supabase!
        res_patch = supabase_client.patch(f"/workouts?id=eq.{workout_id}", json={
            "agent_evaluation": evaluasi_hari_ini,
            "eval_timestamp": datetime.now().isoformat()
        })
        print(f"💾 Status Simpan Evaluasi ke Database: {res_patch.status_code}")
        
    except Exception as e:
        print(f"❌ Gagal generate/simpan evaluasi otomatis: {e}")

# ─── RUN SERVER BOT STANDBY ─────────────────────────────────────────
if __name__ == '__main__':
    print("==================================================")
    print("🤖 TELEGRAM BOT ACTIVE & CONNECTED TO SUPABASE CLOUD!")
    print("Silakan buka HP lu dan tes chat bot-nya, cuy.")
    print("==================================================")
    bot.infinity_polling()