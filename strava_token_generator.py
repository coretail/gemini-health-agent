import requests
from dotenv import load_model, load_dotenv

# Masukkan data asli dari dashboard Strava lu
CLIENT_ID = "253476"
CLIENT_SECRET = "STRAVA_CLIENT_SECRET"  # <-- Klik 'Show' di web Strava lalu copas ke sini
AUTH_CODE = "STRAVA_AUTH_CODE"

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