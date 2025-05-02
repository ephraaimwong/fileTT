import asyncio
import base64
import logging
import os
import uuid
from collections import defaultdict

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import FastAPI, WebSocket, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from spake2 import SPAKE2_Symmetric
from starlette.websockets import WebSocketState

# Configure logging
logging.basicConfig(level=logging.DEBUG)
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
STATIC_DIR = "static"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

# Mount static files directory
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Store progress and WebSocket clients
progress_tracker = defaultdict(lambda: {"progress": 0, "message": "Pending", "completed": False, "canceled": False})
websocket_clients = defaultdict(list)
cancel_events = defaultdict(asyncio.Event)
session_keys = {}  # Store SPAKE2-derived keys per task_id
client_id_connections = defaultdict(list) # Store client_id to WebSocket connections

# SPAKE2 stuff below
def gen_hkdf(session_secret_key, info, length=32):
    salt = os.urandom(16)
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=salt,
        info=info.encode()
    ).derive(session_secret_key)
    return salt, key

# Encrypt data using AES-GCM
def encrypt_data(data, key):
    iv = os.urandom(12)
    cipher = Cipher(algorithms.AES(key), modes.GCM(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(data) + encryptor.finalize()
    return iv, ciphertext, encryptor.tag

# Decrypt data using AES-GCM
def decrypt_data(iv, ciphertext, tag, session_key):
    cipher = Cipher(algorithms.AES(session_key), modes.GCM(iv, tag))
    decryptor = cipher.decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()

# WebSocket endpoint for progress updates
@app.websocket("/ws/progress/{task_id}")
async def websocket_progress(websocket: WebSocket, task_id: str, client_id: str = None, shared_password: str = None):
    await websocket.accept()
    websocket_clients[task_id].append(websocket)
    logger.info(f"WebSocket connected for task_id: {task_id}, client_id: {client_id or 'unknown'}, clients: {len(websocket_clients[task_id])}, ip: {websocket.client.host}:{websocket.client.port}")

    # Perform SPAKE2 key exchange using Symmetric mode
    # Use shared_password if provided, otherwise fall back to task_id + client_id
    if shared_password:
        password = shared_password.encode()
        logger.info(f"Using custom shared password for task_id: {task_id}")
    else:
        password = (task_id + client_id).encode() if client_id else task_id.encode()
        logger.info(f"Using default password generation for task_id: {task_id}")
    spake2 = SPAKE2_Symmetric(password)
    msg_out = spake2.start()
    await websocket.send_json({"spake2_msg": base64.b64encode(msg_out).decode()})

    try:
        # Receive client's SPAKE2 message
        msg_in_data = await websocket.receive_json()
        msg_in = base64.b64decode(msg_in_data.get("spake2_msg", ""))
        session_key = spake2.finish(msg_in)
        salt, derived_key = gen_hkdf(session_key, "file_encryption")
        session_keys[task_id] = derived_key
        await websocket.send_json({
            "hkdf_salt": base64.b64encode(salt).decode(),
            "hkdf_info": "file_encryption"
        })
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
async def websocket_notifications(websocket: WebSocket, client_id: str = None, shared_password: str = None):
    if not client_id:
        await websocket.close(code=1008)  # Policy violation
        return

    await websocket.accept()

    # Perform SPAKE2 key exchange using Symmetric mode if shared_password is provided
    authenticated = True
    if shared_password:
        logger.info(f"Using custom shared password for notifications from client_id: {client_id}")
        password = shared_password.encode()
        spake2 = SPAKE2_Symmetric(password)
        msg_out = spake2.start()
        await websocket.send_json({"spake2_msg": base64.b64encode(msg_out).decode()})

        try:
            # Receive client's SPAKE2 message
            msg_in_data = await websocket.receive_json()
            msg_in = base64.b64decode(msg_in_data.get("spake2_msg", ""))
            session_key = spake2.finish(msg_in)
            # Authentication successful
            logger.info(f"SPAKE2 key exchange completed for notifications from client_id: {client_id}")
            authenticated = True
        except Exception as e:
            logger.error(f"SPAKE2 authentication failed for client {client_id}: {str(e)}")
            authenticated = False
            await websocket.close(code=1008)  # Authentication failure
            return

    # Only proceed if authentication was successful
    if authenticated:
        client_id_connections[client_id].append(websocket)
        logger.info(f"Notifications WebSocket connected for client_id: {client_id}")

        # Broadcast new user connection to all other users
        for other_client_id, connections in client_id_connections.items():
            if other_client_id != client_id:
                for conn in connections:
                    try:
                        await conn.send_json({
                            "action": "user_connected",
                            "client_id": client_id
                        })
                        logger.info(f"Notified client {other_client_id} about new connection from {client_id}")
                    except Exception as e:
                        logger.error(f"Failed to notify client {other_client_id}: {str(e)}")

        # Send currently connected users to the new client
        connected_clients = [cid for cid in client_id_connections.keys() if cid != client_id]
        await websocket.send_json({
            "action": "connected_users",
            "client_ids": connected_clients
        })
        logger.info(f"Sent connected users to client {client_id}: {connected_clients}")

        try:
            while True:
                # Keep connection alive and handle any incoming messages
                try:
                    message = await asyncio.wait_for(websocket.receive_json(), timeout=30)
                    logger.info(f"Received message from client {client_id}: {message}")
                    # Handle any client messages here
                except asyncio.TimeoutError:
                    # Send a ping to keep the connection alive
                    await websocket.send_json({"action": "ping"})
        except Exception as e:
            logger.error(f"WebSocket error for client {client_id}: {str(e)}")
        finally:
            if client_id in client_id_connections:
                if websocket in client_id_connections[client_id]:
                    client_id_connections[client_id].remove(websocket)

                    # If this was the last connection for this client, notify others about disconnection
                    if not client_id_connections[client_id]:
                        # Broadcast user disconnection to all other users
                        for other_client_id, connections in client_id_connections.items():
                            if other_client_id != client_id:
                                for conn in connections:
                                    try:
                                        await conn.send_json({
                                            "action": "user_disconnected",
                                            "client_id": client_id
                                        })
                                        logger.info(f"Notified client {other_client_id} about disconnection of {client_id}")
                                    except Exception as e:
                                        logger.error(f"Failed to notify client {other_client_id}: {str(e)}")

            if websocket.application_state == WebSocketState.CONNECTED:
                await websocket.close()
            logger.info(f"Notifications WebSocket closed for client_id: {client_id}")

# Upload endpoint
@app.post("/upload/")
async def upload_file(file: UploadFile = File(...), task_id: str = Form(None)):
    # Generate a task_id if not provided
    if not task_id:
        task_id = f"task-{uuid.uuid4()}"

    try:
        # Create file path
        file_path = os.path.join(UPLOAD_DIR, file.filename)

        # Save the file
        with open(file_path, "wb") as f:
            # Read and write the file in chunks
            chunk_size = 64 * 1024  # 64KB chunks
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)

        # Mark the upload as completed
        progress_tracker[task_id]["progress"] = 100
        progress_tracker[task_id]["completed"] = True
        progress_tracker[task_id]["message"] = f"Upload of {file.filename} completed"

        # Notify any connected clients about the completed upload
        for client_id, connections in client_id_connections.items():
            for ws in connections:
                try:
                    await ws.send_json({
                        "action": "upload_complete",
                        "filename": file.filename,
                        "task_id": task_id
                    })
                except Exception as e:
                    logger.error(f"Failed to notify client {client_id}: {str(e)}")

        return {"message": "File uploaded successfully", "task_id": task_id}

    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

# Cancel endpoint
@app.post("/cancel/{task_id}")
async def cancel_upload(task_id: str):
    if task_id not in progress_tracker:
        raise HTTPException(status_code=404, detail="Task not found")

    progress_tracker[task_id]["canceled"] = True
    progress_tracker[task_id]["message"] = "Upload canceled by client"

    if task_id not in cancel_events:
        cancel_events[task_id] = asyncio.Event()

    cancel_events[task_id].set()

    return {"message": f"Upload for task {task_id} canceled"}

# Root endpoint
@app.get("/")
async def root():
    html_content = """
    <!DOCTYPE html>
    <html>
        <head>
            <title>FileTT API</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    max-width: 800px;
                    margin: 0 auto;
                    padding: 20px;
                }
                h1 {
                    color: #333;
                }
                .endpoint {
                    background-color: #f5f5f5;
                    padding: 10px;
                    margin-bottom: 10px;
                    border-radius: 5px;
                }
                .method {
                    font-weight: bold;
                    color: #0066cc;
                }
            </style>
        </head>
        <body>
            <h1>FileTT API</h1>
            <p>Welcome to the FileTT API. This API provides endpoints for secure file transfer using SPAKE2 key exchange.</p>

            <h2>Available Endpoints:</h2>

            <div class="endpoint">
                <p><span class="method">GET /</span> - This documentation page</p>
            </div>

            <div class="endpoint">
                <p><span class="method">POST /upload/</span> - Upload a file</p>
            </div>

            <div class="endpoint">
                <p><span class="method">GET /download/{filename}</span> - Download a file</p>
            </div>

            <div class="endpoint">
                <p><span class="method">POST /cancel/{task_id}</span> - Cancel an upload</p>
            </div>

            <div class="endpoint">
                <p><span class="method">WebSocket /ws/progress/{task_id}</span> - Track upload progress</p>
            </div>

            <div class="endpoint">
                <p><span class="method">WebSocket /ws/notifications</span> - Receive notifications</p>
            </div>

            <h2>Frontend Application</h2>
            <p>The frontend application is available at <a href="http://localhost:5173">http://localhost:5173</a></p>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content)

# Favicon endpoint
@app.get("/favicon.ico")
async def favicon():
    favicon_path = os.path.join(STATIC_DIR, "favicon.ico")

    # Check if favicon exists in static directory
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path)

    # Return an empty response if favicon doesn't exist
    return Response(content=b"", media_type="image/x-icon")

# Download endpoint
@app.get("/download/{filename}")
async def download_file(filename: str, task_id: str = None):
    file_path = os.path.join(UPLOAD_DIR, filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"File {filename} not found")

    # If task_id is provided, we can use it to track download progress
    if task_id:
        progress_tracker[task_id]["message"] = f"Downloading {filename}"
        progress_tracker[task_id]["progress"] = 0

    try:
        # Read the file in chunks
        with open(file_path, "rb") as f:
            content = f.read()

        # If task_id is provided, mark the download as completed
        if task_id:
            progress_tracker[task_id]["progress"] = 100
            progress_tracker[task_id]["completed"] = True
            progress_tracker[task_id]["message"] = f"Download of {filename} completed"

        # Return the file as a response
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        if task_id:
            progress_tracker[task_id]["message"] = f"Download failed: {str(e)}"
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")
