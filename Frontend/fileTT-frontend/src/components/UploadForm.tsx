import { useState, ChangeEvent, useEffect, useRef } from "react";
import axios, {CancelTokenSource} from "axios";
import { spake2 } from "spake2"; // Installed via `npm install spake2`
import { Buffer } from "buffer"; // Import Buffer from the buffer module
// (window as any).Buffer = Buffer;
import * as crypto from "crypto";


// Shim crypto.randomBytes using WebCrypto's crypto.getRandomValues
if (typeof window !== "undefined" && window.crypto) {
  
  if (!crypto.randomBytes) {
    crypto.randomBytes = function (size) {
      const array = new Uint8Array(size);
      window.crypto.getRandomValues(array);
      return Buffer.from(array);
    };
  }
}

function UploadForm() {
  const [file, setFile] = useState<File | null>(null);
  const [clientProgress, setClientProgress] = useState<number>(0);
  const [serverProgress, setServerProgress] = useState<number>(0);
  const [message, setMessage] = useState<string>("");
  const [ws, setWs] = useState<WebSocket | null>(null);
  const [isUploading, setIsUploading] = useState<boolean>(false);
  const [cancelToken, setCancelToken] = useState<CancelTokenSource | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null); // Track taskId
  const clientId = useRef(`client-${Math.random().toString(36).substring(2, 9)}`).current;
  const wsReady = useRef<Promise<void> | null>(null);


  console.log("Buffer is defined:", typeof Buffer !== "undefined");
  
  // Temporary fixed password for testing (replace with secure sharing mechanism)
  const password = Buffer.from("fixed_password_32_bytes_long_1234"); // 32-byte fixed password

  // Debounce progress updates
  useEffect(()=>{
    let timeout: NodeJS.Timeout;
    if (clientProgress > 0 || serverProgress > 0){
      timeout = setTimeout(()=> {
        setClientProgress(clientProgress);
        setServerProgress(serverProgress);
      },200); //update progress every 200ms
    }
    return ()=> clearTimeout(timeout); //clear timeout on unmount
  },[clientProgress, serverProgress]);

  // Handle file selection
  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      setFile(e.target.files[0]);
      setClientProgress(0);
      setServerProgress(0);
      setMessage("");
      setIsUploading(false);
      console.log(`[${clientId}] Selected file: ${e.target.files[0].name}`);
    }
  };

  // Connect to WebSocket and complete SPAKE2 key exchange
  const connectWebSocket = (taskId: string) => {
    console.log(`[${clientId}] Connecting WebSocket for task_id: ${taskId}`);
    const websocket = new WebSocket(`ws://localhost:8000/ws/progress/${taskId}?client_id=${clientId}`);

    wsReady.current = new Promise(async (resolve, reject) => {
      // Initialize SPAKE2 client
      const spake2Instance = spake2({ suite: "ED25519-SHA256-HKDF-HMAC-SCRYPT" }, false);
      const clientState = await spake2Instance.startClient("", "", password, Buffer.from(""));

      websocket.onopen = () => {
        console.log(`[${clientId}] WebSocket opened for task_id: ${taskId} at ${new Date().toISOString()}`);
      };

      websocket.onmessage = async (event) => {
        const data = JSON.parse(event.data);
        console.log(`[${clientId}] WebSocket message for ${taskId} at ${new Date().toISOString()}:`, data);

        if (data.spake2_msg) {
          try {
            // Decode server’s SPAKE2 message
            const serverMsg = Buffer.from(data.spake2_msg, "base64");
            // Process server’s message and get shared secret
            const sharedSecret = await clientState.finish(serverMsg);
            // Generate client’s message
            const clientMsg = clientState.getMessage();
            // Send client’s SPAKE2 message
            websocket.send(
              JSON.stringify({
                spake2_msg: clientMsg.toString("base64"),
              })
            );
            console.log(`[${clientId}] Sent SPAKE2 response for task_id: ${taskId}`);
            resolve(); // Key exchange complete
          } catch (error) {
            console.error(`[${clientId}] SPAKE2 error:`, error);
            setMessage("SPAKE2 key exchange failed");
            reject(error);
          }
        } else {
          setServerProgress(data.progress);
          setMessage(data.message);
          if (data.canceled || data.completed) {
            setIsUploading(false);
          }
        }
      };

      websocket.onerror = (error) => {
        setMessage("WebSocket connection failed");
        console.error(`[${clientId}] WebSocket error for ${taskId}:`, error);
        reject(error);
      };

      websocket.onclose = () => {
        setMessage("Server-side progress tracking complete");
        console.log(`[${clientId}] WebSocket closed for task_id: ${taskId} at ${new Date().toISOString()}`);
      };
    });

    setWs(websocket);
    return websocket;
  };

  //clean up websocket on component unmount
  useEffect(()=>{
    return ()=> {
      if (ws) {
        ws?.close();
        console.log(`[${clientId}] WebSocket cleanup on unmount`);
      }
    };
  }, [ws, clientId]);

  //handle file upload
  const handleUpload = async () => {
    if (!file) {
      setMessage("Please select a file to upload");
      console.warn("No file selected");
      return;
    }

    const source = axios.CancelToken.source();
    setCancelToken(source);
    setIsUploading(true);
    setClientProgress(0);
    setServerProgress(0);
    setMessage("Starting upload...");

    const taskId = `task-${uuidv4()}`; // Generate task_id client-side for immediate WebSocket connection
    setTaskId(taskId); // Set task_id in state
    const websocket = connectWebSocket(taskId);



    try {
      console.log(`[${clientId}] Waiting for SPAKE2 key exchange for task_id: ${taskId}`);
      await wsReady.current;
      console.log(`[${clientId}] SPAKE2 key exchange completed for task_id: ${taskId}`);

      const formData = new FormData();
      formData.append("file", file);
      formData.append("task_id", taskId); // Send task_id to backend

      console.log(`[${clientId}] Starting upload for ${file.name} with task_id: ${taskId}`);
      const response = await axios.post("http://localhost:8000/upload/", formData, {
        headers: {"Content-Type": "multipart/form-data",},
        onUploadProgress: (progressEvent)=>{
          if(progressEvent.total){
            const progress = Math.round((progressEvent.loaded / progressEvent.total)*100);
            setClientProgress(progress);
            console.log(`[${clientId}] Client-side upload progress: ${progress}% at ${new Date().toISOString()}`);
          }
        },
        cancelToken: source.token,
        timeout:0, // Disable Axios timeout for large files
      });
      setMessage(response.data.message);
      console.log(`[${clientId}] Upload response:`, response.data);
      // connectWebSocket(response.data.task_id); // Connect to WebSocket with task ID
    } catch (err: any) {
      if (axios.isCancel(err)) {
        setMessage("Upload canceled");
        console.log(`[${clientId}] Upload canceled by user`);
      } else {
        console.error(`[${clientId}] Upload error:`, err);
        setMessage(`Upload failed: ${err.response?.data?.error || err.message}`);
      }
    } finally {
      setIsUploading(false);
      setCancelToken(null);
      setTaskId(null); // Reset task_id
      wsReady.current = null;
      // if (websocket) {
      //   websocket.close();
      //   setWs(null);
      // }
    }
  };

    // Handle cancel upload
    const handleCancel = async () => {
      if (cancelToken) {
          cancelToken.cancel("Upload canceled by user");
          setMessage("Upload canceled");
          setIsUploading(false);
          setClientProgress(0);
          setServerProgress(0);
      }
      if (ws && taskId) {
          try {
              ws.send(JSON.stringify({ action: "cancel", task_id: taskId }));
              console.log(`[${clientId}] Sent cancellation request for task_id: ${taskId}`);
          } catch (error) {
            console.log(`[${clientId}] Failed to send WebSocket cancel: ${error}`);
          } finally {
              ws.close();
              setWs(null);
          }
      }
      if (taskId) {
          try {
              await axios.post(`http://localhost:8000/cancel/${taskId}`);
              console.log(`[${clientId}] Sent HTTP cancellation request for task_id: ${taskId}`);
          } catch (error) {
            console.log(`[${clientId}] Failed to send HTTP cancel: ${error}`);
          }
      }
      setCancelToken(null);
      setTaskId(null);
      wsReady.current = null;
  };

  return (
    <div className="flex flex-col gap-4 items-center justify-center h-screen">
      <input type="file" onChange={handleFileChange} disabled={isUploading}/>
      <div className="flex gap-2">
      <button onClick={handleUpload} className="bg-blue-500 text-white p-2 rounded" disabled={!file || isUploading}>
        Upload File
      </button>
      {isUploading && (
          <button
            onClick={handleCancel}
            className="bg-red-500 text-white p-2 rounded hover:bg-red-600"
          >
            Cancel Upload
          </button>
        )}
      </div>
      {clientProgress > 0 && (
        <div className="w-64">
          <div className="text-sm mb-1">Client-Side Upload Progress: {clientProgress}%</div>
          <div className="w-full bg-gray-200 rounded-full h-2.5">
            <div
              className="bg-blue-500 h-2.5 rounded-full"
              style={{ width: `${clientProgress}%` }}
            ></div>
          </div>
        </div>
      )}
      {serverProgress > 0 && (
        <div className="w-64">
          <div className="text-sm mb-1">Server-Side Progress: {serverProgress}%</div>
          <div className="w-full bg-gray-200 rounded-full h-2.5">
            <div
              className="bg-green-500 h-2.5 rounded-full"
              style={{ width: `${serverProgress}%` }}
            ></div>
          </div>
        </div>
      )}
      {message && (
        <div
          className={`p-2 rounded ${
            message.includes("failed")  || message.includes("canceled") ? "bg-red-100 text-red-700" : "bg-green-100 text-green-700"
          }`}
        >
          {message}
        </div>
      )}
    </div>
  );
}

// UUID generator for client-side task_id
function uuidv4() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
    const r = (Math.random() * 16) | 0,
      v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

export default UploadForm;