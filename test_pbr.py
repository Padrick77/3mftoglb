import trimesh
import json
import numpy as np

mesh = trimesh.creation.box()

# Create material color
mat = trimesh.visual.material.PBRMaterial(baseColorFactor=[255, 128, 64, 255])
# TextureVisuals supports material
vis = trimesh.visual.TextureVisuals(material=mat)
mesh.visual = vis

scene = trimesh.Scene(mesh)
scene.export('test_mat.glb')

with open('test_mat.glb', 'rb') as f:
    f.read(12) # magic, version, len
    json_chunk_len = int.from_bytes(f.read(4), 'little')
    f.read(4) # type
    gltf_json = json.loads(f.read(json_chunk_len).decode('utf-8'))
    
    print('Materials:', json.dumps(gltf_json.get('materials'), indent=2))
    print('Primitive attributes:', gltf_json['meshes'][0]['primitives'][0]['attributes'])
