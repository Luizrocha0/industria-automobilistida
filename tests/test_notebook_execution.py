"""Execute notebook coordination with schema/catalog doubles, not Spark values.

No rows, expression evaluation, SQL projection, or Databricks integration are
simulated. These tests cover clean namespaces, partition columns, publication
order, and repeatable setup only.
"""

import contextlib
import io
import json
from pathlib import Path
import re
import sys
from types import ModuleType
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class Expression:
    def alias(self, name):
        self.name = name
        return self

    def desc(self):
        return self


def expression(*args):
    return Expression()


class SchemaFrame:
    """Carry column names only; transformations never calculate values."""

    def __init__(self, catalog, columns):
        self.catalog = catalog
        self.columns = set(columns)

    def withColumnRenamed(self, old, new):
        return SchemaFrame(self.catalog, (self.columns - {old}) | {new})

    def withColumn(self, name, value):
        return SchemaFrame(self.catalog, self.columns | {name})

    def dropDuplicates(self, columns):
        return self

    def printSchema(self):
        pass

    @property
    def na(self):
        return self

    def fill(self, value, subset):
        return self

    def groupBy(self, *columns):
        return GroupedSchema(self.catalog, columns)

    def orderBy(self, *columns):
        return self

    @property
    def write(self):
        return TableWriter(self)


class GroupedSchema:
    def __init__(self, catalog, columns):
        self.catalog, self.columns = catalog, columns

    def agg(self, *expressions):
        return SchemaFrame(self.catalog, (*self.columns, *(e.name for e in expressions)))


class TableWriter:
    def __init__(self, frame):
        self.frame, self.partitions = frame, ()

    def mode(self, mode):
        if mode != "overwrite":
            raise ValueError("Only overwrite publication is supported by this fixture")
        return self

    def partitionBy(self, *columns):
        self.partitions = columns
        return self

    def saveAsTable(self, name):
        missing = set(self.partitions) - self.frame.columns
        if missing:
            raise ValueError(f"Partition columns missing: {sorted(missing)}")
        self.frame.catalog.tables[name] = self.frame
        self.frame.catalog.publications.append((name, self.partitions))


class Catalog:
    def __init__(self, schemas=None):
        self.tables = {name: SchemaFrame(self, columns) for name, columns in (schemas or {}).items()}
        self.objects, self.publications, self.reads = set(), [], []

    def table(self, name):
        if name not in self.tables:
            raise LookupError(f"Table does not exist: {name}")
        frame = self.tables[name]
        self.reads.append((name, frame))
        return frame

    def sql(self, statement):
        ddl = re.fullmatch(r"\s*CREATE (CATALOG|SCHEMA) (IF NOT EXISTS )?([\w.]+)\s*;?\s*", statement, re.I)
        if ddl:
            kind, repeatable, name = ddl.groups()
            key = (kind.upper(), name)
            if key in self.objects and not repeatable:
                raise ValueError(f"{kind} already exists: {name}")
            self.objects.add(key)
            return None
        source = re.search(r"\bFROM\s+([\w.]+)", statement, re.I)
        if source:
            return self.table(source.group(1))
        raise ValueError(f"Unsupported fixture SQL: {statement}")


def spark_imports():
    modules = {name: ModuleType(name) for name in (
        "pyspark", "pyspark.sql", "pyspark.sql.functions", "pyspark.sql.types"
    )}
    modules["pyspark.sql"].SparkSession = object
    for name in ("col to_date year lit when trim regexp_replace initcap month round "
                 "date_format sum count avg desc").split():
        setattr(modules["pyspark.sql.functions"], name, expression)
    for name in ("IntegerType", "DoubleType", "StringType", "DateType"):
        setattr(modules["pyspark.sql.types"], name, object)
    return modules


def execute_notebook(path, catalog):
    """Continue after cell errors to expose independent notebook regressions."""
    notebook = json.loads((ROOT / path).read_text(encoding="utf-8"))
    namespace = {"spark": catalog, "display": lambda frame: None}
    errors = []
    with patch.dict(sys.modules, spark_imports()), contextlib.redirect_stdout(io.StringIO()):
        for number, cell in enumerate(notebook["cells"], 1):
            if cell["cell_type"] != "code":
                continue
            source = "".join(cell["source"])
            try:
                if source.lstrip().startswith("%sql"):
                    catalog.sql(source.lstrip()[4:].strip())
                else:
                    exec(compile(source, f"{path}:cell-{number}", "exec"), namespace)
            except (NameError, LookupError, ValueError) as error:
                errors.append(f"cell {number}: {type(error).__name__}: {error}")
    return errors


class NotebookExecutionTests(unittest.TestCase):
    def test_setup_can_run_twice_in_same_catalog(self):
        catalog = Catalog()
        path = "industria/bronze/create-objects.ipynb"
        self.assertEqual(execute_notebook(path, catalog), [])
        self.assertEqual(execute_notebook(path, catalog), [])
        self.assertEqual(catalog.objects, {
            ("CATALOG", "classes"), ("SCHEMA", "classes.bronze"),
            ("SCHEMA", "classes.silver"), ("SCHEMA", "classes.gold"),
        })

    def nf_catalog(self):
        return Catalog({"classes.bronze.notas_fiscais": (
            "nr_nota_fiscal nr_linha_nf nr_pedido_referencia nome_vendedor data_emissao_nf "
            "data_vencimento_nf cod_produto nome_produto qtde_produto valor_total_produto imposto_produto"
        ).split()})

    def test_nf_has_no_undefined_namespace_in_clean_session(self):
        self.assertEqual(execute_notebook("industria/silver/processing_data_nf.ipynb", self.nf_catalog()), [])

    def test_nf_publishes_contract_table_once_with_existing_partitions(self):
        catalog = self.nf_catalog()
        execute_notebook("industria/silver/processing_data_nf.ipynb", catalog)
        self.assertEqual(catalog.publications, [
            ("classes.silver.notas_fiscais", ("invoice_year", "invoice_month")),
        ])

    def test_orders_publishes_with_existing_date_partition_columns(self):
        catalog = Catalog({"classes.bronze.pedidos": (
            "nr_pedido nr_linha_pedido data_pedido nome_vendedor estado cidade nome_produto "
            "cod_produto grupo_produto qtde_produtos preco_unitario valor_imposto valor_total_linha status_pedido"
        ).split()})
        self.assertEqual(execute_notebook("industria/silver/processing_data_pedidos.ipynb", catalog), [])
        self.assertEqual(catalog.publications, [("classes.silver.pedidos", ("order_year", "order_month"))])

    def check_gold(self, path, source, columns, destinations, existing):
        catalog = Catalog({source: columns.split()})
        if existing:
            for name in destinations:
                catalog.tables[name] = SchemaFrame(catalog, ["previous_execution"])
        self.assertEqual(execute_notebook(path, catalog), [])
        self.assertEqual({name for name, _ in catalog.publications}, set(destinations))
        gold_reads = [(name, frame) for name, frame in catalog.reads if name in destinations]
        self.assertEqual({name for name, _ in gold_reads}, set(destinations))
        for name, frame in gold_reads:
            self.assertIs(frame, catalog.tables[name], f"Query read a stale version of {name}")

    def test_gold_nf_queries_current_publications_in_clean_and_existing_catalogs(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                self.check_gold("industria/gold/processing_nf.ipynb", "classes.silver.notas_fiscais",
                                "invoice_year invoice_month total_line_value tax_amount invoice_id product_quantity salesperson_name",
                                ("classes.gold.faturamento_mensal", "classes.gold.performance_vendedores"), existing)

    def test_gold_orders_queries_current_publications_in_clean_and_existing_catalogs(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                self.check_gold("industria/gold/processing_pedido.ipynb", "classes.silver.pedidos",
                                "state total_line_value order_id product_group product_name product_quantity order_status",
                                ("classes.gold.vendas_por_estado", "classes.gold.mix_produtos", "classes.gold.funil_vendas"), existing)


if __name__ == "__main__":
    unittest.main()
