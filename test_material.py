import trimesh
import numpy as np

# Create a box
mesh = trimesh.creation.box()

# We want NO vertex colors, but a solid material color
# Let's try setting material directly
material = trimesh.visual.material.PBRMaterial(baseColorFactor=[255, 128, 255, 255])
# Create visuals with no vertex/face colors, just the material
mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh, material=material)

# Export to GLB
scene = trimesh.Scene(mesh)
scene.export('test_material.glb')

print("Exported test_material.glb. Let's check its JSON:")

import json
with open('test_material.glb', 'rb') as f:
    magic = f.read(4)
    version = int.from_bytes(f.read(4), 'little')
    length = int.from_bytes(f.read(4), 'little')
    json_chunk_len = int.from_bytes(f.read(4), 'little')
    json_chunk_type = f.read(4)
    if json_chunk_type == b'JSON':
        gltf_json = json.loads(f.read(json_chunk_len).decode('utf-8'))
        print('Materials:', json.dumps(gltf_json.get('materials'), indent=2))
        print('Meshes:', json.dumps(gltf_json.get('meshes', [])[:2], indent=2))
