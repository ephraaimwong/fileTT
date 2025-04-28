import { useState, ChangeEvent, useEffect } from "react";
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
      console.log("Selected file:", e.target.files[0].name);
    }
  };

  //connect to websocket server to track server-side progress update
  const connectWebSocket = (taskId: string) => {
    console.log(`Connecting WebSocket for task_id: ${taskId}`);
    const websocket = new WebSocket(`ws://localhost:8000/ws/progress/${taskId}`);
    websocket.onopen = () => {
      setMessage(`Tracking Server-sideProgress for upload`);
      console.log(`WebSocket opened for task_id: ${taskId} at ${new Date().toISOString()}`);
    }
    websocket.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setServerProgress(data.progress);
      setMessage(data.message);
      if (data.canceled || data.completed) {
        setIsUploading(false); // Stop upload UI when server confirms cancellation
      }    
      console.log(`WebSocket message for ${taskId} at ${new Date().toISOString()}:`, data);
    };
    websocket.onerror = (error) => {
      setMessage("WebSocket connection failed");
      console.error(`WebSocket error for ${taskId}:`, error);
    };
    websocket.onclose = () => {
      setMessage("Server-side progress tracking complete");
      console.log(`WebSocket closed for task_id: ${taskId} at ${new Date().toISOString()}`);
    };
    setWs(websocket);
    return websocket;
  };

  //clean up websocket on component unmount
  useEffect(()=>{
    return ()=> {
      if (ws) {
        ws?.close();
        console.log("WebSocket cleanup on unmount");
      }
    };
  }, [ws]);

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
      console.log(`Starting upload for ${file.name} with task_id: ${taskId}`);
      const response = await axios.post("http://localhost:8000/upload/", formData, {
        headers: {"Content-Type": "multipart/form-data",},
        onUploadProgress: (progressEvent)=>{
          if(progressEvent.total){
            const progress = Math.round((progressEvent.loaded / progressEvent.total)*100);
            setClientProgress(progress);
            console.log(`Client-side upload progress: ${progress}% at ${new Date().toISOString()}`);
          }
        },
        cancelToken: source.token,
        timeout:0, // Disable Axios timeout for large files
      });
      setMessage(response.data.message);
      console.log(`Upload response:`, response.data);
      connectWebSocket(response.data.task_id); // Connect to WebSocket with task ID
    } catch (err: any) {
      if (axios.isCancel(err)) {
        setMessage("Upload canceled");
        console.log("Upload canceled by user");
      } else {
        console.error("Upload error:", err);
        setMessage(`Upload failed: ${err.response?.data?.error || err.message}`);
      }
    } finally {
      setIsUploading(false);
      setCancelToken(null);
      setTaskId(null); // Reset task_id
      if (websocket) {
        websocket.close();
        setWs(null);
      }
    }
  };

    // Handle cancel upload
    const handleCancel = async () => {
      if (cancelToken && ws && taskId) {
        cancelToken.cancel("Upload canceled by user");
        // Send cancellation signal via WebSocket
        ws.send(JSON.stringify({ action: "cancel" }));
        console.log(`Sent WebSocket cancellation for task_id: ${taskId}`);

        setIsUploading(false);
        setClientProgress(0);
        setServerProgress(0);
        setMessage("Upload canceled");
        ws.close();
        setWs(null);
        setCancelToken(null);
        setTaskId(null);
        console.log("Cancel requested");
      }
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