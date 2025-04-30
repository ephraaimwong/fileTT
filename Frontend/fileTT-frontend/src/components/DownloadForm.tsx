import { useState, useEffect, useRef } from "react";
import axios from "axios";
import ReconnectingWebSocket from "reconnecting-websocket";

function DownloadForm() {
  const [availableFiles, setAvailableFiles] = useState<string[]>([]);
  const [message, setMessage] = useState<string>("Connecting to server...");
  const wsRef = useRef<ReconnectingWebSocket | null>(null);
  const clientId = useRef(`client-${Math.random().toString(36).substr(2, 9)}`).current;

  const handleDownload = async (filename: string, taskId: string) => {
    if (!filename || !taskId) {
      const errorMsg = `[${clientId}] Missing filename or task ID for ${filename} (task_id: ${taskId})`;
      console.error(errorMsg);
      setMessage(errorMsg);
      return;
    }

    try {
      setMessage(`Downloading ${filename}...`);
      console.log(`[${clientId}] Initiating download for ${filename}, task_id: ${taskId} at ${new Date().toISOString()}`);
      const response = await axios.get(`http://localhost:8000/download/${filename}`, {
        responseType: "arraybuffer",
        headers: { "X-Task-Id": taskId },
      });

      console.log(`[${clientId}] Download response received for ${filename}, size: ${response.data.byteLength} bytes at ${new Date().toISOString()}`);
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", filename);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);

      setMessage(`Downloaded ${filename} successfully`);
      console.log(`[${clientId}] Download completed for ${filename} at ${new Date().toISOString()}`);
    } catch (err: any) {
      const errorMsg = `[${clientId}] Download failed for ${filename}: ${err.message}`;
      console.error(errorMsg);
      setMessage(errorMsg);
    }
  };

  useEffect(() => {
    console.log(`[${clientId}] Initializing WebSocket connection at ${new Date().toISOString()}`);
    wsRef.current = new ReconnectingWebSocket(`ws://localhost:8000/ws/notifications?client_id=${clientId}`, [], {
      maxReconnectAttempts: 10,
      reconnectInterval: 2000,
      maxReconnectInterval: 30000,
    });

    wsRef.current.onopen = () => {
      console.log(`[${clientId}] WebSocket connection established at ${new Date().toISOString()}`);
      setMessage("Connected, waiting for uploads...");
    };

    wsRef.current.onmessage = async (event) => {
      try {
        const data = JSON.parse(event.data);
        console.log(`[${clientId}] WebSocket message received at ${new Date().toISOString()}:`, data);
        if (data.action === "upload_complete") {
          setAvailableFiles((prev) => [...new Set([...prev, data.filename])]);
          console.log(`[${clientId}] Processing upload_complete for ${data.filename}, task_id: ${data.task_id} at ${new Date().toISOString()}`);
          await handleDownload(data.filename, data.task_id);
        } else if (data.action === "ping") {
          console.log(`[${clientId}] Received ping from server at ${new Date().toISOString()}`);
        }
      } catch (err) {
        console.error(`[${clientId}] Error processing WebSocket message:`, err);
        setMessage("Error processing notification");
      }
    };

    wsRef.current.onerror = (error) => {
      console.error(`[${clientId}] WebSocket error at ${new Date().toISOString()}:`, error);
      setMessage("WebSocket connection error");
    };

    wsRef.current.onclose = (event) => {
      console.log(`[${clientId}] WebSocket closed with code: ${event.code}, reason: ${event.reason || "unknown"} at ${new Date().toISOString()}`);
      setMessage("WebSocket disconnected, reconnecting...");
    };

    return () => {
      if (wsRef.current) {
        console.log(`[${clientId}] Cleaning up WebSocket connection at ${new Date().toISOString()}`);
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, []);

  return (
    <div className="flex flex-col gap-4 items-center justify-center h-screen">
      <h2 className="text-xl font-bold">Receive Files</h2>
      <p>{message}</p>
      {availableFiles.length > 0 && (
        <div className="border p-2 rounded">
          <h3>Available Files:</h3>
          <ul>
            {availableFiles.map((file) => (
              <li key={file}>{file}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default DownloadForm;