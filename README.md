# 3MF to GLB Converter

A Windows tool that converts 3D Manufacturing Format (3MF) files to GL Binary (GLB) files with **accurate per-triangle color preservation**.

## Why?

Existing converters lose color data. This tool properly handles:
- **basematerials** — per-triangle material color assignments
- **colorgroups** — per-vertex/per-triangle sRGB color assignments  
- **Multi-part models** — components with transforms
- **Sharp face colors** — no interpolation at shared edges

## Features

- **Accurate Colors:** Preserves per-triangle `basematerials`, `colorgroups`, and BambuStudio/PrusaSlicer specific `paint_color`/`mmu_segmentation` data.
- **Batch Processing:** Convert hundreds of files at once via the GUI or command line.
- **Thumbnail Extraction:** Optionally extracts embedded `.png` cover photos from the 3MF archive.
- **Web-Optimized:** Unpainted models are exported using standard PBRMaterials (instead of baked vertex colors) to drastically reduce GLB file size and allow dynamic recoloring in web viewers.

## Usage

### 1. Standalone GUI (Recommended)
Double-click `3mf2glb.exe` with no arguments to open the desktop interface:
- Click **Add 3MF Files** to select multiple files at once.
- Check/Uncheck **GLB File** or **Thumbnails** depending on what outputs you want.
- Click **Convert All** to process the queue. The live log will show progress and status.

### 2. Drag & Drop
Drag one or more `.3mf` files onto `3mf2glb.exe`. The app will process them in the background and save the `.glb` files next to the originals.

### 3. Command Line (Batch & Single)
```bash
# Convert a single file
3mf2glb.exe model.3mf

# Convert a single file and specify output name
3mf2glb.exe model.3mf output.glb

# Convert an entire directory of 3MF files
3mf2glb.exe C:\path\to\folder\

# Convert multiple specific files
3mf2glb.exe model1.3mf model2.3mf model3.3mf
```

## Building from Source

```bash
pip install -r requirements.txt
python converter.py model.3mf
```

### Build Executable
```bash
build.bat
```
The executable will be in the `dist/` folder.

## How It Works

1. Opens the 3MF file (ZIP archive containing XML)
2. Parses the `3dmodel.model` XML to extract vertices, triangles, and color properties
3. Resolves per-triangle colors from `basematerials` and `colorgroup` references
4. Builds a trimesh with `face_colors` and unmerges shared vertices for sharp per-face colors
5. Exports to GLB format
