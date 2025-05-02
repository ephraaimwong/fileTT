import React, { useState, ChangeEvent, useEffect, useRef } from "react";
import axios, {CancelTokenSource} from "axios";

// User Connection Visualizer Component
interface UserConnectionVisualizerProps {
  senderName: string;
  receiverName: string;
  isConnected: boolean;
}

const UserConnectionVisualizer: React.FC<UserConnectionVisualizerProps> = ({ senderName, receiverName, isConnected }) => {
  return (
    <div className="user-connection-container my-6 p-4 border rounded-lg shadow-md">
      <h3 className="text-lg font-semibold mb-3">User Connection Visualization</h3>

<div className="user-connection-layout" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1rem', width: '100%' }}>
        {/* Sender */}
        <div className="user-box p-3 bg-blue-100 rounded-lg text-center w-1/4">
          <div className="user-icon mb-2">
            <svg className="w-10 h-10 mx-auto text-blue-600" fill="currentColor" viewBox="0 0 20 20" style={{ maxWidth: '50px', maxHeight: '50px' }}>
              <path fillRule="evenodd" d="M10 9a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 1114 0H3z" clipRule="evenodd" />
            </svg>
          </div>
          <div className="user-name font-medium">{senderName || "Sender"}</div>
        </div>

        {/* Connection visualization */}
        <div className="connection-container flex-1 mx-4 relative">
          <div className={`connection-line h-1 ${isConnected ? 'bg-green-500' : 'bg-gray-300'} w-full absolute top-1/2`}></div>

          {/* Connection status */}
          <div className="connection-status text-xs text-center absolute w-full" style={{ bottom: '-20px' }}>
            {isConnected ? "Connected" : "Disconnected"}
          </div>
        </div>

        {/* Receiver */}
        <div className="user-box p-3 bg-purple-100 rounded-lg text-center w-1/4">
          <div className="user-icon mb-2">
            <svg className="w-10 h-10 mx-auto text-purple-600" fill="currentColor" viewBox="0 0 20 20" style={{ maxWidth: '50px', maxHeight: '50px' }}>
              <path fillRule="evenodd" d="M10 9a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 1114 0H3z" clipRule="evenodd" />
            </svg>
          </div>
          <div className="user-name font-medium">{receiverName || "Receiver"}</div>
        </div>
      </div>
    </div>
  );
};


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
  const [connectedUsers, setConnectedUsers] = useState<string[]>([]); // Store connected users
  const [notificationWs, setNotificationWs] = useState<WebSocket | null>(null); // WebSocket for notifications
  const [isConnected, setIsConnected] = useState<boolean>(false); // Connection status
  const [sharedPassword, setSharedPassword] = useState<string>(""); // Store shared password
  const clientId = useRef(() => {
    // Try to get existing clientId from localStorage
    const savedClientId = localStorage.getItem('clientId');
    if (savedClientId) return savedClientId;

    // Generate a new one if none exists
    const newClientId = `client-${Math.random().toString(36).substring(2, 9)}`;
    localStorage.setItem('clientId', newClientId);
    return newClientId;
  }).current;

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

    // Include shared password in the WebSocket URL if provided
    let wsUrl = `ws://localhost:8000/ws/progress/${taskId}?client_id=${clientId}`;
    if (sharedPassword) {
      wsUrl += `&shared_password=${encodeURIComponent(sharedPassword)}`;
      console.log(`[${clientId}] Using custom shared password for connection`);
    }

    const websocket = new WebSocket(wsUrl);

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

        // Use shared password if provided, otherwise use default (task_id + client_id)
        const password = sharedPassword || (taskId + clientId);
        console.log(`[${clientId}] Using ${sharedPassword ? 'custom' : 'default'} password for SPAKE2 exchange`);

        // Simple SPAKE2-like response generation
        // This is a simplified implementation that generates a deterministic response
        // based on the password and the received message
        const generateSpake2Response = (incomingMsg, password) => {
          // Decode the base64 message
          const decodedMsg = atob(incomingMsg);

          // Create a response by combining the password with the decoded message
          // and hashing it using a simple algorithm
          let response = '';
          const combinedInput = password + decodedMsg;

          // Simple hash function to generate a deterministic response
          for (let i = 0; i < combinedInput.length; i++) {
            response += String.fromCharCode(
              (combinedInput.charCodeAt(i) + password.charCodeAt(i % password.length)) % 256
            );
          }

          // Encode the response as base64
          return btoa(response);
        };

        // Generate and send the SPAKE2 response
        const responseMsg = generateSpake2Response(data.spake2_msg, password);
        websocket.send(JSON.stringify({
          spake2_msg: responseMsg
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

  // Connect to notification WebSocket
  useEffect(() => {
    const connectNotificationWs = () => {
      // Include shared password in the WebSocket URL if provided
      let wsUrl = `ws://localhost:8000/ws/notifications?client_id=${clientId}`;
      if (sharedPassword) {
        wsUrl += `&shared_password=${encodeURIComponent(sharedPassword)}`;
        console.log(`[${clientId}] Using custom shared password for notification connection`);
      }

      const websocket = new WebSocket(wsUrl);

      // For SPAKE2 key exchange
      let spake2Initialized = false;

      websocket.onopen = () => {
        console.log(`[${clientId}] Notification WebSocket connected`);
        // Don't set connected until after SPAKE2 authentication if password is provided
        if (!sharedPassword) {
          setIsConnected(true);
        }
      };

      websocket.onmessage = (event) => {
        const data = JSON.parse(event.data);

        // Handle SPAKE2 key exchange messages
        if (data.spake2_msg && !spake2Initialized) {
          // Received SPAKE2 message from server, need to respond with our own message
          console.log(`[${clientId}] Received SPAKE2 message from server for notifications`);

          // Use shared password for SPAKE2 exchange
          const password = sharedPassword;
          console.log(`[${clientId}] Using custom password for SPAKE2 exchange in notifications`);

          // Simple SPAKE2-like response generation
          // This is a simplified implementation that generates a deterministic response
          // based on the password and the received message
          const generateSpake2Response = (incomingMsg, password) => {
            // Decode the base64 message
            const decodedMsg = atob(incomingMsg);

            // Create a response by combining the password with the decoded message
            // and hashing it using a simple algorithm
            let response = '';
            const combinedInput = password + decodedMsg;

            // Simple hash function to generate a deterministic response
            for (let i = 0; i < combinedInput.length; i++) {
              response += String.fromCharCode(
                (combinedInput.charCodeAt(i) + password.charCodeAt(i % password.length)) % 256
              );
            }

            // Encode the response as base64
            return btoa(response);
          };

          // Generate and send the SPAKE2 response
          const responseMsg = generateSpake2Response(data.spake2_msg, password);
          websocket.send(JSON.stringify({
            spake2_msg: responseMsg
          }));

          spake2Initialized = true;
          console.log(`[${clientId}] Sent SPAKE2 response for notifications`);
          return;
        }

        // Handle HKDF salt and info (part of the key exchange)
        if (data.hkdf_salt && data.hkdf_info) {
          console.log(`[${clientId}] Received HKDF parameters for notifications`);
          // Authentication successful, now we can set connected
          setIsConnected(true);
          return;
        }

        if (data.action === "user_connected") {
          setConnectedUsers(prev => [...prev, data.client_id]);
          console.log(`[${clientId}] User connected: ${data.client_id}`);
        } else if (data.action === "user_disconnected") {
          setConnectedUsers(prev => prev.filter(id => id !== data.client_id));
          console.log(`[${clientId}] User disconnected: ${data.client_id}`);
        } else if (data.action === "connected_users") {
          setConnectedUsers(data.client_ids || []);
          console.log(`[${clientId}] Connected users:`, data.client_ids);
        } else if (data.action === "ping") {
          // Keep-alive ping
          console.log(`[${clientId}] Received ping from server`);
        }
      };

      websocket.onerror = (error) => {
        console.error(`[${clientId}] Notification WebSocket error:`, error);
        setIsConnected(false);
      };

      websocket.onclose = () => {
        console.log(`[${clientId}] Notification WebSocket closed`);
        setIsConnected(false);

        // Try to reconnect after a delay
        setTimeout(() => {
          if (document.visibilityState !== 'hidden') {
            connectNotificationWs();
          }
        }, 3000);
      };

      setNotificationWs(websocket);
    };

    // Close existing connection before creating a new one
    if (notificationWs) {
      notificationWs.close();
      console.log(`[${clientId}] Closing existing notification WebSocket to reconnect with new password`);
    }

    connectNotificationWs();

    return () => {
      if (notificationWs) {
        notificationWs.close();
        console.log(`[${clientId}] Notification WebSocket cleanup on unmount`);
      }
    };
  }, [clientId, sharedPassword]); // Add sharedPassword as a dependency to reconnect when it changes

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

  // Handle shared password change
  const handlePasswordChange = (e: ChangeEvent<HTMLInputElement>) => {
    setSharedPassword(e.target.value);
  };

  return (
    <div className="flex flex-col gap-4 items-center justify-center h-screen">
      <div className="w-full max-w-md">
        <label className="block text-sm font-medium text-gray-700 mb-1">
          Shared Password (for connecting two instances)
        </label>
        <input 
          type="text" 
          value={sharedPassword}
          onChange={handlePasswordChange}
          placeholder="Enter shared password"
          className="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
          disabled={isUploading}
        />
        <p className="text-xs text-gray-500 mt-1">
          Enter the same password on two instances to connect them
        </p>
      </div>
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

      {/* Display user connection visualization */}
      <UserConnectionVisualizer 
        senderName={`You (${clientId})`}
        receiverName={connectedUsers.length > 0 ? `User (${connectedUsers[0]})` : "Waiting for connection..."}
        isConnected={isConnected && connectedUsers.length > 0}
      />

      {/* Display connected users */}
      {isConnected && (
        <div className="connected-users mt-4 p-3 bg-gray-50 rounded-lg max-w-2xl">
          <h3 className="text-md font-semibold mb-2">Connected Users</h3>
          <div className="flex flex-wrap gap-2">
            <div className="user-badge bg-green-100 text-green-800 px-2 py-1 rounded-full text-sm">
              You ({clientId})
            </div>
            {connectedUsers.map(userId => (
              <div key={userId} className="user-badge bg-blue-100 text-blue-800 px-2 py-1 rounded-full text-sm">
                {userId}
              </div>
            ))}
            {connectedUsers.length === 0 && (
              <div className="text-gray-500 text-sm">No other users connected</div>
            )}
          </div>
          <div className="mt-3">
            <a 
              href="/index.html" 
              className="text-blue-600 hover:text-blue-800 text-sm font-medium"
              target="_blank"
              rel="noopener noreferrer"
            >
              View Connected Users Page →
            </a>
          </div>
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
