"""
3MF to GLB Converter
Converts 3D Manufacturing Format (.3mf) files to GL Binary (.glb) files
with accurate per-triangle color preservation.
"""

import sys
import os
import zipfile
import xml.etree.ElementTree as ET
import numpy as np
import trimesh


# 3MF XML namespaces
NS_CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
NS_MATERIAL = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"

NAMESPACES = {
    "core": NS_CORE,
    "m": NS_MATERIAL,
}


def parse_hex_color(hex_str):
    """Parse a hex color string (#RRGGBB or #RRGGBBAA) to RGBA 0-255 array."""
    hex_str = hex_str.lstrip("#")
    r = int(hex_str[0:2], 16)
    g = int(hex_str[2:4], 16)
    b = int(hex_str[4:6], 16)
    a = int(hex_str[6:8], 16) if len(hex_str) >= 8 else 255
    return [r, g, b, a]


def find_model_xml(zip_file):
    """Find the 3D model XML file inside the 3MF ZIP archive."""
    # Standard location
    for name in zip_file.namelist():
        if name.lower() == "3d/3dmodel.model":
            return name
    # Fallback: any .model file
    for name in zip_file.namelist():
        if name.lower().endswith(".model"):
            return name
    return None


def parse_basematerials(resources_elem):
    """Parse <basematerials> elements → dict of id → list of RGBA colors."""
    materials = {}

    for bm_elem in resources_elem.findall(f"{{{NS_CORE}}}basematerials"):
        bm_id = bm_elem.get("id")
        colors = []
        for base in bm_elem.findall(f"{{{NS_CORE}}}base"):
            display_color = base.get("displaycolor", "#FFFFFFFF")
            colors.append(parse_hex_color(display_color))
        materials[bm_id] = colors

    return materials


def parse_colorgroups(resources_elem):
    """Parse <colorgroup> elements → dict of id → list of RGBA colors."""
    colorgroups = {}

    # Try material extension namespace first, then core
    for ns in [NS_MATERIAL, NS_CORE]:
        for cg_elem in resources_elem.findall(f"{{{ns}}}colorgroup"):
            cg_id = cg_elem.get("id")
            colors = []
            for color_elem in cg_elem.findall(f"{{{ns}}}color"):
                color_val = color_elem.get("color", "#FFFFFFFF")
                colors.append(parse_hex_color(color_val))
            if colors:
                colorgroups[cg_id] = colors

    return colorgroups


def parse_object(obj_elem, basematerials, colorgroups):
    """
    Parse a single <object> element with type='model' that contains a <mesh>.
    Returns dict with vertices, faces, face_colors, and object-level defaults.
    """
    obj_id = obj_elem.get("id")
    obj_name = obj_elem.get("name", f"Object_{obj_id}")
    obj_pid = obj_elem.get("pid")       # default property group
    obj_pindex = obj_elem.get("pindex")  # default property index

    mesh_elem = obj_elem.find(f"{{{NS_CORE}}}mesh")
    if mesh_elem is None:
        return None

    # --- Parse vertices ---
    vertices_elem = mesh_elem.find(f"{{{NS_CORE}}}vertices")
    vertices = []
    if vertices_elem is not None:
        for v in vertices_elem.findall(f"{{{NS_CORE}}}vertex"):
            x = float(v.get("x", 0))
            y = float(v.get("y", 0))
            z = float(v.get("z", 0))
            vertices.append([x, y, z])
    vertices = np.array(vertices, dtype=np.float64)

    # --- Parse triangles and per-triangle color properties ---
    triangles_elem = mesh_elem.find(f"{{{NS_CORE}}}triangles")
    faces = []
    face_colors = []
    default_color = [200, 200, 200, 255]  # light gray fallback

    if triangles_elem is not None:
        for tri in triangles_elem.findall(f"{{{NS_CORE}}}triangle"):
            v1 = int(tri.get("v1"))
            v2 = int(tri.get("v2"))
            v3 = int(tri.get("v3"))
            faces.append([v1, v2, v3])

            # Resolve per-triangle color
            pid = tri.get("pid", obj_pid)
            p1 = tri.get("p1")
            p2 = tri.get("p2")
            p3 = tri.get("p3")

            # Determine the color index — use p1 if available, else object default
            if p1 is not None:
                color_index = int(p1)
            elif obj_pindex is not None:
                color_index = int(obj_pindex)
            else:
                color_index = None

            color = default_color
            if pid is not None and color_index is not None:
                # Check basematerials first
                if pid in basematerials:
                    mat_list = basematerials[pid]
                    if 0 <= color_index < len(mat_list):
                        color = mat_list[color_index]
                # Then check colorgroups
                elif pid in colorgroups:
                    cg_list = colorgroups[pid]
                    if 0 <= color_index < len(cg_list):
                        color = cg_list[color_index]

            face_colors.append(color)

    faces = np.array(faces, dtype=np.int64)
    face_colors = np.array(face_colors, dtype=np.uint8)

    return {
        "id": obj_id,
        "name": obj_name,
        "vertices": vertices,
        "faces": faces,
        "face_colors": face_colors,
        "pid": obj_pid,
        "pindex": obj_pindex,
    }


def parse_components(obj_elem):
    """Parse <components> within an object → list of {objectid, transform}."""
    components = []
    comps_elem = obj_elem.find(f"{{{NS_CORE}}}components")
    if comps_elem is None:
        return components

    for comp in comps_elem.findall(f"{{{NS_CORE}}}component"):
        comp_data = {"objectid": comp.get("objectid")}
        transform_str = comp.get("transform")
        if transform_str:
            t = [float(x) for x in transform_str.split()]
            # 3MF transform is column-major 3x4 → build 4x4 matrix
            mat = np.eye(4)
            mat[0, 0], mat[1, 0], mat[2, 0] = t[0], t[1], t[2]
            mat[0, 1], mat[1, 1], mat[2, 1] = t[3], t[4], t[5]
            mat[0, 2], mat[1, 2], mat[2, 2] = t[6], t[7], t[8]
            mat[0, 3], mat[1, 3], mat[2, 3] = t[9], t[10], t[11]
            comp_data["transform"] = mat
        else:
            comp_data["transform"] = np.eye(4)
        components.append(comp_data)

    return components


def resolve_object(obj_id, objects_data, basematerials, colorgroups, transform=None):
    """
    Recursively resolve an object — if it has components, resolve each,
    applying transforms. Returns list of trimesh.Trimesh objects.
    """
    if transform is None:
        transform = np.eye(4)

    obj_elem = objects_data.get(obj_id)
    if obj_elem is None:
        return []

    meshes = []

    # Check if this object has a direct mesh
    mesh_data = parse_object(obj_elem, basematerials, colorgroups)
    if mesh_data is not None and len(mesh_data["faces"]) > 0:
        mesh = trimesh.Trimesh(
            vertices=mesh_data["vertices"],
            faces=mesh_data["faces"],
            face_colors=mesh_data["face_colors"],
            process=False,
        )
        mesh.apply_transform(transform)
        mesh.metadata["name"] = mesh_data["name"]
        meshes.append(mesh)

    # Check if this object has components (assembly)
    components = parse_components(obj_elem)
    for comp in components:
        child_id = comp["objectid"]
        child_transform = transform @ comp["transform"]
        child_meshes = resolve_object(
            child_id, objects_data, basematerials, colorgroups, child_transform
        )
        meshes.extend(child_meshes)

    return meshes


def convert_3mf_to_glb(input_path, output_path=None):
    """
    Main conversion function.
    Opens a 3MF file, extracts mesh + color data, and exports as GLB.
    """
    if output_path is None:
        base = os.path.splitext(input_path)[0]
        output_path = base + ".glb"

    print(f"Opening: {input_path}")

    # --- Open and extract 3MF ---
    with zipfile.ZipFile(input_path, "r") as zf:
        model_file = find_model_xml(zf)
        if model_file is None:
            print("ERROR: No 3D model XML found in 3MF archive.")
            print(f"  Archive contents: {zf.namelist()}")
            return False

        print(f"  Found model: {model_file}")
        xml_data = zf.read(model_file)

    # --- Parse XML ---
    root = ET.fromstring(xml_data)

    # Handle namespace — the root element might use the core namespace
    resources_elem = root.find(f"{{{NS_CORE}}}resources")
    build_elem = root.find(f"{{{NS_CORE}}}build")

    if resources_elem is None:
        print("ERROR: No <resources> element found in model XML.")
        return False

    if build_elem is None:
        print("ERROR: No <build> element found in model XML.")
        return False

    # --- Parse materials and color groups ---
    basematerials = parse_basematerials(resources_elem)
    colorgroups = parse_colorgroups(resources_elem)

    print(f"  Found {len(basematerials)} basematerial group(s)")
    print(f"  Found {len(colorgroups)} color group(s)")

    # Print color details
    for bm_id, colors in basematerials.items():
        print(f"    basematerials[{bm_id}]: {len(colors)} color(s)")
    for cg_id, colors in colorgroups.items():
        print(f"    colorgroup[{cg_id}]: {len(colors)} color(s)")

    # --- Index all objects by ID ---
    objects_data = {}
    for obj_elem in resources_elem.findall(f"{{{NS_CORE}}}object"):
        obj_id = obj_elem.get("id")
        objects_data[obj_id] = obj_elem

    print(f"  Found {len(objects_data)} object(s)")

    # --- Resolve build items ---
    all_meshes = []
    build_items = build_elem.findall(f"{{{NS_CORE}}}item")

    for item in build_items:
        item_obj_id = item.get("objectid")
        transform_str = item.get("transform")

        item_transform = np.eye(4)
        if transform_str:
            t = [float(x) for x in transform_str.split()]
            item_transform[0, 0], item_transform[1, 0], item_transform[2, 0] = t[0], t[1], t[2]
            item_transform[0, 1], item_transform[1, 1], item_transform[2, 1] = t[3], t[4], t[5]
            item_transform[0, 2], item_transform[1, 2], item_transform[2, 2] = t[6], t[7], t[8]
            item_transform[0, 3], item_transform[1, 3], item_transform[2, 3] = t[9], t[10], t[11]

        meshes = resolve_object(
            item_obj_id, objects_data, basematerials, colorgroups, item_transform
        )
        all_meshes.extend(meshes)

    if not all_meshes:
        print("ERROR: No meshes found in 3MF file.")
        return False

    print(f"  Resolved {len(all_meshes)} mesh(es) total")

    # --- Prepare meshes for GLB export ---
    # Unmerge faces so each triangle has its own vertices → sharp per-face colors
    prepared_meshes = []
    total_faces = 0
    total_vertices = 0

    for mesh in all_meshes:
        total_faces += len(mesh.faces)

        # Store face colors before unmerging
        fc = mesh.visual.face_colors.copy()

        # Unmerge: duplicate shared vertices so each face has unique vertex set
        # This prevents color interpolation at shared edges
        new_vertices = mesh.vertices[mesh.faces.flatten()]
        new_faces = np.arange(len(new_vertices)).reshape(-1, 3)

        # Expand face colors → vertex colors (repeat each face color 3 times)
        vertex_colors = np.repeat(fc, 3, axis=0)

        new_mesh = trimesh.Trimesh(
            vertices=new_vertices,
            faces=new_faces,
            vertex_colors=vertex_colors,
            process=False,
        )

        if hasattr(mesh, "metadata") and "name" in mesh.metadata:
            new_mesh.metadata["name"] = mesh.metadata["name"]

        total_vertices += len(new_vertices)
        prepared_meshes.append(new_mesh)

    print(f"  Total triangles: {total_faces:,}")
    print(f"  Total vertices (after unmerge): {total_vertices:,}")

    # --- Build scene and export GLB ---
    if len(prepared_meshes) == 1:
        scene = trimesh.Scene(geometry={"model": prepared_meshes[0]})
    else:
        geometry = {}
        for i, mesh in enumerate(prepared_meshes):
            name = mesh.metadata.get("name", f"part_{i}") if hasattr(mesh, "metadata") else f"part_{i}"
            # Ensure unique names
            if name in geometry:
                name = f"{name}_{i}"
            geometry[name] = mesh
        scene = trimesh.Scene(geometry=geometry)

    # Export
    glb_data = scene.export(file_type="glb")

    with open(output_path, "wb") as f:
        f.write(glb_data)

    file_size = os.path.getsize(output_path)
    size_str = f"{file_size / 1024:.1f} KB" if file_size < 1024 * 1024 else f"{file_size / (1024*1024):.1f} MB"

    print(f"\nSaved: {output_path} ({size_str})")
    return True


def main():
    """Entry point — handles CLI args, drag-and-drop, and file picker."""
    if len(sys.argv) >= 2:
        # CLI or drag-and-drop mode
        input_path = sys.argv[1]
        output_path = sys.argv[2] if len(sys.argv) >= 3 else None
    else:
        # No arguments — open file picker
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            input_path = filedialog.askopenfilename(
                title="Select 3MF File",
                filetypes=[("3MF Files", "*.3mf"), ("All Files", "*.*")],
            )
            root.destroy()

            if not input_path:
                print("No file selected.")
                return
        except ImportError:
            print("Usage: converter.py <input.3mf> [output.glb]")
            print("  Or drag a .3mf file onto this executable.")
            return

        output_path = None

    # Validate input
    if not os.path.isfile(input_path):
        print(f"ERROR: File not found: {input_path}")
        input("Press Enter to exit...")
        return

    if not input_path.lower().endswith(".3mf"):
        print(f"WARNING: File does not have .3mf extension: {input_path}")

    # Convert
    print("=" * 50)
    print("  3MF to GLB Converter")
    print("=" * 50)

    success = convert_3mf_to_glb(input_path, output_path)

    if success:
        print("\nConversion complete!")
    else:
        print("\nConversion failed.")

    # Keep window open if running as exe (drag-and-drop)
    if getattr(sys, "frozen", False):
        input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()
