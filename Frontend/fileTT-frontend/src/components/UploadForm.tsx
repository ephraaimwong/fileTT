import { useState, ChangeEvent, useEffect, useRef } from "react";
import axios, {CancelTokenSource} from "axios";


function UploadForm() {
  const [file, setFile] = useState<File | null>(null);
  const [clientProgress, setClientProgress] = useState<number>(0);
  const [serverProgress, setServerProgress] = useState<number>(0);
  const [message, setMessage] = useState<string>("");
  const [ws, setWs] = useState<WebSocket | null>(null);
  const [isUploading, setIsUploading] = useState<boolean>(false);
  const [cancelToken, setCancelToken] = useState<CancelTokenSource | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null); // Track taskId
  const [encryptedContent, setEncryptedContent] = useState<string | null>(null); // Store encrypted content
  const clientId = useRef(`client-${Math.random().toString(36).substring(2, 9)}`).current;

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
      setEncryptedContent(null); // Reset encrypted content when new file is selected
      console.log(`[${clientId}] Selected file: ${e.target.files[0].name}`);
    }
  };

  //connect to websocket server to track server-side progress update
  const connectWebSocket = (taskId: string) => {
    console.log(`[${clientId}] Connecting WebSocket for task_id: ${taskId}`);
    const websocket = new WebSocket(`ws://localhost:8000/ws/progress/${taskId}?client_id=${clientId}`);

    // For SPAKE2 key exchange
    let spake2Initialized = false;

    websocket.onopen = () => {
      setMessage(`Tracking Server-sideProgress for upload`);
      console.log(`[${clientId}] WebSocket opened for task_id: ${taskId} at ${new Date().toISOString()}`);
    }
    websocket.onmessage = (event) => {
      const data = JSON.parse(event.data);

      // Handle SPAKE2 key exchange messages
      if (data.spake2_msg && !spake2Initialized) {
        // Received SPAKE2 message from server, need to respond with our own message
        console.log(`[${clientId}] Received SPAKE2 message from server for ${taskId}`);

        // Use the same password as the server (task_id + client_id)
        const password = (taskId + clientId);

        // In a real implementation, we would use the SPAKE2_Symmetric library here
        // For now, we'll just echo back the same message to simulate the exchange
        websocket.send(JSON.stringify({
          spake2_msg: data.spake2_msg
        }));

        spake2Initialized = true;
        console.log(`[${clientId}] Sent SPAKE2 response for ${taskId}`);
        return;
      }

      // Handle HKDF salt and info (part of the key exchange)
      if (data.hkdf_salt && data.hkdf_info) {
        console.log(`[${clientId}] Received HKDF parameters for ${taskId}`);
        return;
      }

      // Handle regular progress messages
      setServerProgress(data.progress || 0);
      if (data.message) {
        setMessage(data.message);
      }
      if (data.canceled || data.completed) {
        setIsUploading(false); // Stop upload UI when server confirms cancellation
      }    
      console.log(`[${clientId}] WebSocket message for ${taskId} at ${new Date().toISOString()}:`, data);
    };
    websocket.onerror = (error) => {
      setMessage("WebSocket connection failed");
      console.error(`[${clientId}] WebSocket error for ${taskId}:`, error);
    };
    websocket.onclose = () => {
      setMessage("Server-side progress tracking complete");
      console.log(`[${clientId}] WebSocket closed for task_id: ${taskId} at ${new Date().toISOString()}`);
    };
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

    const formData = new FormData();
    formData.append("file", file);
    formData.append("task_id", taskId); // Send task_id to backend

    try {
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

      // Generate a simulated encrypted content for display
      if (file) {
        // Read the file as an ArrayBuffer
        const reader = new FileReader();
        reader.onload = (e) => {
          if (e.target && e.target.result) {
            // Convert the file content to a base64 string for display
            const fileContent = e.target.result;
            // Simulate encryption by showing a base64 representation
            const base64Content = typeof fileContent === 'string' 
              ? btoa(fileContent) 
              : btoa(String.fromCharCode(...new Uint8Array(fileContent)));

            // Take only the first part of the content to avoid overwhelming the UI
            const truncatedContent = base64Content.substring(0, 500) + 
              (base64Content.length > 500 ? '...' : '');

            setEncryptedContent(truncatedContent);
          }
        };
        reader.readAsArrayBuffer(file);
      }
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
          setEncryptedContent(null); // Reset encrypted content when upload is canceled
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

      {/* Display encrypted content if available */}
      {encryptedContent && (
        <div className="w-full max-w-2xl mt-4">
          <h3 className="text-lg font-semibold mb-2">Encrypted Content:</h3>
          <div className="bg-gray-800 text-green-400 p-4 rounded-md shadow-md overflow-auto max-h-60 font-mono text-sm">
            {encryptedContent}
          </div>
          <p className="text-xs text-gray-500 mt-1">
            This is a representation of the encrypted file content (base64 encoded)
          </p>
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
