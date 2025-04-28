from cryptography.hazmat.primitives import hashes
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import os
import receive
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from spake2 import SPAKE2_A
from websocket import send
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.post("/upload/")#TODO, encrypt file transfers, TODO setup websocket server to serve this over instead of using psot
async def upload_file(file: UploadFile = File(...)):
    filepath = os.path.join(UPLOAD_DIR, file.filename)
    with open(filepath, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)
    return {"message": f"File {file.filename} uploaded successfully."}

@app.get("/")
async def root():
    return {"message": "Backend is alive"}

@app.get("/download/{filename}")
async def download_file(filename: str):
    filepath = os.path.join(UPLOAD_DIR, filename)
    if os.path.exists(filepath):
        return FileResponse(filepath, filename=filename)
    return {"error": "File not found"}



# SPAKE2 stuff below
def hkdf_expand(session_secret_key, info, length=32):
    """
    Expands key so we can use it for key confirmation.
    :param session_secret_key: sesh secret key generated frm SPAKE2
    :param info: Tag attached to key to identify it
    :param length: keyLength
    :return: HKDF key
    """
    salt = os.urandom(16)
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=salt,
        info=info.encode()
    ).derive(session_secret_key)