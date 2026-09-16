from py_vapid import Vapid01
from cryptography.hazmat.primitives import serialization
from base64 import urlsafe_b64encode

vapid = Vapid01()
vapid.generate_keys()

# Exportar public key em formato X962 (uncompressed point)
public_bytes = vapid.public_key.public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.UncompressedPoint,
)
public_key_b64 = urlsafe_b64encode(public_bytes).decode().rstrip("=")

# Exportar private key em formato PKCS8 DER
private_bytes = vapid.private_key.private_bytes(
    encoding=serialization.Encoding.DER,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)
private_key_b64 = urlsafe_b64encode(private_bytes).decode().rstrip("=")

print(f"VAPID_PUBLIC_KEY={public_key_b64}")
print(f"VAPID_PRIVATE_KEY={private_key_b64}")
