import os
import csv
import json
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

# 1. Pastikan Master API KEY lu terpasang di sini
API_KEY = os.getenv("GEMINI_API_KEY")

DB_FOLDER = "database"
CSV_FILE = os.path.join(DB_FOLDER, "nutrition_history.csv")
IMAGE_FILE = "makanan/makanan.jpg"  # <-- Ganti sesuai nama & ekstensi file foto lu!

# 2. Schema Data Nutrisi yang Ketat
class NutritionLog(BaseModel):
    food_name: str = Field(description="Nama makanan atau hidangan yang terdeteksi di foto")
    estimated_calories: int = Field(description="Estimasi total kalori dalam satuan kkal/kcal")
    protein_grams: int = Field(description="Estimasi kandungan protein dalam satuan gram")
    carbs_grams: int = Field(description="Estimasi kandungan karbohidrat dalam satuan gram")
    fat_grams: int = Field(description="Estimasi kandungan lemak dalam satuan gram")

# Inisialisasi Database CSV Nutrisi
if not os.path.exists(CSV_FILE):
    with open(CSV_FILE, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Tanggal", "Nama Makanan", "Kalori (kcal)", "Protein (g)", "Karbo (g)", "Lemak (g)"])

# Cek apakah file fotonya beneran ada di folder
if not os.path.exists(IMAGE_FILE):
    print(f"❌ Waduh, file foto '{IMAGE_FILE}' belum ketemu di folder lu.")
    print("Pastikan lu udah naruh fotonya dan namanya udah sesuai ya!")
    exit()

# 3. Inisialisasi Client & Load Image
client = genai.Client(api_key=API_KEY)

print("==================================================")
print("📸 AI NUTRITION AGENT v1.0 - MULTIMODAL MODE")
print(f"Menganalisis foto makanan: '{IMAGE_FILE}'...")
print("==================================================\n")

try:
    # Membaca file gambar sebagai bytes
    with open(IMAGE_FILE, "rb") as f:
        image_bytes = f.read()

    # 4. Panggil Gemini API dengan menyertakan komponen Teks + Gambar (Multimodal)
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=[
            types.Part.from_bytes(
                data=image_bytes,
                mime_type='image/jpeg', # Ubah ke 'image/png' kalau pake file PNG
            ),
            "Analisis foto makanan ini dan ekstrak informasi nutrisinya secara objektif."
        ],
        config=types.GenerateContentConfig(
            system_instruction=(
                "Kamu adalah AI Agent ahli nutrisi yang bersertifikat. Tugasmu adalah menganalisis foto makanan "
                "yang diberikan pengguna, memperkirakan porsi standar, lalu mengekstrak nama makanan beserta "
                "estimasi kalori dan makronutrisinya ke dalam format JSON yang sangat ketat sesuai schema."
            ),
            response_mime_type="application/json",
            response_schema=NutritionLog,
        ),
    )
    
    # Parse hasil JSON dari Gemini
    data_json = json.loads(response.text)
    tanggal_sekarang = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # 5. Simpan ke Database CSV Lokal
    with open(CSV_FILE, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            tanggal_sekarang,
            data_json.get("food_name"),
            data_json.get("estimated_calories"),
            data_json.get("protein_grams"),
            data_json.get("carbs_grams"),
            data_json.get("fat_grams")
        ])
        
    print("📊 HASIL SCAN & EKSTRAKSI NUTRISI:")
    print(json.dumps(data_json, indent=4))
    print("\n💾 Data berhasil disimpan ke 'nutrition_history.csv'!")
    print("-" * 50)

except Exception as e:
    print(f"❌ Ada error pas nyecan gambar: {e}")