"""Real Spark transformations over synthetic inputs, with catalog I/O isolated.

Run separately with: python -m unittest discover -s tests/integration -v
Databricks display and Unity Catalog/Delta writes are replaced at the boundary
by local temporary views. Expressions, grouping and SQL run in Spark.
No original CSV data or Databricks workspace is required or validated.
"""

import contextlib
import datetime
import io
import json
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from pyspark.sql import SparkSession
from pyspark.sql.readwriter import DataFrameWriter


ROOT = Path(__file__).resolve().parents[2]
TABLE_PATTERN = re.compile(r"classes\.(bronze|silver|gold)\.([a-z_]+)")


def local_name(name):
    return name.removeprefix("classes.").replace(".", "__")


class LocalCatalog:
    def __init__(self, spark):
        self.spark = spark

    def table(self, name):
        return self.spark.table(local_name(name))

    def sql(self, statement):
        rewritten = TABLE_PATTERN.sub(lambda match: "__".join(match.groups()), statement)
        return self.spark.sql(rewritten)


def execute_notebook(path, spark):
    """Use unchanged notebook expressions; replace only the catalog boundary."""
    notebook = json.loads((ROOT / path).read_text(encoding="utf-8"))
    namespace = {"spark": LocalCatalog(spark), "display": lambda frame: None}

    def publish_view(writer, name, *args, **kwargs):
        # This replaces unavailable Databricks table persistence, not computation.
        writer._df.createOrReplaceTempView(local_name(name))

    with patch.object(DataFrameWriter, "saveAsTable", publish_view), contextlib.redirect_stdout(io.StringIO()):
        for number, cell in enumerate(notebook["cells"], 1):
            if cell["cell_type"] != "code":
                continue
            source = "".join(cell["source"])
            if source.lstrip().startswith("%sql"):
                namespace["spark"].sql(source.lstrip()[4:].strip())
            else:
                exec(compile(source, f"{path}:cell-{number}", "exec"), namespace)
    return namespace


def invoice_input(spark):
    return spark.sql("""
        SELECT nr_nota_fiscal, nr_linha_nf, nr_pedido_referencia,
               nome_vendedor, data_emissao_nf, data_vencimento_nf,
               cod_produto, nome_produto, qtde_produto,
               CAST(valor_total_produto AS DOUBLE) AS valor_total_produto,
               CAST(imposto_produto AS DOUBLE) AS imposto_produto
        FROM VALUES
          ('NF-1', 1, 'PED-1', ' ana souza ', '2025-01-05', '2025-01-20', 'P1', ' pneu a ', 2, 100.0, 2.345),
          ('NF-1', 1, 'PED-1', ' ana souza ', '2025-01-05', '2025-01-20', 'P1', ' pneu a ', 2, 100.0, 2.345),
          ('NF-1', 2, 'PED-1', ' ana souza ', '2025-01-05', '2025-01-20', 'P2', ' bateria b ', 1, 300.0, 6.0),
          ('NF-2', 1, 'PED-2', ' bia lima ', '2025-02-10', '2025-02-28', 'P1', ' pneu a ', 1, 50.0, 1.0)
        AS input(nr_nota_fiscal, nr_linha_nf, nr_pedido_referencia,
                 nome_vendedor, data_emissao_nf, data_vencimento_nf,
                 cod_produto, nome_produto, qtde_produto,
                 valor_total_produto, imposto_produto)
    """)


def order_input(spark):
    return spark.sql("""
        SELECT nr_pedido, nr_linha_pedido, data_pedido, nome_vendedor,
               estado, cidade, nome_produto, cod_produto, grupo_produto,
               qtde_produtos, CAST(preco_unitario AS DOUBLE) AS preco_unitario,
               CAST(valor_imposto AS DOUBLE) AS valor_imposto,
               CAST(valor_total_linha AS DOUBLE) AS valor_total_linha, status_pedido
        FROM VALUES
          ('PED-1', 1, '05-01-2025', ' ana souza ', ' SP ', ' sao paulo ', ' pneu a ', 'P1', 'Peças', 2, 50.0, 2.345, 100.0, ' aprovado '),
          ('PED-1', 1, '05-01-2025', ' ana souza ', ' SP ', ' sao paulo ', ' pneu a ', 'P1', 'Peças', 2, 50.0, 2.345, 100.0, ' aprovado '),
          ('PED-1', 2, '05-01-2025', ' ana souza ', ' SP ', ' sao paulo ', ' bateria b ', 'P2', 'Peças', 1, 300.0, 6.0, 300.0, ' aprovado '),
          ('PED-2', 1, '10-02-2025', ' bia lima ', ' RJ ', ' rio de janeiro ', ' pneu a ', 'P1', 'Peças', 1, 50.0, 1.0, 50.0, ' cancelado '),
          ('PED-3', 1, '15-02-2025', ' caio reis ', ' MG ', ' belo horizonte ', ' sensor c ', 'P3', 'Peças', CAST(NULL AS INT), CAST(NULL AS DOUBLE), CAST(NULL AS DOUBLE), CAST(NULL AS DOUBLE), ' pendente ')
        AS input(nr_pedido, nr_linha_pedido, data_pedido, nome_vendedor,
                 estado, cidade, nome_produto, cod_produto, grupo_produto,
                 qtde_produtos, preco_unitario, valor_imposto,
                 valor_total_linha, status_pedido)
    """)


class SparkDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
        cls.spark = (
            SparkSession.builder.master("local[1]")
            .appName("automotive-synthetic-data-tests")
            .config("spark.ui.enabled", "false")
            .config("spark.driver.host", "127.0.0.1")
            .config("spark.driver.bindAddress", "127.0.0.1")
            .config("spark.sql.shuffle.partitions", "1")
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def setUp(self):
        # Drop only views owned by this fixture. Listing persistent tables would
        # initialize a filesystem catalog and require Hadoop native I/O on Windows.
        names = (
            "bronze__notas_fiscais", "bronze__pedidos",
            "silver__notas_fiscais", "silver__pedidos",
            "gold__faturamento_mensal", "gold__performance_vendedores",
            "gold__vendas_por_estado", "gold__mix_produtos", "gold__funil_vendas",
        )
        for name in names:
            self.spark.catalog.dropTempView(name)
        invoice_input(self.spark).createOrReplaceTempView("bronze__notas_fiscais")
        order_input(self.spark).createOrReplaceTempView("bronze__pedidos")

    def run_silver(self):
        execute_notebook("industria/silver/processing_data_nf.ipynb", self.spark)
        execute_notebook("industria/silver/processing_data_pedidos.ipynb", self.spark)

    def run_gold(self):
        execute_notebook("industria/gold/processing_nf.ipynb", self.spark)
        execute_notebook("industria/gold/processing_pedido.ipynb", self.spark)

    def test_invoice_silver_deduplicates_and_normalizes_dates_and_text(self):
        execute_notebook("industria/silver/processing_data_nf.ipynb", self.spark)
        frame = self.spark.table("silver__notas_fiscais")
        self.assertEqual(frame.count(), 3)
        row = frame.where("invoice_id = 'NF-1' AND invoice_line_id = 1").first()
        self.assertEqual((row.salesperson_name, row.product_name), ("Ana Souza", "Pneu A"))
        self.assertEqual((row.invoice_date, row.due_date), (datetime.date(2025, 1, 5), datetime.date(2025, 1, 20)))
        self.assertEqual((row.invoice_year, row.invoice_month), (2025, 1))
        self.assertAlmostEqual(row.tax_amount, 2.35)

    def test_order_silver_deduplicates_normalizes_and_fills_numeric_nulls(self):
        execute_notebook("industria/silver/processing_data_pedidos.ipynb", self.spark)
        frame = self.spark.table("silver__pedidos")
        self.assertEqual(frame.count(), 4)
        row = frame.where("order_id = 'PED-1' AND order_line_id = 1").first()
        self.assertEqual((row.state, row.city, row.order_status), ("SP", "Sao Paulo", "Aprovado"))
        self.assertEqual((row.order_date, row.order_year, row.order_month), (datetime.date(2025, 1, 5), 2025, 1))
        self.assertAlmostEqual(row.tax_amount, 2.35)
        null_row = frame.where("order_id = 'PED-3'").first()
        self.assertEqual((null_row.product_quantity, null_row.unit_price, null_row.tax_amount, null_row.total_line_value), (0, 0.0, 0.0, 0.0))

    def test_invoice_gold_has_hand_checked_monthly_totals_and_line_ticket(self):
        self.run_silver()
        execute_notebook("industria/gold/processing_nf.ipynb", self.spark)
        monthly = self.spark.table("gold__faturamento_mensal").orderBy("invoice_month").collect()
        actual = [(r.invoice_year, r.invoice_month, r.faturamento_bruto, r.total_impostos, r.qtde_notas_emitidas, r.total_itens_vendidos) for r in monthly]
        self.assertEqual(actual, [(2025, 1, 400.0, 8.35, 2, 3), (2025, 2, 50.0, 1.0, 1, 1)])
        sellers = self.spark.table("gold__performance_vendedores").orderBy("salesperson_name").collect()
        self.assertEqual([(r.salesperson_name, r.valor_total_vendas, r.total_vendas_feitas, r.ticket_medio) for r in sellers], [("Ana Souza", 400.0, 2, 200.0), ("Bia Lima", 50.0, 1, 50.0)])

    def test_order_gold_has_hand_checked_state_mix_and_status_totals(self):
        self.run_silver()
        execute_notebook("industria/gold/processing_pedido.ipynb", self.spark)
        states = self.spark.table("gold__vendas_por_estado").orderBy("state").collect()
        self.assertEqual([(r.state, r.receita_total, r.volume_pedidos, r.ticket_medio) for r in states], [("MG", 0.0, 1, 0.0), ("RJ", 50.0, 1, 50.0), ("SP", 400.0, 2, 200.0)])
        products = self.spark.table("gold__mix_produtos").orderBy("product_name").collect()
        self.assertEqual([(r.product_name, r.qtd_vendida, r.receita_gerada) for r in products], [("Bateria B", 1, 300.0), ("Pneu A", 3, 150.0), ("Sensor C", 0, 0.0)])
        statuses = self.spark.table("gold__funil_vendas").orderBy("order_status").collect()
        self.assertEqual([(r.order_status, r.total_pedidos, r.valor_em_jogo) for r in statuses], [("Aprovado", 2, 400.0), ("Cancelado", 1, 50.0), ("Pendente", 1, 0.0)])

    def test_reexecution_keeps_same_values_in_all_five_gold_views(self):
        names = ["faturamento_mensal", "performance_vendedores", "vendas_por_estado", "mix_produtos", "funil_vendas"]

        def snapshot():
            return {name: sorted(tuple(row) for row in self.spark.table("gold__" + name).collect()) for name in names}

        self.run_silver()
        self.run_gold()
        first = snapshot()
        self.run_silver()
        self.run_gold()
        self.assertEqual(snapshot(), first)

    def test_visual_period_is_padded_and_chronological(self):
        self.run_silver()
        self.run_gold()
        namespace = execute_notebook("industria/gold/visual.ipynb", self.spark)
        self.assertEqual([tuple(row) for row in namespace["df_visual"].collect()], [("2025-01", 400.0), ("2025-02", 50.0)])


if __name__ == "__main__":
    unittest.main()
