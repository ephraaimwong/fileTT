import { useState, ChangeEvent } from "react";
import axios from "axios";

function UploadForm() {
  const [file, setFile] = useState<File | null>(null);

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      setFile(e.target.files[0]);
    }
  };

  const handleUpload = async () => {
    if (!file) return;

    const formData = new FormData();
    formData.append("file", file);

    try {
      const response = await axios.post("http://localhost:8000/upload/", formData, {
        headers: {
          "Content-Type": "multipart/form-data",
        },
      });
      alert(response.data.message);
    } catch (err) {
      console.error(err);
      alert("Upload failed");
    }
  };

  return (
    <div className="flex flex-col gap-4 items-center justify-center h-screen">
      <input type="file" onChange={handleFileChange} />
      <button onClick={handleUpload} className="bg-blue-500 text-white p-2 rounded">
        Upload File
      </button>
    </div>
  );
}

export default UploadForm;