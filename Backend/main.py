from cryptography.hazmat.primitives import hashes
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
from starlette.websockets import WebSocketState


# Configure logging
# logging.basicConfig(level=logging.INFO)
logging.basicConfig(level=logging.DEBUG)  # Change from logging.INFO
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
client_id_connections = defaultdict(list) # Store client_id to WebSocket connections

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
async def websocket_progress(websocket: WebSocket, task_id: str, client_id: str = None):
    await websocket.accept()
    websocket_clients[task_id].append(websocket)
    logger.info(f"WebSocket connected for task_id: {task_id}, client_id: {client_id or 'unknown'}, clients: {len(websocket_clients[task_id])}, ip: {websocket.client.host}:{websocket.client.port}")
    
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
                message = await asyncio.wait_for(websocket.receive_json(), timeout=0.5)
                if message.get("action") == "cancel":
                    progress_tracker[task_id]["canceled"] = True
                    
                    if task_id not in cancel_events:
                        cancel_events[task_id] = asyncio.Event()
                        
                    cancel_events[task_id].set()
                    progress_tracker[task_id]["message"] = "Upload canceled by client"
                    logger.info(f"Cancellation request for task:{task_id} received via WebSocket")
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
        if websocket.application_state == WebSocketState.CONNECTED:
            await websocket.close()
        logger.info(f"WebSocket closed for {task_id}, client_id:{client_id or 'unknown'}, clients: {len(websocket_clients[task_id])}")
        if not websocket_clients[task_id]:
            del websocket_clients[task_id]
            if progress_tracker[task_id]["canceled"] or progress_tracker[task_id]["completed"]:
                del progress_tracker[task_id]
                if task_id in cancel_events:
                    del cancel_events[task_id]
                if task_id in session_keys:
                    del session_keys[task_id]

# WebSocket endpoint for notifications
@app.websocket("/ws/notifications")
async def websocket_notifications(websocket: WebSocket, client_id: str = None):
    try:
        if client_id and len(client_id_connections[client_id]) > 0:
            logger.warning(f"Client {client_id} already connected to /ws/notifications, rejecting new connection from {websocket.client.host}:{websocket.client.port}")
            await websocket.close(code=1008, reason="Only one connection per client allowed")
            return

        await websocket.accept()
        logger.info(f"WebSocket handshake completed for notifications, client_id: {client_id or 'unknown'}, ip: {websocket.client.host}:{websocket.client.port}")
        if "notifications" not in websocket_clients:
            websocket_clients["notifications"] = []
        websocket_clients["notifications"].append(websocket)
        if client_id:
            client_id_connections[client_id].append(websocket)
        logger.info(f"WebSocket connected for notifications, client_id: {client_id or 'unknown'}, clients: {len(websocket_clients['notifications'])}")
        try:
            while True:
                try:
                    await websocket.send_json({"action": "ping"})
                    logger.debug(f"Sent ping to notifications client, client_id: {client_id or 'unknown'}")
                except Exception as e:
                    logger.error(f"Failed to send ping for client_id: {client_id or 'unknown'}: {str(e)}")
                    break
                try:
                    await asyncio.wait_for(websocket.receive_json(), timeout=60)
                except asyncio.TimeoutError:
                    logger.info(f"Closing inactive WebSocket for client_id: {client_id or 'unknown'} due to timeout")
                    break
        except Exception as e:
            logger.error(f"WebSocket error for notifications, client_id: {client_id or 'unknown'}: {str(e)}")
    except Exception as e:
        logger.error(f"WebSocket handshake failed for notifications, client_id: {client_id or 'unknown'}: {str(e)}")
    finally:
        if websocket in websocket_clients["notifications"]:
            websocket_clients["notifications"].remove(websocket)
        if client_id and websocket in client_id_connections[client_id]:
            client_id_connections[client_id].remove(websocket)
            if not client_id_connections[client_id]:
                del client_id_connections[client_id]
        if websocket.application_state == WebSocketState.CONNECTED:
            await websocket.close()
        logger.info(f"WebSocket closed for notifications, client_id: {client_id or 'unknown'}, clients: {len(websocket_clients['notifications'])}")

# Broadcast upload completion
async def broadcast_upload_complete(task_id: str, filename: str):
    logger.info(f"Preparing to broadcast upload_complete for {filename}, task_id: {task_id}")
    if "notifications" in websocket_clients and websocket_clients["notifications"]:
        logger.info(f"Broadcasting to {len(websocket_clients['notifications'])} notification clients")
        for ws in websocket_clients["notifications"]:
            try:
                await ws.send_json({
                    "action": "upload_complete",
                    "filename": filename,
                    "task_id": task_id
                })
                logger.info(f"Successfully broadcasted upload_complete for {filename}, task_id: {task_id} to client")
            except Exception as e:
                logger.error(f"Failed to broadcast to client for {filename}, task_id: {task_id}: {str(e)}")
    else:
        logger.warning("No notification clients connected to broadcast upload_complete")

# Context manager for file handling
@asynccontextmanager
async def file_writer(filepath: str, task_id: str):
    f = open(filepath, "wb")
    try:
        yield f
    finally:
        f.close()
        if os.path.exists(filepath) and (progress_tracker[task_id]["canceled"] or not progress_tracker[task_id]["completed"]):
            try:
                os.remove(filepath)
                logger.info(f"Deleted partial file: {filepath}")
            except Exception as e:
                logger.error(f"Failed to delete partial file {filepath}: {str(e)}")
        elif os.path.exists(filepath):
            logger.info(f"File {filepath} uploaded successfully")

# Upload endpoint with client-provided task_id
@app.post("/upload/")#TODO, encrypt file transfers, TODO setup websocket server to serve this over instead of using psot
async def upload_file(file: UploadFile = File(...), task_id: str = Form(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    
    filepath = os.path.join(UPLOAD_DIR, file.filename)
    total_size = file.size or 1
    chunk_size = 1024 * 1024  # 1mb chunks for cancellation responsiveness
    bytes_read = 0

    logger.info(f"Starting upload for {file.filename}, task_id: {task_id}, size: {total_size}")

    async def broadcast_progress():
        while not progress_tracker[task_id]["completed"] and not progress_tracker[task_id]["canceled"]:
            if websocket_clients[task_id]:
                # logger.debug(f"Broadcasting progress for {task_id}: {progress_tracker[task_id]}")
                logger.info(f"Broadcasting progress for {task_id}: {progress_tracker[task_id]}")  # Change from logger.debug
                for ws in websocket_clients[task_id]:
                    try:
                        await ws.send_json(progress_tracker[task_id])
                    except Exception as e:
                        logger.error(f"Failed to send progress to WebSocket: {str(e)}")
            await asyncio.sleep(0.2)

    asyncio.create_task(broadcast_progress())

    try:
        async with file_writer(filepath, task_id) as f:
            while True:
                # Check cancellation before reading
                if cancel_events[task_id].is_set():
                    logger.info(f"Upload canceled for {task_id}, stopping file write")
                    # progress_tracker[task_id]["completed"] = True
                    progress_tracker[task_id]["canceled"] = True
                    progress_tracker[task_id]["message"] = "Upload canceled"
                    await file.close()  # Close the file stream to stop reading
                    return {"message": f"Upload canceled for {file.filename}", "task_id": task_id}

                # Read chunk with timeout to allow cancellation checks
                try:
                    chunk = await asyncio.wait_for(file.read(chunk_size), timeout=0.5)
                except asyncio.TimeoutError:
                    if cancel_events[task_id].is_set():
                        logger.info(f"Upload canceled for {task_id} during read, stopping file write")
                        progress_tracker[task_id]["canceled"] = True
                        progress_tracker[task_id]["message"] = "Upload canceled"
                        await file.close()
                        return {"message": f"Upload canceled for {file.filename}", "task_id": task_id}
                    continue
                
                if not chunk:  # End of file
                    break

                # Double-check cancellation after read but before write
                if cancel_events[task_id].is_set():
                    logger.info(f"Upload canceled for {task_id} after read, stopping file write")
                    # progress_tracker[task_id]["completed"] = True
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
                # logger.debug(f"Upload progress for {task_id}: {progress}%")
                logger.info(f"Upload progress for {task_id}: {progress}%")  # Change from logger.debug
                # await asyncio.sleep(0.001)  # Minimal yield to event loop
                # await asyncio.sleep(0)

            # Final cancellation check
            if cancel_events[task_id].is_set():
                logger.info(f"Upload canceled for {task_id} at end of file")
                # progress_tracker[task_id]["completed"] = True
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
            
            # --- AUTO-FETCH CHANGE ---
            if progress_tracker[task_id]["completed"] and not progress_tracker[task_id]["canceled"]:
                await broadcast_upload_complete(task_id, file.filename)
            # --- AUTO-FETCH CHANGE END ---
            return {"message": f"File {file.filename} uploaded successfully", "task_id": task_id}

    except Exception as e:
        logger.error(f"Upload failed for {task_id}: {str(e)}")
        progress_tracker[task_id]["message"] = f"Upload failed: {str(e)}"
        progress_tracker[task_id]["completed"] = True
        await file.close()  # Ensure stream is closed on error
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
    finally: # In the finally block, only delete cancel_events if canceled or completed
        if progress_tracker[task_id]["canceled"] or progress_tracker[task_id]["completed"]:
            if task_id in cancel_events:
                del cancel_events[task_id]
                

# Cancel endpoint
@app.post("/cancel/{task_id}")
async def cancel_upload(task_id: str):
    if task_id in progress_tracker:
        progress_tracker[task_id]["canceled"] = True
        
        if task_id not in cancel_events:
            cancel_events[task_id] = asyncio.Event()
            
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