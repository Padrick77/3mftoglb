from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import tempfile
import shutil
import asyncio
from converter import convert_3mf_to_glb

app = FastAPI(title="3MF/STL to GLB Converter API", description="API to convert 3MF/STL models to GLB for web viewing.")

# Enable CORS for the frontend website to make requests to this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Update this to your specific website domains in production!
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/convert")
async def convert_file(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".3mf", ".stl")):
        raise HTTPException(status_code=400, detail="File must be a .3mf or .stl file")
        
    # Create temp directory to avoid filename collisions across concurrent requests
    temp_dir = tempfile.mkdtemp()
    
    input_filename = file.filename
    output_filename = input_filename.rsplit(".", 1)[0] + ".glb"
    
    input_path = os.path.join(temp_dir, input_filename)
    output_path = os.path.join(temp_dir, output_filename)
    
    try:
        # Save the uploaded file to disk
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # Run conversion in a separate thread so it doesn't block the ASGI event loop
        loop = asyncio.get_event_loop()
        ext = os.path.splitext(input_filename)[1].lower()
        if ext == ".stl":
            from converter import convert_stl_to_glb
            success = await loop.run_in_executor(
                None, 
                convert_stl_to_glb, 
                input_path, 
                output_path, 
                True,   # extract_glb
                False   # extract_thumbnails
            )
        else:
            success = await loop.run_in_executor(
                None, 
                convert_3mf_to_glb, 
                input_path, 
                output_path, 
                True,   # extract_glb
                False   # extract_thumbnails
            )
        
        if not success or not os.path.exists(output_path):
            raise Exception("Conversion returned false or produced no output file")
            
        # Clean up the temp directory after the response is sent back to the user
        background_tasks.add_task(shutil.rmtree, temp_dir, ignore_errors=True)
        
        return FileResponse(
            path=output_path, 
            filename=output_filename,
            media_type="model/gltf-binary"
        )
        
    except Exception as e:
        # Clean up immediately on error
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health_check():
    return {"status": "ok", "message": "3MF to GLB Converter Service is running"}
