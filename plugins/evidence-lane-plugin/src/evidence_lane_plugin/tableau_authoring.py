"""Exact selected caption/calculation XML edits; unknown package members survive."""
import base64
import io
import zipfile
from pathlib import PurePosixPath

from .tableau_parsers import digest, fail, package_members, xml_items, xml_root


def edit_xml(arguments):
    from lxml import etree
    content = base64.b64decode(arguments['content_base64'], validate=True)
    if digest(content) != arguments['expected_sha256']:
        fail('EDIT_INPUT_CHANGED')
    filename = arguments['logical_name']
    extension = PurePosixPath(filename).suffix.lower()
    if extension not in {'.twb', '.tds', '.twbx', '.tdsx'}:
        fail('EDIT_FORMAT_UNSUPPORTED')
    packaged = extension in {'.twbx', '.tdsx'}
    members = package_members(content) if packaged else {PurePosixPath(filename).name: content}
    roots, locators, changed, selected = {}, {}, {}, set()
    for edit in arguments['replacements']:
        part = edit['part']
        if part not in members or PurePosixPath(part).suffix.lower() not in {'.twb', '.tds'}:
            fail('EDIT_PART_INVALID')
        identity = (part, edit['item_id'], edit['attribute'])
        if identity in selected:
            fail('EDIT_DUPLICATE_TARGET')
        selected.add(identity)
        candidates = {row['item_id']: row for row in xml_items(part, members[part])}
        item = candidates.get(edit['item_id'])
        if item is None or edit['attribute'] == 'formula' and item['kind'] != 'calculation':
            fail('EDIT_LOCATOR_INVALID')
        if part not in roots:
            roots[part] = xml_root(members[part])
            tree = roots[part].getroottree()
            # Match the parser's exact element paths instead of evaluating an
            # XPath with only root-level namespace bindings. Prefixes can be
            # declared or rebound on a descendant node.
            locators[part] = {tree.getpath(node): node for node in roots[part].iter()
                if isinstance(node.tag, str)}
        nodes = [locators[part][item['xml_path']]] if item['xml_path'] in locators[part] else []
        if len(nodes) != 1 or nodes[0].get(edit['attribute']) != edit['expected_value']:
            fail('EDIT_VALUE_CHANGED')
        nodes[0].set(edit['attribute'], edit['value'])
    for part, root in roots.items():
        changed[part] = etree.tostring(root.getroottree(), encoding='utf-8', xml_declaration=True)
    if packaged:
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(content)) as original, zipfile.ZipFile(output, 'w') as target:
            target.comment = original.comment
            for info in original.infolist():
                normalized = info.filename.replace('\\', '/').rstrip('/')
                target.writestr(info, changed.get(normalized, original.read(info)))
        result = output.getvalue()
        after = package_members(result)
        preserved = all(after[name] == raw for name, raw in members.items() if name not in changed)
        if not preserved:
            fail('EDIT_MEMBER_CHANGED')
    else:
        result, preserved = changed[PurePosixPath(filename).name], True
    return result, {'operation': 'xml_edit', 'edited_parts': sorted(changed),
        'untouched_members_byte_identical': preserved, 'xml_serialization_may_change': True,
        'tableau_semantic_validation': False, 'calculation_execution': False}
