from cryptography.fernet import Fernet
import os

# Generate this once via Fernet.generate_key() and store in ENV
ENCRYPTION_KEY = os.getenv("NEOPULSE_ENC_KEY") 

cipher = Fernet(ENCRYPTION_KEY.encode()) if ENCRYPTION_KEY else None

def encrypt_secret(secret: str) -> str:
    return cipher.encrypt(secret.encode()).decode()

def decrypt_secret(hashed: str) -> str:
    return cipher.decrypt(hashed.encode()).decode()