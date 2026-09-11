"""Editable OpenXML generation and exact package-preserving paragraph edits."""
from __future__ import annotations

import base64
import copy
import io
import re
import xml.etree.ElementTree as ET
import zipfile

from .document_parsers import REL, R, digest, package_members, xml_root
from .presentation_parsers import A, P, ordered_slides, paragraph_text

CT = '{http://schemas.openxmlformats.org/package/2006/content-types}'


def element(parent, name, attributes=None, text=None):
    node = ET.SubElement(parent, name, attributes or {})
    node.text = text
    return node


def xml_bytes(root):
    raw = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    if root.tag in {CT + 'Types', REL + 'Relationships'}:
        # Some office importers require the conventional default namespace for
        # OPC metadata even though a prefixed equivalent is namespace-correct.
        # Bind it per document; do not mutate ElementTree's global namespace map.
        from lxml import etree as xml
        parsed = xml.fromstring(raw, xml.XMLParser(resolve_entities=False, load_dtd=False, no_network=True))
        rebound = xml.Element(parsed.tag, dict(parsed.attrib), nsmap={None: parsed.tag[1:].split('}', 1)[0]})
        rebound.extend(parsed)
        raw = xml.tostring(rebound, encoding='utf-8', xml_declaration=True)
    xml_root(raw)
    return raw


def relations(rows):
    root = ET.Element(REL + 'Relationships')
    for index, (kind, target) in enumerate(rows, 1):
        element(root, REL + 'Relationship', {'Id': 'rId' + str(index), 'Type': R[1:-1] + '/' + kind, 'Target': target})
    return xml_bytes(root)


def shape_tree(root):
    common = element(root, P + 'cSld')
    tree = element(common, P + 'spTree')
    nonvisual = element(tree, P + 'nvGrpSpPr')
    element(nonvisual, P + 'cNvPr', {'id': '1', 'name': ''})
    element(nonvisual, P + 'cNvGrpSpPr')
    element(nonvisual, P + 'nvPr')
    group = element(tree, P + 'grpSpPr')
    transform = element(group, A + 'xfrm')
    for name, values in [('off', {'x': '0', 'y': '0'}), ('ext', {'cx': '0', 'cy': '0'}),
                         ('chOff', {'x': '0', 'y': '0'}), ('chExt', {'cx': '0', 'cy': '0'})]:
        element(transform, A + name, values)
    return common, tree


def nonvisual(shape, kind, identity, name, description=''):
    mapping = {'sp': ('nvSpPr', 'cNvSpPr'), 'pic': ('nvPicPr', 'cNvPicPr'),
               'graphicFrame': ('nvGraphicFramePr', 'cNvGraphicFramePr')}
    container, child = mapping[kind]
    props = element(shape, P + container)
    element(props, P + 'cNvPr', {'id': str(identity), 'name': name, 'descr': description})
    element(props, P + child)
    element(props, P + 'nvPr')


def transform(parent, obj, *, namespace=A):
    node = element(parent, namespace + 'xfrm')
    element(node, A + 'off', {key: str(round(obj[key] * 914400)) for key in ('x', 'y')})
    element(node, A + 'ext', {'cx': str(round(obj['width'] * 914400)), 'cy': str(round(obj['height'] * 914400))})


def paragraph(parent, text, style):
    node = element(parent, A + 'p')
    for index, line in enumerate(text.replace('\r\n', '\n').replace('\r', '\n').split('\n')):
        if index:
            element(node, A + 'br')
        run = element(node, A + 'r')
        props = element(run, A + 'rPr', {'lang': 'en-US', 'sz': str(style['font_points'] * 100), 'b': str(int(style['bold']))})
        element(element(props, A + 'solidFill'), A + 'srgbClr', {'val': style['color']})
        element(props, A + 'latin', {'typeface': 'Arial'})
        element(run, A + 't', text=line)
    element(node, A + 'endParaRPr', {'lang': 'en-US', 'sz': str(style['font_points'] * 100)})


def text_body(parent, text, style, *, namespace=P):
    body = element(parent, namespace + 'txBody')
    element(body, A + 'bodyPr', {'wrap': 'square', 'anchor': 't'})
    element(body, A + 'lstStyle')
    for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        paragraph(body, line, style)


def theme():
    root = ET.Element(A + 'theme', {'name': 'Evidence Lane'})
    elements = element(root, A + 'themeElements')
    colors = element(elements, A + 'clrScheme', {'name': 'Evidence Lane'})
    for name, value in [('dk1', '172333'), ('lt1', 'FFFFFF'), ('dk2', '314154'), ('lt2', 'F1F4F7'),
        ('accent1', '2663A3'), ('accent2', '238476'), ('accent3', 'B56B26'), ('accent4', '7662A3'),
        ('accent5', '3B8197'), ('accent6', 'A85062'), ('hlink', '2663A3'), ('folHlink', '7662A3')]:
        element(element(colors, A + name), A + 'srgbClr', {'val': value})
    fonts = element(elements, A + 'fontScheme', {'name': 'Arial'})
    for name in ('majorFont', 'minorFont'):
        font = element(fonts, A + name)
        element(font, A + 'latin', {'typeface': 'Arial'})
        element(font, A + 'ea', {'typeface': ''})
        element(font, A + 'cs', {'typeface': ''})
    styles = element(elements, A + 'fmtScheme', {'name': 'Evidence Lane'})
    fills = element(styles, A + 'fillStyleLst')
    for _ in range(3):
        element(element(fills, A + 'solidFill'), A + 'schemeClr', {'val': 'phClr'})
    lines = element(styles, A + 'lnStyleLst')
    for width in (9525, 25400, 38100):
        line = element(lines, A + 'ln', {'w': str(width), 'cap': 'flat', 'cmpd': 'sng', 'algn': 'ctr'})
        element(element(line, A + 'solidFill'), A + 'schemeClr', {'val': 'phClr'})
        element(line, A + 'prstDash', {'val': 'solid'})
    effects = element(styles, A + 'effectStyleLst')
    for _ in range(3):
        element(element(effects, A + 'effectStyle'), A + 'effectLst')
    backgrounds = element(styles, A + 'bgFillStyleLst')
    for _ in range(3):
        element(element(backgrounds, A + 'solidFill'), A + 'schemeClr', {'val': 'phClr'})
    return root


def add_object(tree, obj, identity, slide_rels, members, slide_index):
    kind = obj['kind']
    if kind == 'image':
        from PIL import Image
        raw = base64.b64decode(obj['image_base64'], validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            if (image.format.lower() != obj['image_format'] or image.width * image.height > 20_000_000
                    or getattr(image, 'n_frames', 1) != 1):
                raise ValueError('PRESENTATION_IMAGE_FORMAT_OR_PIXEL_BUDGET')
            image.verify()
        name = f'image-{slide_index}-{identity}.' + obj['image_format']
        members['ppt/media/' + name] = raw
        slide_rels.append(('image', '../media/' + name))
        shape = element(tree, P + 'pic')
        nonvisual(shape, 'pic', identity, obj['name'], obj['description'])
        fill = element(shape, P + 'blipFill')
        element(fill, A + 'blip', {R + 'embed': 'rId' + str(len(slide_rels))})
        element(element(fill, A + 'stretch'), A + 'fillRect')
        props = element(shape, P + 'spPr')
        transform(props, obj)
        element(element(props, A + 'prstGeom', {'prst': 'rect'}), A + 'avLst')
    elif kind == 'table':
        shape = element(tree, P + 'graphicFrame')
        nonvisual(shape, 'graphicFrame', identity, obj['name'])
        transform(shape, obj, namespace=P)
        data = element(element(shape, A + 'graphic'), A + 'graphicData', {'uri': 'http://schemas.openxmlformats.org/drawingml/2006/table'})
        table = element(data, A + 'tbl')
        element(table, A + 'tblPr', {'firstRow': '1', 'bandRow': '1'})
        grid = element(table, A + 'tblGrid')
        rows = obj['rows']
        for _ in rows[0]:
            element(grid, A + 'gridCol', {'w': str(round(obj['width'] * 914400 / len(rows[0])))})
        for row in rows:
            table_row = element(table, A + 'tr', {'h': str(round(obj['height'] * 914400 / len(rows)))})
            for value in row:
                cell = element(table_row, A + 'tc')
                text_body(cell, value, obj, namespace=A)
                props = element(cell, A + 'tcPr')
                for side in ('L', 'R', 'T', 'B'):
                    line = element(props, A + 'ln' + side, {'w': '12700'})
                    element(element(line, A + 'solidFill'), A + 'srgbClr', {'val': 'BBC4CE'})
    else:
        shape = element(tree, P + 'sp')
        nonvisual(shape, 'sp', identity, obj['name'])
        props = element(shape, P + 'spPr')
        transform(props, obj)
        element(element(props, A + 'prstGeom', {'prst': 'ellipse' if kind == 'ellipse' else 'rect'}), A + 'avLst')
        if obj['fill']:
            element(element(props, A + 'solidFill'), A + 'srgbClr', {'val': obj['fill']})
        else:
            element(props, A + 'noFill')
        element(element(props, A + 'ln'), A + 'noFill')
        text_body(shape, obj['text'], obj)


def generate_package(arguments):
    from .presentation_contracts import PresentationGenerate
    arguments = PresentationGenerate.model_validate(arguments).model_dump(mode='json')
    members, overrides = {}, []
    def part(name, root, content_type):
        members[name] = xml_bytes(root)
        overrides.append((name, content_type))
    content_prefix = 'application/vnd.openxmlformats-officedocument.presentationml.'
    presentation = ET.Element(P + 'presentation')
    master_ids = element(presentation, P + 'sldMasterIdLst')
    element(master_ids, P + 'sldMasterId', {'id': '2147483648', R + 'id': 'rId1'})
    slide_ids = element(presentation, P + 'sldIdLst')
    presentation_rels = [('slideMaster', 'slideMasters/slideMaster1.xml')]
    for index, slide in enumerate(arguments['slides'], 1):
        presentation_rels.append(('slide', f'slides/slide{index}.xml'))
        element(slide_ids, P + 'sldId', {'id': str(255 + index), R + 'id': 'rId' + str(len(presentation_rels))})
        root = ET.Element(P + 'sld')
        common, tree = shape_tree(root)
        common.set('name', slide['name'])
        background = ET.Element(P + 'bg')
        element(element(element(background, P + 'bgPr'), A + 'solidFill'), A + 'srgbClr', {'val': slide['background']})
        common.insert(0, background)
        slide_rels = [('slideLayout', '../slideLayouts/slideLayout1.xml')]
        for identity, obj in enumerate(slide['objects'], 2):
            add_object(tree, obj, identity, slide_rels, members, index)
        element(element(root, P + 'clrMapOvr'), A + 'masterClrMapping')
        if slide['notes']:
            notes = ET.Element(P + 'notes')
            _, notes_tree = shape_tree(notes)
            obj = {'kind': 'text', 'name': 'Speaker notes', 'x': 1, 'y': 1, 'width': 5, 'height': 7,
                'text': slide['notes'], 'font_points': 12, 'bold': False, 'color': '172333', 'fill': None}
            add_object(notes_tree, obj, 2, [], members, index)
            element(notes_tree[-1].find(P + 'nvSpPr/' + P + 'nvPr'), P + 'ph', {'type': 'body', 'idx': '1'})
            element(element(notes, P + 'clrMapOvr'), A + 'masterClrMapping')
            part(f'ppt/notesSlides/notesSlide{index}.xml', notes, content_prefix + 'notesSlide+xml')
            members[f'ppt/notesSlides/_rels/notesSlide{index}.xml.rels'] = relations([
                ('slide', f'../slides/slide{index}.xml'), ('notesMaster', '../notesMasters/notesMaster1.xml')])
            slide_rels.append(('notesSlide', f'../notesSlides/notesSlide{index}.xml'))
        part(f'ppt/slides/slide{index}.xml', root, content_prefix + 'slide+xml')
        members[f'ppt/slides/_rels/slide{index}.xml.rels'] = relations(slide_rels)
    presentation_rels.append(('presProps', 'presProps.xml'))
    if any(slide['notes'] for slide in arguments['slides']):
        presentation_rels.append(('notesMaster', 'notesMasters/notesMaster1.xml'))
        notes_ids = ET.Element(P + 'notesMasterIdLst')
        element(notes_ids, P + 'notesMasterId', {R + 'id': 'rId' + str(len(presentation_rels))})
        presentation.insert(1, notes_ids)
        notes_master = ET.Element(P + 'notesMaster')
        shape_tree(notes_master)
        element(notes_master, P + 'clrMap', {name: value for name, value in [
            ('bg1', 'lt1'), ('tx1', 'dk1'), ('bg2', 'lt2'), ('tx2', 'dk2'),
            *[(f'accent{i}', f'accent{i}') for i in range(1, 7)], ('hlink', 'hlink'), ('folHlink', 'folHlink')]})
        part('ppt/notesMasters/notesMaster1.xml', notes_master, content_prefix + 'notesMaster+xml')
        members['ppt/notesMasters/_rels/notesMaster1.xml.rels'] = relations([('theme', '../theme/theme1.xml')])
    element(presentation, P + 'sldSz', {'cx': str(round(arguments['width_inches'] * 914400)),
        'cy': str(round(arguments['height_inches'] * 914400))})
    element(presentation, P + 'notesSz', {'cx': '6858000', 'cy': '9144000'})
    part('ppt/presentation.xml', presentation, content_prefix + 'presentation.main+xml')
    part('ppt/presProps.xml', ET.Element(P + 'presentationPr'), content_prefix + 'presProps+xml')
    part('ppt/theme/theme1.xml', theme(), 'application/vnd.openxmlformats-officedocument.theme+xml')
    members['ppt/_rels/presentation.xml.rels'] = relations(presentation_rels)
    master = ET.Element(P + 'sldMaster')
    shape_tree(master)
    element(master, P + 'clrMap', {name: value for name, value in [
        ('bg1', 'lt1'), ('tx1', 'dk1'), ('bg2', 'lt2'), ('tx2', 'dk2'),
        *[(f'accent{i}', f'accent{i}') for i in range(1, 7)], ('hlink', 'hlink'), ('folHlink', 'folHlink')]})
    element(element(master, P + 'sldLayoutIdLst'), P + 'sldLayoutId', {'id': '2147483649', R + 'id': 'rId1'})
    part('ppt/slideMasters/slideMaster1.xml', master, content_prefix + 'slideMaster+xml')
    members['ppt/slideMasters/_rels/slideMaster1.xml.rels'] = relations([
        ('slideLayout', '../slideLayouts/slideLayout1.xml'), ('theme', '../theme/theme1.xml')])
    layout = ET.Element(P + 'sldLayout', {'type': 'blank', 'preserve': '1'})
    shape_tree(layout)
    element(element(layout, P + 'clrMapOvr'), A + 'masterClrMapping')
    part('ppt/slideLayouts/slideLayout1.xml', layout, content_prefix + 'slideLayout+xml')
    members['ppt/slideLayouts/_rels/slideLayout1.xml.rels'] = relations([('slideMaster', '../slideMasters/slideMaster1.xml')])
    core = ET.Element('{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}coreProperties')
    element(core, '{http://purl.org/dc/elements/1.1/}title', text=arguments['title'])
    part('docProps/core.xml', core, 'application/vnd.openxmlformats-package.core-properties+xml')
    package_rels = xml_root(relations([('officeDocument', 'ppt/presentation.xml')]))
    element(package_rels, REL + 'Relationship', {'Id': 'rId2',
        'Type': 'http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties',
        'Target': 'docProps/core.xml'})
    members['_rels/.rels'] = xml_bytes(package_rels)
    types = ET.Element(CT + 'Types')
    for extension, content_type in [('rels', 'application/vnd.openxmlformats-package.relationships+xml'),
                                  ('xml', 'application/xml'), ('png', 'image/png'), ('jpeg', 'image/jpeg')]:
        element(types, CT + 'Default', {'Extension': extension, 'ContentType': content_type})
    for name, content_type in overrides:
        element(types, CT + 'Override', {'PartName': '/' + name, 'ContentType': content_type})
    members['[Content_Types].xml'] = xml_bytes(types)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(members.items()):
            archive.writestr(name, raw)
    return output.getvalue()


def edit_package(arguments):
    from lxml import etree as xml
    content = base64.b64decode(arguments['content_base64'], validate=True)
    if digest(content) != arguments['expected_sha256']:
        raise ValueError('PRESENTATION_EDIT_INPUT_CHANGED')
    members = package_members(content)
    _, slides = ordered_slides(members)
    roots, selected = {}, set()
    for replacement in arguments['replacements']:
        part, ordinal = replacement['part'], replacement['paragraph_index']
        if not re.fullmatch(r'ppt/(slides/slide|notesSlides/notesSlide)\d+\.xml', part) or part not in members:
            raise ValueError('PRESENTATION_EDIT_PART_INVALID')
        if (part, ordinal) in selected:
            raise ValueError('PRESENTATION_EDIT_DUPLICATE_TARGET')
        selected.add((part, ordinal))
        if part not in roots:
            xml_root(members[part])
            roots[part] = xml.fromstring(members[part], xml.XMLParser(resolve_entities=False, load_dtd=False, no_network=True))
        paragraphs = list(roots[part].iter(A + 'p'))
        if ordinal >= len(paragraphs) or paragraph_text(paragraphs[ordinal]) != replacement['expected_text']:
            raise ValueError('PRESENTATION_EDIT_TEXT_CHANGED')
        paragraph_node = paragraphs[ordinal]
        # Refuse to flatten fields, hyperlinks, rich run mixes or unknown children.
        runs = list(paragraph_node.findall(A + 'r'))
        properties = [xml.tostring(run.find(A + 'rPr')) if run.find(A + 'rPr') is not None else b'' for run in runs]
        if (len(set(properties)) > 1 or any(child.tag not in {A + 'pPr', A + 'r', A + 'br', A + 'endParaRPr'} for child in paragraph_node)
                or any(node.tag in {A + 'hlinkClick', A + 'hlinkMouseOver'} for node in paragraph_node.iter())
                or any(child.tag not in {A + 'rPr', A + 't'} for run in runs for child in run)):
            raise ValueError('PRESENTATION_EDIT_COMPLEX_PARAGRAPH')
        run_properties = copy.deepcopy(runs[0].find(A + 'rPr')) if runs else None
        for child in list(paragraph_node):
            if child.tag in {A + 'r', A + 'br'}:
                paragraph_node.remove(child)
        insert_at = len(paragraph_node) - int(paragraph_node.find(A + 'endParaRPr') is not None)
        for index, line in enumerate(replacement['replacement_text'].replace('\r\n', '\n').replace('\r', '\n').split('\n')):
            if index:
                paragraph_node.insert(insert_at, xml.Element(A + 'br'))
                insert_at += 1
            run = xml.Element(A + 'r')
            if run_properties is not None:
                run.append(copy.deepcopy(run_properties))
            xml.SubElement(run, A + 't').text = line
            paragraph_node.insert(insert_at, run)
            insert_at += 1
    if arguments.get('slide_order') is not None:
        order = arguments['slide_order']
        if len(order) != len(slides) or set(order) != {slide['part'] for slide in slides}:
            raise ValueError('PRESENTATION_EDIT_SLIDE_ORDER_INVALID')
        root = xml.fromstring(members['ppt/presentation.xml'], xml.XMLParser(resolve_entities=False, load_dtd=False, no_network=True))
        container = root.find(P + 'sldIdLst')
        nodes = {slide['part']: node for slide, node in zip(slides, list(container), strict=True)}
        for child in list(container):
            container.remove(child)
        for name in order:
            container.append(nodes[name])
        roots['ppt/presentation.xml'] = root
    for name, root in roots.items():
        members[name] = xml.tostring(root, encoding='utf-8', xml_declaration=True)
        xml_root(members[name])
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as original, zipfile.ZipFile(output, 'w') as edited:
        for info in original.infolist():
            edited.writestr(info, members.get(info.filename, b''))
    after = package_members(output.getvalue())
    if any(after[name] != raw for name, raw in package_members(content).items() if name not in roots):
        raise ValueError('PRESENTATION_EDIT_UNTOUCHED_MEMBER_CHANGED')
    return output.getvalue(), {'changed_parts': sorted(roots), 'untouched_members_byte_identical': True,
        'text_fidelity': 'uniform_run_properties_retained_complex_paragraphs_refused', 'layout_verified': False}
