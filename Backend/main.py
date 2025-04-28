from fastapi import FastAPI, UploadFile, File, WebSocket, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
import os
import uuid
from collections import defaultdict
import asyncio
import logging
from contextlib import asynccontextmanager



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

# WebSocket endpoint for progress updates
@app.websocket("/ws/progress/{task_id}")
async def websocket_progress(websocket: WebSocket, task_id: str):
    await websocket.accept()
    websocket_clients[task_id].append(websocket)
    logger.info(f"WebSocket connected for task_id: {task_id}, clients: {len(websocket_clients[task_id])}")
    try:
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

                bytes_read += len(chunk)
                f.write(chunk)
                f.flush()  # Ensure data is written to disk
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
            
    #         if not progress_tracker[task_id]["canceled"]:
    #             f.flush()
    #             os.fsync(f.fileno())
    #             progress_tracker[task_id]["progress"] = 100
    #             progress_tracker[task_id]["completed"] = True
    #             progress_tracker[task_id]["message"] = f"File {file.filename} uploaded successfully"
    #             logger.info(f"Upload completed for {task_id}")
    #         else:
    #             progress_tracker[task_id]["completed"] = True
    #             progress_tracker[task_id]["message"] = f"Upload canceled for {file.filename}"
    #             logger.info(f"Upload canceled for {task_id}, file not saved")
                
    #     if progress_tracker[task_id]["canceled"]:
    #         if os.path.exists(filepath):
    #             try:
    #                 os.remove(filepath)
    #                 logger.info(f"Deleted canceled upload file: {filepath}")
    #             except Exception as cleanup_error:
    #                 logger.error(f"Failed to delete canceled upload file {filepath}: {str(cleanup_error)}")
    #         return {"message": f"Upload canceled for {file.filename}", "task_id": task_id}
    #     return {"message": f"File {file.filename} uploaded successfully", "task_id": task_id}
    # except Exception as e:
    #     logger.error(f"Upload failed for {task_id}: {str(e)}")
    #     progress_tracker[task_id]["message"] = f"Upload failed: {str(e)}"
    #     progress_tracker[task_id]["completed"] = True
    #     if os.path.exists(filepath):
    #         try:
    #             os.remove(filepath)
    #             logger.info(f"Deleted partial file: {filepath}")
    #         except Exception as cleanup_error:
    #             logger.error(f"Failed to delete partial file {filepath}: {str(cleanup_error)}")
    #     raise
    # finally:
    #     if bytes_read < total_size and os.path.exists(filepath):
    #         try:
    #             os.remove(filepath)
    #             logger.info(f"Deleted partial file after incomplete upload: {filepath}")
    #         except Exception as cleanup_error:
    #             logger.error(f"Failed to delete partial file {filepath}: {str(cleanup_error)}")

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
async def download_file(filename: str):
    filepath = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(filepath):
        logger.error(f"File not found: {filename}")
        raise HTTPException(status_code=404, detail="File not found")
        # return {"error": "File not found"}

    task_id = str(uuid.uuid4())
    file_size = os.path.getsize(filepath)
    bytes_sent = 0
    chunk_size = 1024 * 1024 * 10 #10MB chunks

    logger.info(f"Starting download for {filename}, task_id: {task_id}, size: {file_size}")
    async def stream_file():
        nonlocal bytes_sent
        with open(filepath, "rb") as f:
            while chunk := f.read(chunk_size):
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