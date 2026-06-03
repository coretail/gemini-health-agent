import os
import csv
import json
from google import genai
from google.genai import types

from dotenv import load_model, load_dotenv

# 1. Pastikan API KEY lu terpasang di sini
API_KEY = "AQ.Ab8RN6ICT6ghrMxfmZMD4JzXMyp8s4vNUmKuFvUfoyKO9fj2vA"

DB_FOLDER = "database"
CSV_FILE = os.path.join(DB_FOLDER, "workout_history.csv")

# Inisialisasi Gemini Client
client = genai.Client(api_key=API_KEY)

print("==================================================")
print("🏃‍♂️ AI WEEKLY COACH AGENT v1.2 - INTERACTIVE MODE")
print("Membaca riwayat latihan lu dari database...")
print("==================================================\n")

if not os.path.exists(CSV_FILE):
    print("❌ Waduh, file 'workout_history.csv' belum ketemu nih.")
    print("Coba jalankan 'python app.py' dulu buat ngisi data olahraga lu!")
    exit()

# Baca data dari file CSV
all_workouts = []
with open(CSV_FILE, mode='r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        all_workouts.append(row)

if not all_workouts:
    print("📭 Database CSV lu masih kosong, cuy. Yuk olahraga dulu!")
    exit()

# Format data CSV jadi teks
formatted_history = ""
for index, item in enumerate(all_workouts, 1):
    formatted_history += (
        f"{index}. Tanggal: {item['Tanggal']} | "
        f"Olahraga: {item['Jenis Olahraga']} | "
        f"Durasi: {item['Durasi (Menit)']} Menit | "
        f"Jarak: {item['Jarak (KM)']} KM | "
        f"Intensitas: {item['Intensitas']}\n"
    )

# 2. Bikin Sesi Chat Interaktif dengan System Instruction
coach_session = client.chats.create(
    model='gemini-2.5-flash',
    config=types.GenerateContentConfig(
        system_instruction=(
            "Kamu adalah seorang Pelatih Kebugaran (Health & Fitness Coach) yang sangat berpengalaman, "
            "bijak, suportif, dan menggunakan bahasa santai ala anak muda Jakarta (gunakan kata gue-lu). "
            "Kamu memegang data riwayat olahraga pengguna dan siap memberikan evaluasi, menjawab pertanyaan, "
            "serta membantu memodifikasi jadwal latihan mereka agar aman dari cedera."
        )
    )
)

# 3. Kirim data awal (Context Injection) sebagai kick-off evaluasi
pemicu_awal = f"""
Berikut adalah riwayat olahraga saya selama beberapa hari terakhir:
{formatted_history}

Tolong berikan evaluasi pelatih yang mendalam, meliputi:
1. Ringkasan singkat total aktivitas.
2. Analisis variasi & intensitas latihan.
3. Rekomendasi jadwal concreto buat minggu depan.
"""

print("Coach Gemini sedang menganalisis data awal lu... ⏳\n")
respons_awal = coach_session.send_message(pemicu_awal)

print("======= EVALUASI AWAL COACH =======")
print(respons_awal.text)
print("====================================\n")

print("🤖 Coach siap diajak diskusi! Lu bisa nanya/sanggah jadwal di atas.")
print("Ketik 'keluar' atau 'exit' untuk menyudahi konsultasi.\n")

# 4. Looping Obrolan/Tanya Jawab
while True:
    user_tanya = input("💬 Lu (Tanya Coach) : ")
    
    if user_tanya.lower() in ['keluar', 'exit']:
        print("\n👋 Sip, latihan yang konsisten ya bro/sis! Sampai ketemu minggu depan.")
        break
        
    if not user_tanya.strip():
        continue
        
    print("Coach lagi mikir... ⏳")
    
    try:
        # Kirim chat lanjutan ke sesi yang sama
        respons_lanjutan = coach_session.send_message(user_tanya)
        print(f"\n🏃‍♂️ Coach Gemini : {respons_lanjutan.text}")
        print("-" * 50 + "\n")
        
    except Exception as e:
        print(f"❌ Ada error nih: {e}\n")