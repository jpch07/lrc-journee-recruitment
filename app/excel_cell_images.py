from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET


CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
PACKAGE_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOCUMENT_RELS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _sheet_path(parts: dict[str, bytes], sheet_name: str) -> str:
    workbook = ET.fromstring(parts["xl/workbook.xml"])
    sheet = next(
        (item for item in workbook.findall(f"{{{SHEET_NS}}}sheets/{{{SHEET_NS}}}sheet") if item.get("name") == sheet_name),
        None,
    )
    if sheet is None:
        raise ValueError(f"Worksheet {sheet_name!r} was not found.")
    relation_id = sheet.get(f"{{{DOCUMENT_RELS_NS}}}id")
    relationships = ET.fromstring(parts["xl/_rels/workbook.xml.rels"])
    relation = next(
        (item for item in relationships.findall(f"{{{PACKAGE_RELS_NS}}}Relationship") if item.get("Id") == relation_id),
        None,
    )
    if relation is None:
        raise ValueError(f"Worksheet relationship for {sheet_name!r} was not found.")
    target = relation.get("Target", "").lstrip("/")
    return target if target.startswith("xl/") else f"xl/{target}"


def _next_relationship_ids(relationships: ET.Element, count: int) -> list[str]:
    used = {item.get("Id") for item in relationships.findall(f"{{{PACKAGE_RELS_NS}}}Relationship")}
    result: list[str] = []
    number = 1
    while len(result) < count:
        candidate = f"rId{number}"
        if candidate not in used:
            used.add(candidate)
            result.append(candidate)
        number += 1
    return result


def _mark_rich_value_cell(sheet_xml: str, cell_reference: str, metadata_index: int) -> str:
    pattern = re.compile(
        rf'(<c\b[^>]*\br="{re.escape(cell_reference)}"[^>]*>)(.*?)(</c>)',
        flags=re.DOTALL,
    )
    match = pattern.search(sheet_xml)
    if match is None:
        raise ValueError(f"Cell {cell_reference} was not written to the worksheet XML.")
    opening = re.sub(r'\s+(?:t|vm)="[^"]*"', "", match.group(1))
    opening = opening[:-1] + f' t="e" vm="{metadata_index}">'
    body = match.group(2)
    if re.search(r"<v(?:\s[^>]*)?\s*/>", body):
        body = re.sub(r"<v(?:\s[^>]*)?\s*/>", "<v>#VALUE!</v>", body, count=1)
    elif re.search(r"<v(?:\s[^>]*)?>.*?</v>", body, flags=re.DOTALL):
        body = re.sub(r"<v(?:\s[^>]*)?>.*?</v>", "<v>#VALUE!</v>", body, count=1, flags=re.DOTALL)
    else:
        body += "<v>#VALUE!</v>"
    return sheet_xml[:match.start()] + opening + body + match.group(3) + sheet_xml[match.end():]


def embed_lookup_images(
    workbook_bytes: bytes,
    *,
    sheet_name: str,
    image_cells: list[tuple[str, bytes]],
    formula_cell: str,
    initial_image_index: int,
) -> bytes:
    """Embed PNGs as Excel rich values so XLOOKUP can return a selected image."""
    if not image_cells:
        return workbook_bytes
    if initial_image_index < 0 or initial_image_index >= len(image_cells):
        raise ValueError("Initial image index is outside the embedded image list.")

    with zipfile.ZipFile(io.BytesIO(workbook_bytes), "r") as source:
        parts = {item.filename: source.read(item.filename) for item in source.infolist()}

    sheet_path = _sheet_path(parts, sheet_name)
    sheet_xml = parts[sheet_path].decode("utf-8")
    for index, (cell_reference, _) in enumerate(image_cells, 1):
        sheet_xml = _mark_rich_value_cell(sheet_xml, cell_reference, index)
    formula_metadata_index = len(image_cells) + 1
    sheet_xml = _mark_rich_value_cell(sheet_xml, formula_cell, formula_metadata_index)
    parts[sheet_path] = sheet_xml.encode("utf-8")

    content_types = ET.fromstring(parts["[Content_Types].xml"])
    if not any(item.get("Extension") == "png" for item in content_types.findall(f"{{{CONTENT_TYPES_NS}}}Default")):
        ET.SubElement(content_types, f"{{{CONTENT_TYPES_NS}}}Default", Extension="png", ContentType="image/png")
    overrides = {
        "/xl/metadata.xml": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheetMetadata+xml",
        "/xl/richData/richValueRel.xml": "application/vnd.ms-excel.richvaluerel+xml",
        "/xl/richData/rdrichvalue.xml": "application/vnd.ms-excel.rdrichvalue+xml",
        "/xl/richData/rdrichvaluestructure.xml": "application/vnd.ms-excel.rdrichvaluestructure+xml",
        "/xl/richData/rdRichValueTypes.xml": "application/vnd.ms-excel.rdrichvaluetypes+xml",
    }
    existing_overrides = {item.get("PartName") for item in content_types.findall(f"{{{CONTENT_TYPES_NS}}}Override")}
    for part_name, content_type in overrides.items():
        if part_name not in existing_overrides:
            ET.SubElement(content_types, f"{{{CONTENT_TYPES_NS}}}Override", PartName=part_name, ContentType=content_type)
    ET.register_namespace("", CONTENT_TYPES_NS)
    parts["[Content_Types].xml"] = ET.tostring(content_types, encoding="utf-8", xml_declaration=True)

    workbook_rels = ET.fromstring(parts["xl/_rels/workbook.xml.rels"])
    relationship_ids = _next_relationship_ids(workbook_rels, 5)
    workbook_relationships = [
        ("http://schemas.openxmlformats.org/officeDocument/2006/relationships/sheetMetadata", "metadata.xml"),
        ("http://schemas.microsoft.com/office/2022/10/relationships/richValueRel", "richData/richValueRel.xml"),
        ("http://schemas.microsoft.com/office/2017/06/relationships/rdRichValue", "richData/rdrichvalue.xml"),
        ("http://schemas.microsoft.com/office/2017/06/relationships/rdRichValueStructure", "richData/rdrichvaluestructure.xml"),
        ("http://schemas.microsoft.com/office/2017/06/relationships/rdRichValueTypes", "richData/rdRichValueTypes.xml"),
    ]
    for relation_id, (relation_type, target) in zip(relationship_ids, workbook_relationships):
        ET.SubElement(workbook_rels, f"{{{PACKAGE_RELS_NS}}}Relationship", Id=relation_id, Type=relation_type, Target=target)
    ET.register_namespace("", PACKAGE_RELS_NS)
    parts["xl/_rels/workbook.xml.rels"] = ET.tostring(workbook_rels, encoding="utf-8", xml_declaration=True)

    rich_count = len(image_cells) + 1
    future_blocks = "".join(
        f'<bk><extLst><ext uri="{{3e2802c4-a4d2-4d8b-9148-e3be6c30e623}}"><xlrd:rvb i="{index}"/></ext></extLst></bk>'
        for index in range(rich_count)
    )
    value_blocks = "".join(f'<bk><rc t="1" v="{index}"/></bk>' for index in range(rich_count))
    parts["xl/metadata.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<metadata xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:xlrd="http://schemas.microsoft.com/office/spreadsheetml/2017/richdata">'
        '<metadataTypes count="1"><metadataType name="XLRICHVALUE" minSupportedVersion="120000" copy="1" pasteAll="1" pasteValues="1" merge="1" splitFirst="1" rowColShift="1" clearFormats="1" clearComments="1" assign="1" coerce="1"/></metadataTypes>'
        f'<futureMetadata name="XLRICHVALUE" count="{rich_count}">{future_blocks}</futureMetadata>'
        f'<valueMetadata count="{rich_count}">{value_blocks}</valueMetadata></metadata>'
    ).encode("utf-8")

    relation_entries = "".join(f'<rel r:id="rId{index}"/>' for index in range(1, len(image_cells) + 1))
    parts["xl/richData/richValueRel.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<richValueRels xmlns="http://schemas.microsoft.com/office/spreadsheetml/2022/richvaluerel" xmlns:r="{DOCUMENT_RELS_NS}">{relation_entries}</richValueRels>'
    ).encode("utf-8")

    rich_values = "".join(f'<rv s="0"><v>{index}</v><v>5</v></rv>' for index in range(len(image_cells)))
    rich_values += f'<rv s="0"><v>{initial_image_index}</v><v>5</v></rv>'
    parts["xl/richData/rdrichvalue.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<rvData xmlns="http://schemas.microsoft.com/office/spreadsheetml/2017/richdata" count="{rich_count}">{rich_values}</rvData>'
    ).encode("utf-8")
    parts["xl/richData/rdrichvaluestructure.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<rvStructures xmlns="http://schemas.microsoft.com/office/spreadsheetml/2017/richdata" count="1"><s t="_localImage"><k n="_rvRel:LocalImageIdentifier" t="i"/><k n="CalcOrigin" t="i"/></s></rvStructures>'
    ).encode("utf-8")
    parts["xl/richData/rdRichValueTypes.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<rvTypesInfo xmlns="http://schemas.microsoft.com/office/spreadsheetml/2017/richdata2" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" mc:Ignorable="x" xmlns:x="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><global><keyFlags>'
        '<key name="_Self"><flag name="ExcludeFromFile" value="1"/><flag name="ExcludeFromCalcComparison" value="1"/></key><key name="_DisplayString"><flag name="ExcludeFromCalcComparison" value="1"/></key><key name="_Flags"><flag name="ExcludeFromCalcComparison" value="1"/></key><key name="_Format"><flag name="ExcludeFromCalcComparison" value="1"/></key><key name="_SubLabel"><flag name="ExcludeFromCalcComparison" value="1"/></key><key name="_Attribution"><flag name="ExcludeFromCalcComparison" value="1"/></key><key name="_Icon"><flag name="ExcludeFromCalcComparison" value="1"/></key><key name="_Display"><flag name="ExcludeFromCalcComparison" value="1"/></key><key name="_CanonicalPropertyNames"><flag name="ExcludeFromCalcComparison" value="1"/></key><key name="_ClassificationId"><flag name="ExcludeFromCalcComparison" value="1"/></key>'
        '</keyFlags></global></rvTypesInfo>'
    ).encode("utf-8")

    rel_items: list[str] = []
    existing_media = set(parts)
    for index, (_, image_bytes) in enumerate(image_cells, 1):
        suffix = index
        media_name = f"xl/media/profile-photo-{suffix}.png"
        while media_name in existing_media:
            suffix += len(image_cells)
            media_name = f"xl/media/profile-photo-{suffix}.png"
        existing_media.add(media_name)
        parts[media_name] = image_bytes
        rel_items.append(f'<Relationship Id="rId{index}" Type="{DOCUMENT_RELS_NS}/image" Target="../media/{media_name.rsplit("/", 1)[1]}"/>')
    parts["xl/richData/_rels/richValueRel.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{PACKAGE_RELS_NS}">{"".join(rel_items)}</Relationships>'
    ).encode("utf-8")

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as destination:
        for name, data in parts.items():
            destination.writestr(name, data)
    return output.getvalue()
