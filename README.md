# 3MF to GLB Converter

A Windows tool that converts 3D Manufacturing Format (3MF) files to GL Binary (GLB) files with **accurate per-triangle color preservation**.

## Why?

Existing converters lose color data. This tool properly handles:
- **basematerials** — per-triangle material color assignments
- **colorgroups** — per-vertex/per-triangle sRGB color assignments  
- **Multi-part models** — components with transforms
- **Sharp face colors** — no interpolation at shared edges

## Usage

### Drag & Drop
Drag a `.3mf` file onto `3mf2glb.exe` — the `.glb` file will be saved next to the original.

### Command Line
```
3mf2glb.exe model.3mf
3mf2glb.exe model.3mf output.glb
```

### Double-Click
Run `3mf2glb.exe` with no arguments to open a file picker dialog.

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
