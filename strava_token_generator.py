import requests
from dotenv import load_model, load_dotenv

# Masukkan data asli dari dashboard Strava lu
CLIENT_ID = "253476"
CLIENT_SECRET = "311253551609da2f2faeeab2a08a3218e02a2cfb"  # <-- Klik 'Show' di web Strava lalu copas ke sini
AUTH_CODE = "28b22a23c7ba1fba70f9f29e00c351677066ba17"

print("🔄 Menukarkan kode authorization menjadi Access Token...")

response = requests.post(
    url='https://www.strava.com/oauth/token',
    data={
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'code': AUTH_CODE,
        'grant_type': 'authorization_code'
    }
)

if response.status_code == 200:
    data = response.json()
    print("\n✅ BERHASIL TUKAR TOKEN!")
    print(f"Token Baru Lu (Ganti di strava_test.py) : {data['access_token']}")
    print(f"Refresh Token (Simpan buat nanti)     : {data['refresh_token']}")
else:
    print(f"\n❌ Gagal menukar token. Error: {response.text}")