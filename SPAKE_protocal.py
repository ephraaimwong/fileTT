from fastapi import WebSocket
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os

async def send_encrypted_file(
    ws: WebSocket,
    file_path: str,
    aesgcm: AESGCM,
    nonce: bytes,
    chunk_size: int = 64 * 1024
):
    """
    Encrypts and streams the contents of `file_path` over the WebSocket.
    Each chunk is encrypted with the same nonce (you may rotate per chunk if you like).
    """
    # 1) Send filename metadata first
    await ws.send_json({
        "type": "file",
        "filename": os.path.basename(file_path),
        "nonce": nonce.hex(),
    })

    # 2) Open and stream encrypted chunks
    with open(file_path, "rb") as f:
        while True:
            plaintext = f.read(chunk_size)
            if not plaintext:
                break
            ciphertext = aesgcm.encrypt(nonce, plaintext, None)
            await ws.send_bytes(ciphertext)  # whole binary frame :contentReference[oaicite:0]{index=0}

    # 3) Signal end-of-file (optional)
    await ws.send_json({"type": "file_end"})  # so server knows to stop reading

async def send_encrypted_message(
    ws: WebSocket,
    message: str,
    aesgcm: AESGCM,
    nonce: bytes
):
    plaintext = message.encode("utf-8")
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)  # AES-GCM AEAD :contentReference[oaicite:1]{index=1}
    # send as hex in a JSON wrapper so server can parse it
    await ws.send_json({
        "type": "message",
        "payload": ciphertext.hex(),
    })
