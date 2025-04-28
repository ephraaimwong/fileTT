from fastapi import FastAPI, UploadFile, File, WebSocket, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from spake2 import SPAKE2_A
import os
import uuid
from collections import defaultdict
import asyncio
import logging
from contextlib import asynccontextmanager
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import base64



# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Task-Id"],
)

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Store progress and WebSocket clients
progress_tracker = defaultdict(lambda: {"progress": 0, "message": "Pending", "completed": False, "canceled": False})
websocket_clients = defaultdict(list)
cancel_events = defaultdict(asyncio.Event)
session_keys = {}  # Store SPAKE2-derived keys per task_id

# SPAKE2 stuff below
def hkdf_expand(session_secret_key, info, length=32):
    """
    Expands key for encryption and key confirmation.
    :param session_secret_key: Session secret key from SPAKE2
    :param info: Tag to identify the key
    :param length: Key length
    :return: HKDF-derived key
    """
    salt = os.urandom(16)
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=salt,
        info=info.encode()
    ).derive(session_secret_key)
    
# Encrypt data using AES-GCM
def encrypt_data(data, key):
    """
    Encrypts data using AES-GCM.
    :param data: Data to encrypt
    :param key: 32-byte encryption key
    :return: (iv, ciphertext, tag)
    """
    iv = os.urandom(12)
    cipher = Cipher(algorithms.AES(key), modes.GCM(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(data) + encryptor.finalize()
    return iv, ciphertext, encryptor.tag

# Decrypt data using AES-GCM
def decrypt_data(iv, ciphertext, tag, key):
    """
    Decrypts data using AES-GCM.
    :param iv: Initialization vector
    :param ciphertext: Encrypted data
    :param tag: Authentication tag
    :param key: 32-byte encryption key
    :return: Decrypted data
    """
    cipher = Cipher(algorithms.AES(key), modes.GCM(iv, tag))
    decryptor = cipher.decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()

# WebSocket endpoint for progress updates
@app.websocket("/ws/progress/{task_id}")
async def websocket_progress(websocket: WebSocket, task_id: str):
    await websocket.accept()
    websocket_clients[task_id].append(websocket)
    logger.info(f"WebSocket connected for task_id: {task_id}, clients: {len(websocket_clients[task_id])}")
    
    # Perform SPAKE2 key exchange
    spake2_a = SPAKE2_A(os.urandom(32))  # Use random password for simplicity
    msg_out = spake2_a.start()
    await websocket.send_json({"spake2_msg": base64.b64encode(msg_out).decode()})
    
    try:
        # Receive client's SPAKE2 message
        msg_in_data = await websocket.receive_json()
        msg_in = base64.b64decode(msg_in_data.get("spake2_msg", ""))
        session_key = spake2_a.finish(msg_in)
        derived_key = hkdf_expand(session_key, "file_encryption")
        session_keys[task_id] = derived_key
        logger.info(f"SPAKE2 key exchange completed for task_id: {task_id}")
        
        while True:
            # send server-side progress updates to the client
            progress_data = progress_tracker[task_id]
            logger.info(f"Sending progress for {task_id}: {progress_data}")
            await websocket.send_json(progress_data)
            
            #check for cancellation requests from the client
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=1.0)
                if message.get("action") == "cancel":
                    progress_tracker[task_id]["canceled"] = True
                    cancel_events[task_id].set()
                    progress_tracker[task_id]["message"] = "Upload canceled by client"
                    logger.info(f"Cancellation request for task:{task_id} received")
                elif message.get("action") == "upload_chunk":
                    # Handle encrypted file chunk
                    iv = base64.b64decode(message["iv"])
                    ciphertext = base64.b64decode(message["ciphertext"])
                    tag = base64.b64decode(message["tag"])
                    chunk = decrypt_data(iv, ciphertext, tag, session_keys[task_id])
                    filepath = os.path.join(UPLOAD_DIR, message["filename"])
                    with open(filepath, "ab") as f:
                        f.write(chunk)
                    progress_tracker[task_id]["progress"] = message.get("progress", 0)
                    progress_tracker[task_id]["message"] = f"Received chunk for {message['filename']}"
                    logger.info(f"Received chunk for {task_id}, progress: {progress_tracker[task_id]['progress']}%")
            except asyncio.TimeoutError:
                pass # no message received, continue to send progress updates
            if progress_data["completed"] or progress_data["canceled"]:
                logger.info(f"Progress complete or canceled for task: {task_id}, closing WebSocket")
                break

    except Exception as e:
        logger.error(f"WebSocket error for {task_id}: {str(e)}")
        progress_tracker[task_id]["message"] = f"WebSocket error: {str(e)}"
    finally:
        websocket_clients[task_id].remove(websocket)
        await websocket.close()
        logger.info(f"WebSocket closed for {task_id}, clients: {len(websocket_clients[task_id])}")
        if not websocket_clients[task_id]:
            del websocket_clients[task_id]
            if progress_tracker[task_id]["canceled"] or progress_tracker[task_id]["completed"]:
                del progress_tracker[task_id]
                if task_id in cancel_events:
                    del cancel_events[task_id]

# Context manager for file handling
@asynccontextmanager
async def file_writer(filepath: str):
    f = open(filepath, "wb")
    try:
        yield f
    finally:
        f.close()
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
                logger.info(f"Deleted partial file: {filepath}")
            except Exception as e:
                logger.error(f"Failed to delete partial file {filepath}: {str(e)}")

# Upload endpoint with client-provided task_id
@app.post("/upload/")
async def upload_file(file: UploadFile = File(...), task_id: str = Form(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    
    filepath = os.path.join(UPLOAD_DIR, file.filename)
    total_size = file.size or 1
    chunk_size = 1024 * 1024 *10  # 10mb chunks
    bytes_read = 0

    logger.info(f"Starting upload for {file.filename}, task_id: {task_id}, size: {total_size}")

    async def broadcast_progress():
        while not progress_tracker[task_id]["completed"] and not progress_tracker[task_id]["canceled"]:
            if websocket_clients[task_id]:
                logger.debug(f"Broadcasting progress for {task_id}: {progress_tracker[task_id]}")
                for ws in websocket_clients[task_id]:
                    try:
                        await ws.send_json(progress_tracker[task_id])
                    except Exception as e:
                        logger.error(f"Failed to send progress to WebSocket: {str(e)}")
            await asyncio.sleep(0.2)

    asyncio.create_task(broadcast_progress())

    try:
        async with file_writer(filepath) as f:
            while True:
                # Check cancellation before reading
                if cancel_events[task_id].is_set():
                    logger.info(f"Upload canceled for {task_id}, stopping file write")
                    progress_tracker[task_id]["completed"] = True
                    progress_tracker[task_id]["canceled"] = True
                    progress_tracker[task_id]["message"] = "Upload canceled"
                    await file.close()  # Close the file stream to stop reading
                    return {"message": f"Upload canceled for {file.filename}", "task_id": task_id}

                # Read a small chunk
                chunk = await file.read(chunk_size)
                if not chunk:  # End of file
                    break

                # Double-check cancellation after read but before write
                if cancel_events[task_id].is_set():
                    logger.info(f"Upload canceled for {task_id} after read, stopping file write")
                    progress_tracker[task_id]["completed"] = True
                    progress_tracker[task_id]["canceled"] = True
                    progress_tracker[task_id]["message"] = "Upload canceled"
                    await file.close()
                    return {"message": f"Upload canceled for {file.filename}", "task_id": task_id}

                # Encrypt chunk if session key exists (set via WebSocket)
                if task_id in session_keys:
                    iv, ciphertext, tag = encrypt_data(chunk, session_keys[task_id])
                    f.write(iv + tag + ciphertext)  # Store encrypted data
                else:
                    f.write(chunk)  # Fallback to unencrypted
                    
                f.flush()  # Ensure data is written to disk
                bytes_read += len(chunk)
                progress = min(100, (bytes_read / total_size) * 100)
                progress_tracker[task_id]["progress"] = progress
                progress_tracker[task_id]["message"] = f"Uploaded {bytes_read} of {total_size} bytes"
                logger.debug(f"Upload progress for {task_id}: {progress}%")
                await asyncio.sleep(0.001)  # Minimal yield to event loop

            # Final cancellation check
            if cancel_events[task_id].is_set():
                logger.info(f"Upload canceled for {task_id} at end of file")
                progress_tracker[task_id]["completed"] = True
                progress_tracker[task_id]["canceled"] = True
                progress_tracker[task_id]["message"] = "Upload canceled"
                await file.close()
                return {"message": f"Upload canceled for {file.filename}", "task_id": task_id}

            # Upload completed successfully
            f.flush()
            progress_tracker[task_id]["progress"] = 100
            progress_tracker[task_id]["completed"] = True
            progress_tracker[task_id]["message"] = f"File {file.filename} uploaded successfully"
            logger.info(f"Upload completed for {task_id}")
            return {"message": f"File {file.filename} uploaded successfully", "task_id": task_id}

    except Exception as e:
        logger.error(f"Upload failed for {task_id}: {str(e)}")
        progress_tracker[task_id]["message"] = f"Upload failed: {str(e)}"
        progress_tracker[task_id]["completed"] = True
        await file.close()  # Ensure stream is closed on error
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
    finally:
        if task_id in cancel_events:
            del cancel_events[task_id]
            

# Cancel endpoint
@app.post("/cancel/{task_id}")
async def cancel_upload(task_id: str):
    if task_id in progress_tracker:
        progress_tracker[task_id]["canceled"] = True
        cancel_events[task_id].set()
        progress_tracker[task_id]["message"] = "Upload canceled by client"
        logger.info(f"Cancellation requested for task_id: {task_id} via cancel endpoint")
        return {"message": f"Upload canceled for task_id: {task_id}"}
    return {"error": "Task not found"}

# Download endpoint (unchanged)
@app.get("/download/{filename}")
async def download_file(filename: str, task_id: str = None):
    filepath = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(filepath):
        logger.error(f"File not found: {filename}")
        raise HTTPException(status_code=404, detail="File not found")
        # return {"error": "File not found"}

    task_id = task_id or str(uuid.uuid4())
    file_size = os.path.getsize(filepath)
    bytes_sent = 0
    chunk_size = 1024 * 1024 * 10 #10MB chunks

    logger.info(f"Starting download for {filename}, task_id: {task_id}, size: {file_size}")
    async def stream_file():
        nonlocal bytes_sent
        with open(filepath, "rb") as f:
            while chunk := f.read(chunk_size):
                
                if task_id in session_keys:
                    iv, ciphertext, tag = encrypt_data(chunk, session_keys[task_id])
                    chunk = iv + tag + ciphertext  # Send encrypted data
                    
                bytes_sent += len(chunk)
                progress = min(100, (bytes_sent / file_size) * 100)
                progress_tracker[task_id]["progress"] = progress
                progress_tracker[task_id]["message"] = f"Downloaded {bytes_sent} of {file_size} bytes"
                logger.info(f"Download progress for {task_id}: {progress}%")
                yield chunk
                await asyncio.sleep(0.01) 
        progress_tracker[task_id]["progress"] = 100
        progress_tracker[task_id]["completed"] = True
        logger.info(f"Download completed for {task_id}")

    return StreamingResponse(
        stream_file(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={filename}", "X-Task-Id": task_id},
    )

# Root endpoint
@app.get("/")
async def root():
    return {"message": "Backend is alive"}

# Polling endpoint
@app.get("/status/{task_id}")
async def get_status(task_id: str):
    logger.info(f"Polling status for {task_id}: {progress_tracker.get(task_id)}")
    return progress_tracker.get(task_id, {"error": "Task not found"})