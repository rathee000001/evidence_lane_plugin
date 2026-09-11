"""Independent source documents for the Power BI format contract tests."""

import json

PREFIX = "https://developer.microsoft.com/json-schemas/"


def model_document():
    return {
        "name": "EvidenceLaneFixture",
        "compatibilityLevel": 1600,
        "model": {
            "culture": "en-US",
            "tables": [
                {
                    "name": "Facts",
                    "columns": [{"name": "Value", "dataType": "int64", "sourceColumn": "Value"}],
                    "measures": [{"name": "Total", "expression": "SUM(Facts[Value])"}],
                }
            ],
        },
    }


def project_documents():
    documents = {
        "Example.pbip": {
            "$schema": PREFIX + "fabric/pbip/pbipProperties/1.0.0/schema.json",
            "version": "1.0",
            "artifacts": [{"report": {"path": "Example.Report"}}],
        },
        "Example.Report/definition.pbir": {
            "$schema": PREFIX + "fabric/item/report/definitionProperties/2.0.0/schema.json",
            "version": "4.0",
            "datasetReference": {"byPath": {"path": "../Example.SemanticModel"}},
        },
        "Example.Report/definition/version.json": {
            "$schema": PREFIX + "fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
            "version": "4.0.0",
        },
        "Example.Report/definition/report.json": {
            "$schema": PREFIX + "fabric/item/report/definition/report/3.3.0/schema.json",
            "themeCollection": {},
            "annotations": [{"name": "fixture", "value": "not a rendered report"}],
        },
        "Example.Report/definition/pages/Main/page.json": {
            "$schema": PREFIX + "fabric/item/report/definition/page/2.1.0/schema.json",
            "name": "Main",
            "displayName": "Overview",
            "displayOption": "FitToPage",
            "height": 720,
            "width": 1280,
        },
        "Example.Report/definition/pages/Main/visuals/Card/visual.json": {
            "$schema": PREFIX + "fabric/item/report/definition/visualContainer/2.9.0/schema.json",
            "name": "Card",
            "position": {"x": 10, "y": 10, "height": 160, "width": 260},
            "visual": {"visualType": "card"},
        },
        "Example.SemanticModel/definition.pbism": {
            "$schema": PREFIX + "fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
            "version": "4.0",
        },
        "Example.SemanticModel/model.bim": model_document(),
    }
    return {
        name: json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        for name, value in documents.items()
    }


def tmdl_documents():
    return {
        "model.tmdl": "model Model\n\tculture: en-US\n\nref table Facts\n",
        "tables/Facts.tmdl": "table Facts\n\tcolumn Value\n\t\tdataType: int64\n\t\tsourceColumn: Value\n\tmeasure Total = SUM(Facts[Value])\n",
    }
