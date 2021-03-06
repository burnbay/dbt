import os
import shutil
from datetime import datetime
from typing import Dict, List, Any, Optional

from hologram import ValidationError

from dbt.adapters.factory import get_adapter
from dbt.contracts.graph.compiled import CompileResultNode
from dbt.contracts.graph.manifest import Manifest
from dbt.contracts.results import (
    TableMetadata, CatalogTable, CatalogResults, Primitive, CatalogKey,
    StatsItem, StatsDict, ColumnMetadata
)
from dbt.include.global_project import DOCS_INDEX_FILE_PATH
import dbt.ui.printer
import dbt.utils
import dbt.compilation
import dbt.exceptions

from dbt.task.compile import CompileTask
from dbt.task.runnable import write_manifest


CATALOG_FILENAME = 'catalog.json'


def get_stripped_prefix(source: Dict[str, Any], prefix: str) -> Dict[str, Any]:
    """Go through source, extracting every key/value pair where the key starts
    with the given prefix.
    """
    cut = len(prefix)
    return {
        k[cut:]: v for k, v in source.items()
        if k.startswith(prefix)
    }


PrimitiveDict = Dict[str, Primitive]


def build_catalog_table(data) -> CatalogTable:
    # build the new table's metadata + stats
    metadata = TableMetadata.from_dict(get_stripped_prefix(data, 'table_'))
    stats = format_stats(get_stripped_prefix(data, 'stats:'))

    return CatalogTable(
        metadata=metadata,
        stats=stats,
        columns={},
    )


# keys are database name, schema name, table name
class Catalog(Dict[CatalogKey, CatalogTable]):
    def __init__(self, columns: List[PrimitiveDict]):
        super().__init__()
        for col in columns:
            self.add_column(col)

    def get_table(self, data: PrimitiveDict) -> CatalogTable:
        try:
            key = CatalogKey(
                str(data['table_database']),
                str(data['table_schema']),
                str(data['table_name']),
            )
        except KeyError as exc:
            raise dbt.exceptions.CompilationException(
                'Catalog information missing required key {} (got {})'
                .format(exc, data)
            )
        table: CatalogTable
        if key in self:
            table = self[key]
        else:
            table = build_catalog_table(data)
            self[key] = table
        return table

    def add_column(self, data: PrimitiveDict):
        table = self.get_table(data)
        column_data = get_stripped_prefix(data, 'column_')
        # the index should really never be that big so it's ok to end up
        # serializing this to JSON (2^53 is the max safe value there)
        column_data['index'] = int(column_data['index'])

        column = ColumnMetadata.from_dict(column_data)
        table.columns[column.name] = column

    def make_unique_id_map(
        self, manifest: Manifest
    ) -> Dict[str, CatalogTable]:
        nodes: Dict[str, CatalogTable] = {}

        manifest_mapping = get_unique_id_mapping(manifest)
        for table in self.values():
            unique_ids = manifest_mapping.get(table.key(), [])
            for unique_id in unique_ids:
                if unique_id in nodes:
                    dbt.exceptions.raise_ambiguous_catalog_match(
                        unique_id, nodes[unique_id].to_dict(), table.to_dict()
                    )
                else:
                    nodes[unique_id] = table.replace(unique_id=unique_id)
        return nodes


def format_stats(stats: PrimitiveDict) -> StatsDict:
    """Given a dictionary following this layout:

        {
            'encoded:label': 'Encoded',
            'encoded:value': 'Yes',
            'encoded:description': 'Indicates if the column is encoded',
            'encoded:include': True,

            'size:label': 'Size',
            'size:value': 128,
            'size:description': 'Size of the table in MB',
            'size:include': True,
        }

    format_stats will convert the dict into a StatsDict with keys of 'encoded'
    and 'size'.
    """
    stats_collector: StatsDict = {}

    base_keys = {k.split(':')[0] for k in stats}
    for key in base_keys:
        dct: PrimitiveDict = {'id': key}
        for subkey in ('label', 'value', 'description', 'include'):
            dct[subkey] = stats['{}:{}'.format(key, subkey)]

        try:
            stats_item = StatsItem.from_dict(dct)
        except ValidationError:
            continue
        if stats_item.include:
            stats_collector[key] = stats_item

    # we always have a 'has_stats' field, it's never included
    has_stats = StatsItem(
        id='has_stats',
        label='Has Stats?',
        value=len(stats_collector) > 0,
        description='Indicates whether there are statistics for this table',
        include=False,
    )
    stats_collector['has_stats'] = has_stats
    return stats_collector


def mapping_key(node: CompileResultNode) -> CatalogKey:
    return CatalogKey(
        node.database.lower(), node.schema.lower(), node.identifier.lower()
    )


def get_unique_id_mapping(manifest: Manifest) -> Dict[CatalogKey, List[str]]:
    # A single relation could have multiple unique IDs pointing to it if a
    # source were also a node.
    ident_map: Dict[CatalogKey, List[str]] = {}
    for unique_id, node in manifest.nodes.items():
        key = mapping_key(node)

        if key not in ident_map:
            ident_map[key] = []

        ident_map[key].append(unique_id)
    return ident_map


def _coerce_decimal(value):
    if isinstance(value, dbt.utils.DECIMALS):
        return float(value)
    return value


class GenerateTask(CompileTask):
    def _get_manifest(self) -> Manifest:
        return self.manifest

    def run(self):
        compile_results = None
        if self.args.compile:
            compile_results = CompileTask.run(self)
            if any(r.error is not None for r in compile_results):
                dbt.ui.printer.print_timestamped_line(
                    'compile failed, cannot generate docs'
                )
                return CatalogResults({}, datetime.utcnow(), compile_results)

        shutil.copyfile(
            DOCS_INDEX_FILE_PATH,
            os.path.join(self.config.target_path, 'index.html'))

        adapter = get_adapter(self.config)
        with adapter.connection_named('generate_catalog'):
            dbt.ui.printer.print_timestamped_line("Building catalog")
            catalog_table = adapter.get_catalog(self.manifest)

        catalog_data: List[PrimitiveDict] = [
            dict(zip(catalog_table.column_names, map(_coerce_decimal, row)))
            for row in catalog_table
        ]

        catalog = Catalog(catalog_data)
        results = self.get_catalog_results(
            nodes=catalog.make_unique_id_map(self.manifest),
            generated_at=datetime.utcnow(),
            compile_results=compile_results,
        )

        path = os.path.join(self.config.target_path, CATALOG_FILENAME)
        results.write(path)
        write_manifest(self.config, self.manifest)

        dbt.ui.printer.print_timestamped_line(
            'Catalog written to {}'.format(os.path.abspath(path))
        )
        return results

    def get_catalog_results(
        self,
        nodes: Dict[str, CatalogTable],
        generated_at: datetime,
        compile_results: Optional[Any]
    ) -> CatalogResults:
        return CatalogResults(
            nodes=nodes,
            generated_at=generated_at,
            _compile_results=compile_results,
        )

    def interpret_results(self, results):
        compile_results = results._compile_results
        if compile_results is None:
            return True

        return super().interpret_results(compile_results)
