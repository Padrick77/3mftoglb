import zipfile
import xml.etree.ElementTree as ET

zf = zipfile.ZipFile(r'C:\websites\Crafts Unleashed\staging\Rosewing_Colors By Object.3mf')

with zf.open('3D/3dmodel.model') as f:
    for event, elem in ET.iterparse(f, events=('start', 'end')):
        tag = elem.tag.split('}', 1)[1] if '}' in elem.tag else elem.tag
        if hasattr(elem, 'get'):
            if tag == 'object' and event == 'start':
                print(f"Object id='{elem.get('id')}'")
            elif tag == 'component' and event == 'start':
                print(f"  Component objectid='{elem.get('objectid')}'")
            elif tag == 'object' and event == 'end':
                print(f"End Object")
