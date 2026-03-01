"""Inspect an older 3MF file to understand its color storage."""
import zipfile
import sys
import re
import json

path = sys.argv[1] if len(sys.argv) > 1 else r"C:\websites\Crafts Unleashed\staging\Tiny_HeartDragon_MMU.3mf"

with zipfile.ZipFile(path, "r") as zf:
    print("=== Files in archive ===")
    for name in zf.namelist():
        info = zf.getinfo(name)
        print(f"  {name}  ({info.file_size:,} bytes)")
    
    # Check for project settings
    for name in zf.namelist():
        if 'project_settings' in name.lower() or 'model_settings' in name.lower():
            data = zf.read(name).decode('utf-8')
            if 'filament_colour' in data.lower() or 'color' in data.lower():
                if name.endswith('.config') or name.endswith('.json'):
                    try:
                        settings = json.loads(data)
                        print(f"\n=== {name} - filament colors ===")
                        print(f"  filament_colour: {settings.get('filament_colour')}")
                    except:
                        # Not JSON, check for color-related lines
                        print(f"\n=== {name} (color lines) ===")
                        for line in data.split('\n'):
                            if 'color' in line.lower() or 'filament' in line.lower():
                                print(f"  {line.strip()[:200]}")
    
    # Check the model XML
    for name in zf.namelist():
        if name.lower().endswith('.model'):
            data = zf.read(name).decode('utf-8')
            print(f"\n=== {name} (first 3000 chars) ===")
            print(data[:3000])
            
            # Count key elements
            print(f"\nElement counts:")
            print(f"  basematerials: {len(re.findall(r'<basematerials', data[:500000]))}")
            print(f"  colorgroup: {len(re.findall(r'<colorgroup', data[:500000]))}")
            print(f"  paint_color: {len(re.findall(r'paint_color', data[:500000]))}")
            print(f"  mmu_segmentation: {len(re.findall(r'mmu_segmentation', data[:500000]))}")
            print(f"  pid= on triangles: {len(re.findall(r'<triangle[^>]*pid=', data[:500000]))}")
            
            # Find sample triangles
            triangles = re.findall(r'<triangle [^>]*>', data[:200000])
            if triangles:
                # Show first few with attributes
                print(f"\nFirst 10 triangles:")
                for t in triangles[:10]:
                    print(f"  {t}")
                # Show ones with non-standard attributes
                special = [t for t in triangles if 'paint' in t or 'pid' in t or 'p1' in t]
                if special:
                    print(f"\nTriangles with color attrs ({len(special)} in sample):")
                    for t in special[:10]:
                        print(f"  {t}")
