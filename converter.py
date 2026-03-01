"""
3MF to GLB Converter
Converts 3D Manufacturing Format (.3mf) files to GL Binary (.glb) files
with accurate per-triangle color preservation.

Supports:
- Standard 3MF basematerials and colorgroups
- BambuStudio/Orca Slicer paint_color format
- Multi-file 3MF packages (sub-models in 3D/Objects/)
- Multi-part assemblies with component transforms
- Large files (streaming XML parser for 300MB+ models)
"""

import sys
import os
import zipfile
import json
import io
import xml.etree.ElementTree as ET
from collections import Counter
import numpy as np
import trimesh


# 3MF XML namespaces
NS_CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
NS_MATERIAL = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"
NS_PRODUCTION = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
NS_SLIC3R = "http://schemas.slic3r.org/3mf/2017/06"


def parse_hex_color(hex_str):
    """Parse a hex color string (#RRGGBB or #RRGGBBAA) to RGBA 0-255 list."""
    hex_str = hex_str.lstrip("#")
    r = int(hex_str[0:2], 16)
    g = int(hex_str[2:4], 16)
    b = int(hex_str[4:6], 16)
    a = int(hex_str[6:8], 16) if len(hex_str) >= 8 else 255
    return [r, g, b, a]


def decode_paint_color(paint_color_hex):
    """
    Decode BambuStudio/Slic3r paint_color attribute.
    
    The paint_color is a hex-nibble-encoded bit stream representing a recursive
    triangle subdivision tree (TriangleSelector format):
    - Convert hex string to bits (MSB first within each nibble)
    - Read 2-bit states from the bit stream (bit0 + bit1*2)
    - State 0 = subdivided into 4 children (read 4 more states recursively)
    - States 1-3 = painted with filament/extruder
    
    Returns the dominant state as a filament index (state value used directly).
    """
    if not paint_color_hex:
        return 0

    # Convert hex string to bit array (MSB first within each nibble)
    bits = []
    for ch in paint_color_hex:
        nibble = int(ch, 16)
        bits.append((nibble >> 3) & 1)
        bits.append((nibble >> 2) & 1)
        bits.append((nibble >> 1) & 1)
        bits.append(nibble & 1)

    # State counter: count leaf sub-triangles for each state
    state_counts = Counter()
    pos = [0]

    def read_state(depth=0):
        if pos[0] + 2 > len(bits):
            return
        # Read 2-bit state (LSB first: bit0 + bit1*2)
        state = bits[pos[0]] + bits[pos[0] + 1] * 2
        pos[0] += 2

        if state == 0:
            # Subdivided: 4 children follow
            if depth < 20:
                for _ in range(4):
                    read_state(depth + 1)
        else:
            state_counts[state] += 1

    read_state()

    if not state_counts:
        return 0

    # BambuStudio assigns paint states to non-default filaments:
    # state 1 → first non-default (left-click paint color)
    # state 2 → last non-default (right-click paint color)
    # state 3 → middle non-default
    # For a general mapping, we swap states 2 and 3 to match filament order
    dominant = state_counts.most_common(1)[0][0]
    REMAP = {1: 1, 2: 3, 3: 2}
    return REMAP.get(dominant, dominant)


def parse_transform(transform_str):
    """Parse a 3MF transform string (12 floats, column-major 3x4) into a 4x4 matrix."""
    t = [float(x) for x in transform_str.split()]
    mat = np.eye(4)
    mat[0, 0], mat[1, 0], mat[2, 0] = t[0], t[1], t[2]
    mat[0, 1], mat[1, 1], mat[2, 1] = t[3], t[4], t[5]
    mat[0, 2], mat[1, 2], mat[2, 2] = t[6], t[7], t[8]
    mat[0, 3], mat[1, 3], mat[2, 3] = t[9], t[10], t[11]
    return mat


def get_filament_colors(zip_file):
    """Extract filament colors from BambuStudio or PrusaSlicer settings."""
    # Try BambuStudio project_settings.config (JSON)
    for name in zip_file.namelist():
        if name.lower() == "metadata/project_settings.config":
            try:
                data = zip_file.read(name).decode("utf-8")
                settings = json.loads(data)
                hex_colors = settings.get("filament_colour", [])
                if hex_colors:
                    return [parse_hex_color(hc) for hc in hex_colors]
            except (json.JSONDecodeError, KeyError, ValueError):
                pass

    # Try PrusaSlicer Slic3r_PE.config (INI-style)
    for name in zip_file.namelist():
        if name.lower() in ("metadata/slic3r_pe.config", "metadata/prusa slicer.config"):
            try:
                data = zip_file.read(name).decode("utf-8")
                import re as _re
                # Look for extruder_colour first, then filament_colour
                for key in ["extruder_colour", "filament_colour"]:
                    match = _re.search(rf"; {key} = (.+)", data)
                    if match:
                        raw = match.group(1).strip()
                        hex_colors = [c.strip() for c in raw.split(";") if c.strip()]
                        if hex_colors and any(c != hex_colors[0] for c in hex_colors):
                            return [parse_hex_color(hc) for hc in hex_colors]
            except (KeyError, ValueError):
                pass

    return None


def get_object_names(zip_file):
    """Extract proper object names from BambuStudio model settings."""
    names = {}
    for name in zip_file.namelist():
        if name.lower() == "metadata/model_settings.config":
            try:
                data = zip_file.read(name).decode("utf-8")
                # Simple regex extraction to avoid complex XML namespaces
                import re as _re
                parts = _re.findall(r'<part id="([^"]+)"[^>]*>.*?<metadata key="name" value="([^"]+)"', data, _re.DOTALL)
                for part_id, part_name in parts:
                    if part_name.lower().endswith(".stl") or part_name.lower().endswith(".obj"):
                        part_name = part_name[:-4]
                    names[part_id] = part_name
            except Exception:
                pass
            break
    return names


def stream_parse_model(file_obj, basematerials, colorgroups, filament_colors, object_names=None):
    """
    Streaming XML parser for large 3MF model files.
    Uses iterparse to process elements one at a time without loading
    the entire DOM into memory.
    
    Returns: objects dict, components dict
    """
    if object_names is None:
        object_names = {}

    objects = {}      # obj_id → mesh data dict
    components = {}   # obj_id → list of component refs

    # State tracking
    current_obj_id = None
    current_obj_name = None
    current_obj_pid = None
    current_obj_pindex = None
    in_mesh = False
    in_vertices = False
    in_triangles = False
    mesh_has_colors = False

    vertices = []
    faces = []
    face_colors = []

    default_color = [200, 200, 200, 255]
    default_filament_color = filament_colors[0] if filament_colors else default_color

    obj_count = 0
    tri_count = 0

    for event, elem in ET.iterparse(file_obj, events=("start", "end")):
        tag = elem.tag

        # Strip namespace
        if "}" in tag:
            tag = tag.split("}", 1)[1]

        if event == "start":
            if tag == "object":
                current_obj_id = elem.get("id")
                default_name = f"Object_{current_obj_id}"
                current_obj_name = elem.get("name") or object_names.get(current_obj_id) or default_name
                current_obj_pid = elem.get("pid")
                current_obj_pindex = elem.get("pindex")
                vertices = []
                faces = []
                face_colors = []
                mesh_has_colors = False
                in_mesh = False

            elif tag == "mesh":
                in_mesh = True

            elif tag == "vertices":
                in_vertices = True

            elif tag == "triangles":
                in_triangles = True

            elif tag == "vertex" and in_vertices:
                vertices.append([
                    float(elem.get("x", 0)),
                    float(elem.get("y", 0)),
                    float(elem.get("z", 0)),
                ])

            elif tag == "triangle" and in_triangles:
                faces.append([
                    int(elem.get("v1")),
                    int(elem.get("v2")),
                    int(elem.get("v3")),
                ])
                tri_count += 1

                color = None

                # BambuStudio paint_color or PrusaSlicer mmu_segmentation
                paint_color = elem.get("paint_color")
                if paint_color is None:
                    # Try PrusaSlicer slic3rpe:mmu_segmentation
                    paint_color = elem.get(f"{{{NS_SLIC3R}}}mmu_segmentation")
                if paint_color is None:
                    # Try without namespace prefix (iterparse may strip it)
                    for attr_name in elem.attrib:
                        if 'mmu_segmentation' in attr_name:
                            paint_color = elem.attrib[attr_name]
                            break
                if paint_color is not None and filament_colors:
                    filament_idx = decode_paint_color(paint_color)
                    if 0 <= filament_idx < len(filament_colors):
                        color = filament_colors[filament_idx]

                # Standard basematerials / colorgroups
                if color is None:
                    pid = elem.get("pid", current_obj_pid)
                    p1 = elem.get("p1")
                    color_index = int(p1) if p1 is not None else (int(current_obj_pindex) if current_obj_pindex is not None else None)

                    if pid is not None and color_index is not None:
                        if pid in basematerials:
                            mat_list = basematerials[pid]
                            if 0 <= color_index < len(mat_list):
                                color = mat_list[color_index]
                        elif pid in colorgroups:
                            cg_list = colorgroups[pid]
                            if 0 <= color_index < len(cg_list):
                                color = cg_list[color_index]

                if color is not None:
                    mesh_has_colors = True
                else:
                    color = default_filament_color

                face_colors.append(color)

                # Print progress every 500K triangles
                if tri_count % 500000 == 0:
                    print(f"    ... {tri_count:,} triangles parsed")

            elif tag == "component":
                if current_obj_id not in components:
                    components[current_obj_id] = []
                comp_data = {
                    "objectid": elem.get("objectid"),
                    "path": elem.get(f"{{{NS_PRODUCTION}}}path", elem.get("path")),
                }
                transform_str = elem.get("transform")
                comp_data["transform"] = parse_transform(transform_str) if transform_str else np.eye(4)
                components[current_obj_id].append(comp_data)

            elif tag == "basematerials":
                pass  # handled on end

            elif tag == "base" and elem.getparent if hasattr(elem, 'getparent') else False:
                pass

        elif event == "end":
            if tag == "vertices":
                in_vertices = False

            elif tag == "triangles":
                in_triangles = False

            elif tag == "mesh":
                in_mesh = False

            elif tag == "object":
                if faces:
                    obj_count += 1
                    objects[current_obj_id] = {
                        "id": current_obj_id,
                        "name": current_obj_name,
                        "vertices": np.array(vertices, dtype=np.float64),
                        "faces": np.array(faces, dtype=np.int64),
                        "face_colors": np.array(face_colors, dtype=np.uint8) if mesh_has_colors else None,
                        "default_color": default_filament_color,
                    }
                    print(f"    Object '{current_obj_name}': {len(faces):,} triangles, {len(vertices):,} vertices")
                current_obj_id = None
                vertices = []
                faces = []
                face_colors = []
                mesh_has_colors = False

            elif tag == "basematerials":
                # Parse basematerials from the element
                bm_id = elem.get("id")
                colors = []
                for base in elem.findall(f"{{{NS_CORE}}}base"):
                    display_color = base.get("displaycolor", "#FFFFFFFF")
                    colors.append(parse_hex_color(display_color))
                # Also try without namespace
                if not colors:
                    for base in elem.findall("base"):
                        display_color = base.get("displaycolor", "#FFFFFFFF")
                        colors.append(parse_hex_color(display_color))
                if colors:
                    basematerials[bm_id] = colors

            elif tag == "colorgroup":
                cg_id = elem.get("id")
                colors = []
                for c in elem.findall(f"{{{NS_MATERIAL}}}color"):
                    colors.append(parse_hex_color(c.get("color", "#FFFFFFFF")))
                if not colors:
                    for c in elem.findall(f"{{{NS_CORE}}}color"):
                        colors.append(parse_hex_color(c.get("color", "#FFFFFFFF")))
                if not colors:
                    for c in elem.findall("color"):
                        colors.append(parse_hex_color(c.get("color", "#FFFFFFFF")))
                if colors:
                    colorgroups[cg_id] = colors

            # Free memory for processed elements
            elem.clear()

    print(f"    Parsed {obj_count} object(s), {tri_count:,} total triangles")
    return objects, components


def parse_main_model(xml_data):
    """Parse the main 3dmodel.model to get build items and top-level components."""
    root = ET.fromstring(xml_data)
    
    components = {}
    resources_elem = root.find(f"{{{NS_CORE}}}resources")
    if resources_elem is not None:
        for obj_elem in resources_elem.findall(f"{{{NS_CORE}}}object"):
            obj_id = obj_elem.get("id")
            comps_elem = obj_elem.find(f"{{{NS_CORE}}}components")
            if comps_elem is not None:
                comp_list = []
                for comp in comps_elem.findall(f"{{{NS_CORE}}}component"):
                    comp_data = {
                        "objectid": comp.get("objectid"),
                        "path": comp.get(f"{{{NS_PRODUCTION}}}path", comp.get("path")),
                    }
                    transform_str = comp.get("transform")
                    comp_data["transform"] = parse_transform(transform_str) if transform_str else np.eye(4)
                    comp_list.append(comp_data)
                components[obj_id] = comp_list

    build_items = []
    build_elem = root.find(f"{{{NS_CORE}}}build")
    if build_elem is not None:
        for item in build_elem.findall(f"{{{NS_CORE}}}item"):
            bi = {"objectid": item.get("objectid")}
            transform_str = item.get("transform")
            bi["transform"] = parse_transform(transform_str) if transform_str else np.eye(4)
            build_items.append(bi)

    # Also get basematerials/colorgroups from main model
    basematerials = {}
    colorgroups = {}
    if resources_elem is not None:
        for bm_elem in resources_elem.findall(f"{{{NS_CORE}}}basematerials"):
            bm_id = bm_elem.get("id")
            colors = []
            for base in bm_elem.findall(f"{{{NS_CORE}}}base"):
                colors.append(parse_hex_color(base.get("displaycolor", "#FFFFFFFF")))
            if colors:
                basematerials[bm_id] = colors
        for cg_elem in resources_elem.findall(f"{{{NS_MATERIAL}}}colorgroup"):
            cg_id = cg_elem.get("id")
            colors = []
            for c in cg_elem.findall(f"{{{NS_MATERIAL}}}color"):
                colors.append(parse_hex_color(c.get("color", "#FFFFFFFF")))
            if colors:
                colorgroups[cg_id] = colors


    return build_items, components, basematerials, colorgroups


def convert_3mf_to_glb(input_path, output_path=None, extract_glb=True, extract_thumbnails=True):
    """Main conversion function."""
    if output_path is None:
        base = os.path.splitext(input_path)[0]
        output_path = base + ".glb"

    print(f"Opening: {input_path}")

    with zipfile.ZipFile(input_path, "r") as zf:
        # --- Extract Thumbnails ---
        if extract_thumbnails:
            for filename in zf.namelist():
                if filename.lower().endswith(".png"):
                    img_data = zf.read(filename)
                    # Ensure the name is unique but ties to the input.
                    # e.g. "AutumnwingColors_plate_1.png"
                    img_base = os.path.basename(filename)
                    base_input_name = os.path.splitext(os.path.basename(input_path))[0]
                    img_out_path = os.path.join(os.path.dirname(input_path), f"{base_input_name}_{img_base}")
                    
                    with open(img_out_path, "wb") as f:
                        f.write(img_data)
                    print(f"  Extracted thumbnail: {os.path.basename(img_out_path)}")

        if not extract_glb:
            if not extract_thumbnails:
                print("ERROR: No output options selected.")
                return False
            return True

        # --- Detect filament colors ---
        filament_colors = get_filament_colors(zf)
        if filament_colors:
            print(f"  BambuStudio filament colors:")
            for i, c in enumerate(filament_colors):
                print(f"    Filament {i+1}: #{c[0]:02X}{c[1]:02X}{c[2]:02X}")

        # Extract object names from model settings if available
        object_names = get_object_names(zf)

        # --- Find model files ---
        model_files = []
        main_model_name = None
        for name in zf.namelist():
            if name.lower().endswith(".model"):
                model_files.append(name)
                if name.lower() == "3d/3dmodel.model":
                    main_model_name = name

        if not model_files:
            print("ERROR: No model files found.")
            return False

        if model_files and extract_glb:
            print(f"  Found {len(model_files)} model file(s)")
        for name in model_files:
            info = zf.getinfo(name)
            size_mb = info.file_size / (1024 * 1024)
            print(f"    {name} ({size_mb:.1f} MB)")

        # --- Parse main model for build items and components ---
        main_build_items = []
        main_components = {}
        basematerials = {}
        colorgroups = {}

        if main_model_name:
            print(f"  Parsing {main_model_name}...")
            main_xml = zf.read(main_model_name).decode("utf-8")
            main_build_items, main_components, basematerials, colorgroups = parse_main_model(main_xml)
            print(f"    {len(main_build_items)} build item(s), {len(main_components)} component group(s)")

        # --- Parse sub-model files (streaming for large files) ---
        all_objects = {}
        sub_components = {}

        if extract_glb:
            for model_name in model_files:
                if model_name == main_model_name:
                    continue

                print(f"  Parsing {model_name} (streaming)...")
                with zf.open(model_name) as f:
                    objects, comps = stream_parse_model(
                        f, basematerials, colorgroups, filament_colors, object_names
                    )
                    for obj_id, obj_data in objects.items():
                        all_objects[(model_name, obj_id)] = obj_data
                    sub_components.update(comps)

        # Also parse main model objects if it has meshes directly
        if main_model_name and main_model_name not in [n for n in model_files if n != main_model_name]:
            # Check if main model has direct mesh objects
            main_xml_bytes = zf.read(main_model_name)
            with io.BytesIO(main_xml_bytes) as f:
                objects, comps = stream_parse_model(
                    f, basematerials, colorgroups, filament_colors, object_names
                )
                for obj_id, obj_data in objects.items():
                    all_objects[("main", obj_id)] = obj_data
                sub_components.update(comps)

        print(f"  Total: {len(basematerials)} basematerial group(s), {len(colorgroups)} color group(s)")

    if not extract_glb:
        return True
        
    # --- Resolve build items → meshes ---
    all_meshes = []

    def resolve(path, obj_id, transform):
        """Recursively resolve objects and components."""
        # Look for mesh data
        obj_data = all_objects.get((path, obj_id))
        if obj_data is None:
            # Try normalized path
            for key, data in all_objects.items():
                if key[1] == obj_id:
                    obj_data = data
                    break

        if obj_data is not None:
            vertices = obj_data["vertices"]
            faces = obj_data["faces"]
            color_data = obj_data.get("face_colors")
            
            if color_data is not None:
                # We have per-triangle colors, build a mesh with vertex colors
                face_colors_np = np.array(color_data, dtype=np.uint8)
                mesh = trimesh.Trimesh(
                    vertices=vertices,
                    faces=faces,
                    face_colors=face_colors_np,
                    process=False
                )
                # Unmerge vertices so each triangle has unique vertices and sharp colors
                mesh.unmerge_vertices()
            else:
                # Uniform uncolored mesh! Use a PBRMaterial instead of baked vertex colors
                # This allows it to be easily recolored in web viewers via material.color
                default_color = obj_data.get("default_color", [200, 200, 200, 255])
                # convert 0-255 to 0.0-1.0
                base_color = [c / 255.0 for c in default_color]
                mat = trimesh.visual.material.PBRMaterial(baseColorFactor=base_color)
                vis = trimesh.visual.TextureVisuals(material=mat)
                
                mesh = trimesh.Trimesh(
                    vertices=vertices,
                    faces=faces,
                    visual=vis,
                    process=False
                )

            mesh.apply_transform(transform)
            mesh.metadata["name"] = obj_data["name"]
            all_meshes.append(mesh)

        # Check components from main model
        comp_list = main_components.get(obj_id) or sub_components.get(obj_id)
        if comp_list:
            for comp in comp_list:
                child_id = comp["objectid"]
                child_path = comp.get("path")
                child_transform = transform @ comp["transform"]

                if child_path:
                    child_path = child_path.lstrip("/")
                else:
                    child_path = path

                resolve(child_path, child_id, child_transform)

    if main_build_items:
        for bi in main_build_items:
            resolve("3D/3dmodel.model", bi["objectid"], bi["transform"])
    else:
        for (path, obj_id), obj_data in all_objects.items():
            vertices = obj_data["vertices"]
            faces = obj_data["faces"]
            color_data = obj_data.get("face_colors")
            
            if color_data is not None:
                # We have per-triangle colors, build a mesh with vertex colors
                face_colors_np = np.array(color_data, dtype=np.uint8)
                mesh = trimesh.Trimesh(
                    vertices=vertices,
                    faces=faces,
                    face_colors=face_colors_np,
                    process=False
                )
                # Unmerge vertices so each triangle has unique vertices and sharp colors
                mesh.unmerge_vertices()
            else:
                # Uniform uncolored mesh! Use a PBRMaterial instead of baked vertex colors
                # This allows it to be easily recolored in web viewers via material.color
                default_color = obj_data.get("default_color", [200, 200, 200, 255])
                # convert 0-255 to 0.0-1.0
                base_color = [c / 255.0 for c in default_color]
                mat = trimesh.visual.material.PBRMaterial(baseColorFactor=base_color)
                vis = trimesh.visual.TextureVisuals(material=mat)
                
                mesh = trimesh.Trimesh(
                    vertices=vertices,
                    faces=faces,
                    visual=vis,
                    process=False
                )

            mesh.metadata["name"] = obj_data["name"]
            all_meshes.append(mesh)

    if not all_meshes:
        print("ERROR: No meshes found.")
        return False

    print(f"  Resolved {len(all_meshes)} mesh(es)")

    # --- Unmerge faces for sharp per-face colors ---
    print("  Preparing meshes for GLB export...")
    prepared_meshes = []
    total_faces = 0
    total_vertices = 0

    for mesh in all_meshes:
        total_faces += len(mesh.faces)
        
        # If the mesh already has a PBRMaterial, it means it was uncolored
        # and we don't need to unmerge faces or set vertex colors.
        if isinstance(mesh.visual, trimesh.visual.TextureVisuals) and isinstance(mesh.visual.material, trimesh.visual.material.PBRMaterial):
            new_mesh = mesh
        else:
            # Otherwise, it has face_colors (or default colors baked in)
            # and we need to unmerge for sharp per-face colors.
            fc = mesh.visual.face_colors.copy()

            new_vertices = mesh.vertices[mesh.faces.flatten()]
            new_faces = np.arange(len(new_vertices)).reshape(-1, 3)
            vertex_colors = np.repeat(fc, 3, axis=0)

            new_mesh = trimesh.Trimesh(
                vertices=new_vertices,
                faces=new_faces,
                vertex_colors=vertex_colors,
                process=False,
            )
        
        if hasattr(mesh, "metadata") and "name" in mesh.metadata:
            new_mesh.metadata["name"] = mesh.metadata["name"]

        total_vertices += len(new_mesh.vertices)
        prepared_meshes.append(new_mesh)

    print(f"  Total triangles: {total_faces:,}")
    print(f"  Total vertices: {total_vertices:,}")

    # Export the GLB
    # The actual export was previously handled by pygltflib, but here we just need to use trimesh
    # to combine the meshes and save the result.
    
    scene = trimesh.Scene(prepared_meshes)
    scene.export(output_path)

    print(f"\nSaved: {output_path}")
    return True


def process_file(input_path, output_path=None, extract_glb=True, extract_thumbnails=True):
    if not os.path.isfile(input_path):
        print(f"ERROR: File not found: {input_path}")
        return False

    print("=" * 50)
    print(f"  Converting: {os.path.basename(input_path)}")
    print("=" * 50)

    try:
        success = convert_3mf_to_glb(input_path, output_path, extract_glb, extract_thumbnails)

        if success:
            print(f"\n✅ Conversion complete for {os.path.basename(input_path)}!")
        else:
            print(f"\n❌ Conversion failed for {os.path.basename(input_path)}.")
        return success
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n❌ Exception during conversion for {os.path.basename(input_path)}.")
        return False

class GUI:
    def __init__(self, root):
        self.root = root
        self.root.title("3MF to GLB Converter")
        self.root.geometry("700x500")
        self.root.configure(padx=10, pady=10)

        import tkinter as tk
        from tkinter import ttk, filedialog, scrolledtext
        import queue

        # Thread-safe queue for log messages
        self.log_queue = queue.Queue()

        # Top frame - buttons and options
        top_frame = ttk.Frame(root)
        top_frame.pack(fill=tk.X, pady=(0, 10))
        
        btn_frame = ttk.Frame(top_frame)
        btn_frame.pack(side=tk.LEFT)

        ttk.Button(btn_frame, text="Add 3MF Files", command=self.add_files).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="Clear List", command=self.clear_files).pack(side=tk.LEFT, padx=(0, 5))
        
        options_frame = ttk.LabelFrame(top_frame, text="Output Options")
        options_frame.pack(side=tk.LEFT, padx=(15, 0))
        
        self.var_extract_glb = tk.BooleanVar(value=True)
        self.var_extract_thumbnails = tk.BooleanVar(value=True)
        
        ttk.Checkbutton(options_frame, text="GLB File", variable=self.var_extract_glb).pack(side=tk.LEFT, padx=5, pady=2)
        ttk.Checkbutton(options_frame, text="Thumbnails", variable=self.var_extract_thumbnails).pack(side=tk.LEFT, padx=5, pady=2)
        
        self.convert_btn = ttk.Button(top_frame, text="Convert All", command=self.start_conversion)
        self.convert_btn.pack(side=tk.RIGHT)

        # Middle frame - file list
        list_frame = ttk.LabelFrame(root, text="Files to Convert")
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        self.listbox = tk.Listbox(list_frame, selectmode=tk.EXTENDED)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        ysb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listbox.yview)
        ysb.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.configure(yscrollcommand=ysb.set)

        # Bottom frame - log
        log_frame = ttk.LabelFrame(root, text="Conversion Log")
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=10, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.input_paths = []
        
        # Start the UI polling loop for log messages
        self.root.after(50, self.poll_log_queue)
        
    def poll_log_queue(self):
        import tkinter as tk
        try:
            while True:
                text = self.log_queue.get_nowait()
                self.log_text.configure(state=tk.NORMAL)
                self.log_text.insert(tk.END, text)
                self.log_text.see(tk.END)
                self.log_text.configure(state=tk.DISABLED)
                self.log_text.update_idletasks() # Force redraw immediately
        except BaseException: # queue.Empty is not always easily catchable here depending on imports
            pass
        finally:
            self.root.after(50, self.poll_log_queue)

    def log(self, text):
        self.log_queue.put(text)

    def _update_listbox(self, index, text):
        self.listbox.delete(index)
        self.listbox.insert(index, text)
        self.listbox.see(index)
        self.listbox.update_idletasks()

    def add_files(self):
        import tkinter as tk
        from tkinter import filedialog
        file_paths = filedialog.askopenfilenames(
            title="Select 3MF Files",
            filetypes=[("3MF Files", "*.3mf"), ("All Files", "*.*")],
        )
        for p in file_paths:
            if p not in self.input_paths:
                self.input_paths.append(p)
                import os
                self.listbox.insert(tk.END, os.path.basename(p))

    def clear_files(self):
        import tkinter as tk
        self.input_paths.clear()
        self.listbox.delete(0, tk.END)
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def start_conversion(self):
        import threading
        if not self.input_paths:
            self.log("No files selected to convert.\n")
            return
        
        self.convert_btn.configure(state="disabled")
        import os
        for i, path in enumerate(self.input_paths):
            self._update_listbox(i, os.path.basename(path))
            
        threading.Thread(target=self.run_conversion, daemon=True).start()

    def run_conversion(self):
        import sys
        import os
        
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        
        class StdoutRedirector:
            def __init__(self, log_func):
                self.log_func = log_func
            def write(self, s):
                if s:
                    self.log_func(s)
            def flush(self):
                pass
        
        sys.stdout = StdoutRedirector(self.log)
        sys.stderr = sys.stdout
        
        try:
            success_count = 0
            for i, path in enumerate(self.input_paths):
                if i > 0:
                    print("\n")
                
                filename = os.path.basename(path)
                self.root.after(0, lambda idx=i, name=filename: self._update_listbox(idx, f"⏳ [Processing] {name}"))

                if process_file(path, None, extract_glb=self.var_extract_glb.get(), extract_thumbnails=self.var_extract_thumbnails.get()):
                    success_count += 1
                    self.root.after(0, lambda idx=i, name=filename: self._update_listbox(idx, f"✅ [Done] {name}"))
                else:
                    self.root.after(0, lambda idx=i, name=filename: self._update_listbox(idx, f"❌ [Failed] {name}"))
                    
            print("\n" + "=" * 50)
            print(f"Finished processing {len(self.input_paths)} file(s).")
            print(f"Successful: {success_count} | Failed: {len(self.input_paths) - success_count}")
            print("=" * 50)
        except Exception as e:
            import traceback
            traceback.print_exc()
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            self.root.after(0, lambda: self.convert_btn.configure(state="normal"))


def main():
    if len(sys.argv) > 1:
        # Command-line / drag-and-drop mode
        input_paths = []
        output_path_override = None

        if len(sys.argv) == 2 and os.path.isdir(sys.argv[1]):
            directory = sys.argv[1]
            for file in os.listdir(directory):
                if file.lower().endswith(".3mf"):
                    input_paths.append(os.path.join(directory, file))
        else:
            for arg in sys.argv[1:]:
                if not arg.lower().endswith(".3mf") and len(input_paths) == 1 and len(sys.argv) == 3:
                     output_path_override = arg
                     continue
                if os.path.isfile(arg) and arg.lower().endswith(".3mf"):
                    input_paths.append(arg)

        if not input_paths:
            print("ERROR: No 3MF files provided or found.")
            if getattr(sys, "frozen", False):
                try:
                    input("Press Enter to exit...")
                except Exception:
                    pass
            return

        print(f"Found {len(input_paths)} file(s) to process.")
        success_count = 0
        for i, path in enumerate(input_paths):
            if i > 0:
                print("\n")
            out_path = output_path_override if len(input_paths) == 1 else None
            if process_file(path, out_path):
                success_count += 1
                
        print("\n" + "=" * 50)
        print(f"Finished processing {len(input_paths)} file(s).")
        print(f"Successful: {success_count} | Failed: {len(input_paths) - success_count}")
        print("=" * 50)

        if getattr(sys, "frozen", False):
            try:
                input("\nPress Enter to exit...")
            except Exception:
                pass
            
    else:
        # GUI mode
        try:
            import tkinter as tk
            root = tk.Tk()
            gui = GUI(root)
            root.mainloop()
        except ImportError:
            print("Tkinter not available. Usage: converter.py <input1.3mf> [input2.3mf...] OR directory")

if __name__ == "__main__":
    main()
