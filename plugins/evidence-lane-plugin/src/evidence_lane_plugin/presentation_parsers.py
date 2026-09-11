"""Finite native presentation facts, adapted from the admitted PPTX extractor.

Slide order comes from presentation.xml. XML and relationships are read as data;
package bytes remain authoritative for properties outside this bounded projection.
"""
from __future__ import annotations

import posixpath
from pathlib import PurePosixPath
from urllib.parse import unquote, urldefrag

from .document_parsers import REL, Facts, R, digest, package_members, xml_root
from .hashing import canonical_json_bytes

P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
C = '{http://schemas.openxmlformats.org/drawingml/2006/chart}'
EXTENSIONS = {'.pptx', '.pptm', '.potx', '.ppsx'}


def paragraph_text(node):
    return ''.join(child.text or '' if child.tag == A + 't' else '\n' if child.tag == A + 'br' else ''
                   for child in node.iter() if child.tag in {A + 't', A + 'br'})


def text_of(node):
    return '\n'.join(paragraph_text(paragraph) for paragraph in node.iter(A + 'p'))


def relationships(members, part):
    name = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
    result = {}
    if name not in members:
        return result
    for row in xml_root(members[name]).findall(REL + 'Relationship'):
        identity, target = row.get('Id', ''), row.get('Target', '')
        if not identity or identity in result:
            raise ValueError('PRESENTATION_RELATIONSHIP_ID_INVALID')
        external = row.get('TargetMode') == 'External'
        resolved = None if external else posixpath.normpath(posixpath.join(
            posixpath.dirname(part), unquote(urldefrag(target)[0])))
        result[identity] = {'relationship_id': identity, 'target': target,
            'relationship_type': row.get('Type', ''), 'external': external,
            'member': resolved, 'member_present': resolved in members if resolved else None}
    return result


def ordered_slides(members):
    if 'ppt/presentation.xml' not in members or '[Content_Types].xml' not in members:
        raise ValueError('PRESENTATION_PACKAGE_INVALID')
    xml_root(members['[Content_Types].xml'])
    root = xml_root(members['ppt/presentation.xml'])
    if root.tag != P + 'presentation':
        raise ValueError('PRESENTATION_PACKAGE_INVALID')
    rels, selected, ids, result = relationships(members, 'ppt/presentation.xml'), set(), set(), []
    for node in root.findall(P + 'sldIdLst/' + P + 'sldId'):
        identity = node.get('id')
        rel = rels.get(node.get(R + 'id'))
        if (not rel or rel['external'] or not rel['relationship_type'].endswith('/slide')
                or not rel['member_present'] or rel['member'] in selected or identity in ids):
            raise ValueError('PRESENTATION_SLIDE_ORDER_INVALID')
        selected.add(rel['member'])
        ids.add(identity)
        result.append({'part': rel['member'], 'slide_id': identity, 'relationship_id': rel['relationship_id']})
    if not 1 <= len(result) <= 100:
        raise ValueError('PRESENTATION_SLIDE_BUDGET')
    return root, result


def geometry(node):
    transform = node.find(P + 'spPr/' + A + 'xfrm')
    if transform is None:
        transform = node.find(P + 'xfrm')
    if transform is None:
        transform = node.find(P + 'grpSpPr/' + A + 'xfrm')
    if transform is None:
        return None
    return {'attributes': dict(transform.attrib), 'coordinates': {
        child.tag.rsplit('}', 1)[-1]: dict(child.attrib) for child in transform}}


def parse_presentation(filename, content):
    if PurePosixPath(filename).suffix.lower() not in EXTENSIONS:
        raise ValueError('PRESENTATION_FORMAT_UNSUPPORTED')
    members, facts = package_members(content), Facts()
    presentation, slides = ordered_slides(members)
    size = presentation.find(P + 'sldSz')
    features = {'macros': any(name.lower().endswith('vbaproject.bin') for name in members),
        'external_resources': False, 'embedded_objects': False, 'charts': False,
        'animations': False, 'transitions': False, 'groups': False}
    # Record every relationship once, including masters, layouts, notes and charts.
    relationship_parts = ['ppt/presentation.xml'] + sorted(name for name in members
        if name.endswith('.xml') and name != 'ppt/presentation.xml' and name.startswith('ppt/'))
    all_rels = {}
    for part in relationship_parts:
        rels = relationships(members, part)
        all_rels[part] = rels
        for ordinal, rel in enumerate(rels.values()):
            facts.add('relationship', part, ordinal, **rel)
            features['external_resources'] |= rel['external'] and not rel['relationship_type'].endswith('/hyperlink')
    seen_notes = set()
    for slide_number, slide in enumerate(slides, 1):
        part = slide['part']
        root = xml_root(members[part])
        if root.tag != P + 'sld':
            raise ValueError('PRESENTATION_SLIDE_PART_INVALID')
        show = root.get('show', 'true').strip()
        if show not in {'true', 'false', '1', '0'}:
            raise ValueError('PRESENTATION_SHOW_ATTRIBUTE_INVALID')
        rels = all_rels.get(part, {})
        notes = [rel['member'] for rel in rels.values() if rel['relationship_type'].endswith('/notesSlide')
                 and not rel['external'] and rel['member_present']]
        if len(notes) > 1 or seen_notes.intersection(notes):
            raise ValueError('PRESENTATION_NOTES_BINDING_INVALID')
        seen_notes.update(notes)
        facts.add('slide', part, slide_number - 1, text_of(root), slide_number=slide_number,
            slide_id=slide['slide_id'], relationship_id=slide['relationship_id'], notes_parts=notes,
            hidden=show in {'0', 'false'})
        features['animations'] |= root.find(P + 'timing') is not None
        features['transitions'] |= root.find(P + 'transition') is not None
        for selected_part in [part, *notes]:
            selected_root = root if selected_part == part else xml_root(members[selected_part])
            is_notes = selected_part != part
            selected_rels = all_rels.get(selected_part, {})
            parents = {child: parent for parent in selected_root.iter() for child in parent}
            if is_notes:
                facts.add('notes', selected_part, 0, text_of(selected_root), slide_number=slide_number, slide_part=part)
            shapes = [node for node in selected_root.iter() if node.tag in {
                P + 'sp', P + 'pic', P + 'graphicFrame', P + 'grpSp', P + 'cxnSp'}]
            shape_ids = set()
            for ordinal, shape in enumerate(shapes):
                properties = next(shape.iter(P + 'cNvPr'), None)
                shape_id = properties.get('id') if properties is not None else None
                if not shape_id or shape_id in shape_ids:
                    raise ValueError('PRESENTATION_SHAPE_ID_INVALID')
                shape_ids.add(shape_id)
                grouped = any(ancestor.tag == P + 'grpSp' for ancestor in _ancestors(shape, parents))
                features['groups'] |= grouped or shape.tag == P + 'grpSp'
                facts.add('shape', selected_part, ordinal, text_of(shape), shape_id=shape_id,
                    name=properties.get('name', ''), description=properties.get('descr', ''),
                    object_type=shape.tag.rsplit('}', 1)[-1], geometry=geometry(shape),
                    local_coordinates=True, inside_group=grouped, slide_number=slide_number, is_notes=is_notes)
            for ordinal, paragraph in enumerate(selected_root.iter(A + 'p')):
                shape = next((node for node in _ancestors(paragraph, parents) if node in shapes), None)
                properties = next(shape.iter(P + 'cNvPr'), None) if shape is not None else None
                facts.add('text_block', selected_part, ordinal, paragraph_text(paragraph),
                    slide_number=slide_number, is_notes=is_notes,
                    shape_id=properties.get('id') if properties is not None else None,
                    locator={'paragraph_index': ordinal},
                    runs=[{'text': child.text or '', 'run_properties': dict(parents[child].find(A + 'rPr').attrib)
                        if parents[child].find(A + 'rPr') is not None else {}} for child in paragraph.iter(A + 't')])
            for ordinal, table in enumerate(selected_root.iter(A + 'tbl')):
                rows = [[text_of(cell) for cell in row.findall(A + 'tc')] for row in table.findall(A + 'tr')]
                if sum(map(len, rows)) > 4096:
                    raise ValueError('PRESENTATION_TABLE_BUDGET')
                facts.add('table', selected_part, ordinal, '\n'.join('\t'.join(row) for row in rows),
                    rows=rows, slide_number=slide_number, merged_cells=any(any(key in cell.attrib
                        for key in ('gridSpan', 'rowSpan', 'hMerge', 'vMerge')) for cell in table.iter(A + 'tc')))
            for ordinal, picture in enumerate(selected_root.iter(P + 'pic')):
                properties, blip = next(picture.iter(P + 'cNvPr'), None), next(picture.iter(A + 'blip'), None)
                relationship_id = (blip.get(R + 'embed') or blip.get(R + 'link')) if blip is not None else None
                rel = selected_rels.get(relationship_id)
                media = members.get(rel['member']) if rel and not rel['external'] else None
                facts.add('image', selected_part, ordinal, description=properties.get('descr', '') if properties is not None else '',
                    relationship_id=relationship_id, relationship=rel, slide_number=slide_number,
                    media_sha256=digest(media) if media is not None else None, media_bytes=len(media) if media is not None else None)
            for ordinal, chart in enumerate(selected_root.iter(C + 'chart')):
                features['charts'] = True
                rel = selected_rels.get(chart.get(R + 'id'))
                chart_raw = members.get(rel['member']) if rel and not rel['external'] else None
                chart_root = xml_root(chart_raw) if chart_raw else None
                facts.add('chart', selected_part, ordinal, relationship=rel, slide_number=slide_number,
                    chart_sha256=digest(chart_raw) if chart_raw else None,
                    cached_values=[node.text or '' for node in chart_root.iter(C + 'v')] if chart_root is not None else [])
            features['embedded_objects'] |= any(node.tag in {P + 'oleObj', P + 'control'} for node in selected_root.iter())
    limitations = ['Native facts describe stored content; inherited layout, theme and group transforms are not flattened.',
        'Animation, transition, chart workbook, media playback and PowerPoint layout equivalence are not verified.',
        'Rendering and visual review require separate operations. External resources are never fetched during intake.']
    body = {'schema': 'evidence-lane.presentation-facts.v4', 'format': PurePosixPath(filename).suffix.lower(),
        'slide_size_emu': dict(size.attrib) if size is not None else None,
        'slide_order': [slide['part'] for slide in slides], 'features': features, 'items': facts.items,
        'package_members': [{'part': name, 'sha256': digest(raw), 'bytes': len(raw)} for name, raw in sorted(members.items())],
        'fidelity': {'original_bytes_retained': True, 'native_order': True, 'editable_objects_retained': True,
            'layout_verified': False, 'external_resources_executed': False}, 'limitations': limitations}
    if len(canonical_json_bytes(body)) > 8_388_608:
        raise ValueError('PRESENTATION_FACTS_BYTE_BUDGET')
    return body


def _ancestors(node, parents):
    while node in parents:
        node = parents[node]
        yield node
