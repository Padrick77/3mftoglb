import zipfile
import xml.etree.ElementTree as ET

zf = zipfile.ZipFile(r'C:\websites\Crafts Unleashed\staging\Rosewing_Colors By Object.3mf')

print('=== Metadata/model_settings.config ===')
try:
    print(zf.read('Metadata/model_settings.config').decode('utf-8')[:3000])
except Exception as e:
    print('Not found')

with zf.open('3D/3dmodel.model') as f:
    for event, elem in ET.iterparse(f, events=('start',)):
        tag = elem.tag.split('}', 1)[1] if '}' in elem.tag else elem.tag
        if hasattr(elem, 'get'):
            if tag == 'object':
                print(f"Object: id={elem.get('id')} name={elem.get('name')} pid={elem.get('pid')} pindex={elem.get('pindex')}")
            elif tag == 'component':
                print(f"Component: objectid={elem.get('objectid')}")
            elif tag == 'item':
                print(f"Item: objectid={elem.get('objectid')} p1={elem.get('p1')} pid={elem.get('pid')}")
        elem.clear()
