import zipfile
import xml.etree.ElementTree as ET

zf = zipfile.ZipFile(r'C:\websites\Crafts Unleashed\staging\Rosewing_Colors By Object.3mf')

print("--- 3D/3dmodel.model ---")
with zf.open('3D/3dmodel.model') as f:
    for event, elem in ET.iterparse(f, events=('start',)):
        tag = elem.tag.split('}', 1)[1] if '}' in elem.tag else elem.tag
        if hasattr(elem, 'get') and tag == 'object':
            print(f"Object: id={elem.get('id')} name={elem.get('name')}")
        elem.clear()

try:
    print("\n--- 3D/Objects/object_4.model ---")
    with zf.open('3D/Objects/object_4.model') as f:
        for event, elem in ET.iterparse(f, events=('start',)):
            tag = elem.tag.split('}', 1)[1] if '}' in elem.tag else elem.tag
            if hasattr(elem, 'get') and tag == 'object':
                print(f"Object: id={elem.get('id')} name={elem.get('name')}")
            elem.clear()
except Exception as e:
    print(f"Could not open object_4.model: {e}")
