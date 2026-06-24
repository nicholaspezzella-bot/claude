"""
One-time helper: generate an RSA-2048 key pair for Kalshi API authentication.
Upload the public key to https://kalshi.com/account/api and note the UUID key ID.
Store the private key path + UUID in your .env file.

Usage: python keygen.py
"""

from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

private_pem = key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)
public_pem = key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
)

Path("kalshi_private_key.pem").write_bytes(private_pem)
Path("kalshi_public_key.pem").write_bytes(public_pem)

print("Generated kalshi_private_key.pem and kalshi_public_key.pem")
print("Upload kalshi_public_key.pem at https://kalshi.com/account/api")
print("Set KALSHI_PRIVATE_KEY_PATH=./kalshi_private_key.pem in .env")
print("Set KALSHI_API_KEY_ID to the UUID shown after uploading the key")
