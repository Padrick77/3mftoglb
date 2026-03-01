import zipfile
import xml.etree.ElementTree as ET

zf = zipfile.ZipFile(r'C:\websites\Crafts Unleashed\staging\Rosewing_Colors By Object.3mf')

with zf.open('3D/3dmodel.model') as f:
    for event, elem in ET.iterparse(f, events=('start',)):
        tag = elem.tag.split('}', 1)[1] if '}' in elem.tag else elem.tag
        if hasattr(elem, 'get'):
            if tag == 'object':
                print(f"Object id={elem.get('id')} pid={elem.get('pid')} pindex={elem.get('pindex')}")
            elif tag == 'triangle':
                pid = elem.get('pid')
                p1 = elem.get('p1')
                paint = elem.get('paint_color')
                if pid or p1 or paint:
                    print(f"Triangle color: pid={pid} p1={p1} paint_color={paint}")
                    break
        elem.clear()
