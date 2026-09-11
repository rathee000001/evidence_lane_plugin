"""Inspectable operation fidelity and exact per-lane format ownership."""
from .spreadsheet_parsers import OPENXML
from .structured_data import FORMATS
from .tabular_schema import LANE_TABLES, PREFIX


def format_contract(lane_id):
    body = {'schema': 'evidence-lane.tabular-formats.v4', 'lane_id': lane_id,
        'database_owner': lane_id, 'queries': ['metadata', 'text', *LANE_TABLES[lane_id]],
        'original_bytes_retained': True, 'source_changes_require_separate_export': True,
        'versions_and_files': 'owning_lane_only', 'query_sql_from_clients': False,
        'graph_files': [lane_id + '.mmd', lane_id + '.dot', lane_id + '.pointer.json'],
        'version_tables': [PREFIX[lane_id] + '_' + suffix for suffix in ('source', 'version', 'current', 'export', 'derivative')],
        'budgets': {'max_source_bytes': 8_388_608, 'max_facts_bytes': 16_777_216, 'max_fact_items': 50_000,
            'max_tables': 128, 'sampled_rows_per_table': 2000, 'sample_columns': 256, 'max_query_items': 100},
        'installed_native_execution_verified': False}
    if lane_id == 'data_excel':
        from .spreadsheet_rendering import SAFE_FUNCTIONS
        body.update(native_intake=sorted(OPENXML), value_reader_intake=['.xls', '.xlsb', '.ods'],
            generation=['.xlsx'], cell_edit=sorted(OPENXML), render=sorted(OPENXML), recalculate=['.xlsx'],
            native_formulas='source_expression_and_cached_value_separate', formula_dependencies='syntactic_A1_ranges_only',
            value_reader_limitations='Formula expressions and formatting not extracted; source bytes retained.',
            cell_edit_fidelity='Exact existing cells; active protection, merged/validated/formula ranges, rich text and extended cell metadata refused; unchanged package members preserved.',
            generated_numbers='At most 15 significant decimal digits within Excel normal range; output values independently reparsed and compared.',
            generated_dates='ISO dates and timezone-free times through milliseconds; submillisecond requests refused.',
            recalculation='Separate new Calc-written version; package formatting/objects may change.',
            render_equivalence='Calc page output is not Microsoft Excel equivalence proof.',
            library_inspection=['openpyxl_original_workbook', 'pandas_native_sample'],
            calc_allowed_functions=sorted(SAFE_FUNCTIONS), macros_executed=False, external_links_followed=False)
    elif lane_id == 'data':
        body.update(native_intake=sorted(FORMATS), generation=['.csv', '.tsv', '.json', '.jsonl'],
            transformations=['select_columns', 'typed_equals_filter', 'typed_not_equals_filter', 'is_null_filter',
                'homogeneous_numeric_or_text_sort', 'explicit_text_cast'], transform_requires_complete_source=True,
            csv_types='strings_without_automatic_inference', json_numbers='exact_decimal_values',
            numeric_filter_equality='Decimal numeric equivalence with source spelling preserved; numbers remain distinct from text and booleans.',
            arrow_containers=['Parquet', 'Arrow_IPC_file', 'Arrow_IPC_stream', 'Feather_V2'],
            unsupported_arrow_containers=['Feather_V1'],
            library_inspection=['pandas_native_sample', 'polars_native_sample', 'duckdb_in_memory_native_sample'],
            json_missing='distinct_from_null', nested_json='tagged_canonical_JSON_text',
            output_limitations='CSV collapses null/missing to empty fields; empty JSON has no column names; requested/selected columns remain in operation evidence; casts may lose types.')
    else:
        raise ValueError('TABULAR_LANE_UNSUPPORTED')
    return body
