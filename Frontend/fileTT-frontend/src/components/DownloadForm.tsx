import { useState, ChangeEvent } from "react";
import axios from "axios";

function DownloadForm() {
  const [filename, setFilename] = useState<string>("");

  const handleDownload = async () => {
    if (!filename) return;

    try {
      const response = await axios.get(`http://localhost:8000/download/${filename}`, {
        responseType: "blob",
      });

      // Create a temporary URL for the blob
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", filename); // Set default download name
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (err) {
      console.error(err);
      alert("Download failed");
    }
  };

  const handleChange = (e: ChangeEvent<HTMLInputElement>) => {
    setFilename(e.target.value);
  };

  return (
    <div className="flex flex-col gap-4 items-center justify-center h-screen">
      <input
        type="text"
        placeholder="Enter filename to download"
        value={filename}
        onChange={handleChange}
        className="border p-2 rounded"
      />
      <button onClick={handleDownload} className="bg-green-500 text-white p-2 rounded">
        Download File
      </button>
    </div>
  );
}

export default DownloadForm;