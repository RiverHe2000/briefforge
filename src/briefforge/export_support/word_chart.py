"""Minimal editable OOXML column chart with an embedded Excel workbook."""

from __future__ import annotations

from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import parse_xml

C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _workbook(labels: list[str], values: list[float], series: str) -> bytes:
    buffer = BytesIO()
    rows = [("项目", series), *zip(labels, values)]
    xml_rows = []
    for number, row in enumerate(rows, 1):
        cells = []
        for column, value in zip(("A", "B"), row):
            if isinstance(value, (int, float)):
                cells.append(f'<c r="{column}{number}"><v>{value}</v></c>')
            else:
                cells.append(
                    f'<c r="{column}{number}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
                )
        xml_rows.append(f'<row r="{number}">{"".join(cells)}</row>')
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        )
        archive.writestr(
            "_rels/.rels",
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{R}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="{R}"><sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{R}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>',
        )
    return buffer.getvalue()


def add_editable_chart(document, chart: dict) -> None:
    labels, values = chart["labels"], chart["values"]
    labels_xml = "".join(
        f'<c:pt idx="{i}"><c:v>{escape(label)}</c:v></c:pt>' for i, label in enumerate(labels)
    )
    values_xml = "".join(f'<c:pt idx="{i}"><c:v>{value}</c:v></c:pt>' for i, value in enumerate(values))
    n = len(labels)
    label_format = "0.00" if chart.get("kind") == "price" else "0"
    chart_xml = f'''<c:chartSpace xmlns:c="{C}" xmlns:a="{A}" xmlns:r="{R}">
    <c:lang val="zh-CN"/><c:roundedCorners val="0"/><c:chart><c:autoTitleDeleted val="1"/><c:plotArea><c:layout/>
    <c:barChart><c:barDir val="col"/><c:grouping val="clustered"/><c:varyColors val="0"/>
    <c:ser><c:idx val="0"/><c:order val="0"/><c:tx><c:v>{escape(chart["series"])}</c:v></c:tx>
    <c:spPr><a:solidFill><a:srgbClr val="275C4C"/></a:solidFill></c:spPr>
    <c:cat><c:strRef><c:f>Data!$A$2:$A${n + 1}</c:f><c:strCache><c:ptCount val="{n}"/>{labels_xml}</c:strCache></c:strRef></c:cat>
    <c:val><c:numRef><c:f>Data!$B$2:$B${n + 1}</c:f><c:numCache><c:formatCode>0.##</c:formatCode><c:ptCount val="{n}"/>{values_xml}</c:numCache></c:numRef></c:val>
    </c:ser><c:dLbls><c:numFmt formatCode="{label_format}" sourceLinked="0"/><c:dLblPos val="outEnd"/><c:showLegendKey val="0"/><c:showVal val="1"/><c:showCatName val="0"/><c:showSerName val="0"/><c:showPercent val="0"/><c:showBubbleSize val="0"/></c:dLbls><c:gapWidth val="90"/><c:axId val="123"/><c:axId val="456"/></c:barChart>
    <c:catAx><c:axId val="123"/><c:scaling><c:orientation val="minMax"/></c:scaling><c:delete val="0"/><c:axPos val="b"/><c:tickLblPos val="nextTo"/><c:crossAx val="456"/><c:crosses val="autoZero"/><c:lblOffset val="100"/></c:catAx>
    <c:valAx><c:axId val="456"/><c:scaling><c:orientation val="minMax"/><c:min val="0"/></c:scaling><c:delete val="0"/><c:axPos val="l"/><c:majorGridlines><c:spPr><a:ln w="6350"><a:solidFill><a:srgbClr val="D9DEDA"/></a:solidFill></a:ln></c:spPr></c:majorGridlines><c:numFmt formatCode="0" sourceLinked="0"/><c:majorTickMark val="none"/><c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/><c:crossAx val="123"/><c:crosses val="autoZero"/></c:valAx>
    <c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr></c:plotArea><c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/></c:chart><c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr><c:externalData r:id="rId1"><c:autoUpdate val="0"/></c:externalData></c:chartSpace>'''
    package = document.part.package
    chart_part = Part(
        PackURI("/word/charts/chart1.xml"),
        "application/vnd.openxmlformats-officedocument.drawingml.chart+xml",
        chart_xml.encode(),
        package,
    )
    workbook_part = Part(
        PackURI("/word/embeddings/chart-data.xlsx"),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        _workbook(labels, values, chart["series"]),
        package,
    )
    chart_part.relate_to(workbook_part, f"{R}/package")
    relationship = document.part.relate_to(chart_part, f"{R}/chart")
    drawing = parse_xml(
        f'''<w:drawing xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:a="{A}" xmlns:c="{C}" xmlns:r="{R}"><wp:inline><wp:extent cx="5486400" cy="2286000"/><wp:docPr id="101" name="Editable comparison chart" descr="{escape(chart["title"])}"/><a:graphic><a:graphicData uri="{C}"><c:chart r:id="{relationship}"/></a:graphicData></a:graphic></wp:inline></w:drawing>'''
    )
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.line_spacing = 1.0
    paragraph.add_run()._r.append(drawing)
