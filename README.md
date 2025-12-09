# Engenharia de dados para a indústria automobilística

Pipeline analítico de pedidos e notas fiscais no Databricks, organizado em camadas Bronze, Silver e Gold.

**Contexto:** projeto acadêmico desenvolvido no semestre **2025.2**, em equipe. Este repositório reúne os notebooks e a documentação do pipeline em camadas Bronze, Silver e Gold.

## Objetivo

Organizar dados operacionais de pedidos e faturamento para apoiar análises comerciais. O trabalho demonstra transformação de dados com PySpark, consultas SQL, agregações de negócio e visualizações no Databricks.

As análises disponíveis incluem valores mensais de notas fiscais, desempenho por vendedor, distribuição de pedidos por estado, mix de produtos e distribuição por status.

## Arquitetura

```mermaid
flowchart LR
    A["CSV de pedidos e notas fiscais"] -->|"Carga no ambiente original"| B["Bronze: tabelas de origem"]
    B --> C["Silver: padronização e deduplicação"]
    C --> D["Gold: cinco tabelas analíticas"]
    D --> E["Consultas e visualizações no Databricks"]
```

A carga dos CSV ocorreu no ambiente original. Os arquivos de origem e o procedimento de ingestão não estão versionados. Os notebooks publicados partem das tabelas `classes.bronze.pedidos` e `classes.bronze.notas_fiscais`.

## Tecnologias

| Tecnologia | Papel no projeto |
| --- | --- |
| Databricks | Ambiente de execução, catálogo de tabelas e visualizações |
| PySpark | Padronização, deduplicação, transformação e agregação |
| Spark SQL | Criação de objetos e consultas analíticas |
| Delta Lake | Formato padrão de tabelas no Databricks; as escritas usam `saveAsTable` |

Os notebooks não fixam explicitamente o formato de escrita. A documentação do Databricks descreve [Delta Lake como formato padrão](https://docs.databricks.com/aws/en/delta/tutorial); a versão e a configuração do ambiente original não estão registradas.

## Organização do repositório

```text
industria/
├── bronze/
│   ├── create-objects.ipynb
│   └── inicializar.ipynb
├── silver/
│   ├── processing_data_nf.ipynb
│   └── processing_data_pedidos.ipynb
└── gold/
    ├── processing_nf.ipynb
    ├── processing_pedido.ipynb
    └── visual.ipynb
```

| Camada | Conteúdo publicado |
| --- | --- |
| Bronze | Criação do catálogo `classes` e dos três schemas; consultas às tabelas de origem |
| Silver | Renomeação de campos, tratamento de texto, conversão de datas e deduplicação por documento e linha |
| Gold | Agregações de notas fiscais e pedidos, persistência de tabelas e consultas para visualização |

## Tabelas analíticas

As tabelas são gravadas no schema `classes.gold`.

| Tabela | Agrupamento | Medidas calculadas |
| --- | --- | --- |
| `faturamento_mensal` | Ano e mês de emissão | Soma dos valores de linhas, impostos, quantidades e contagem de linhas com identificador de nota |
| `performance_vendedores` | Nome do vendedor | Soma dos valores de notas, contagem de linhas e valor médio por linha |
| `vendas_por_estado` | Estado | Soma dos valores de pedidos, contagem de linhas e valor médio por linha |
| `mix_produtos` | Grupo e nome do produto | Soma de quantidades e valores de linhas de pedidos |
| `funil_vendas` | Status do pedido | Contagem de linhas e soma dos valores por status |

**Como interpretar:** os identificadores de pedido e nota podem aparecer em várias linhas de itens. O código usa `count` desses identificadores, e não contagem distinta de documentos. O campo `ticket_medio` corresponde a `avg(total_line_value)`, isto é, média por linha. Essas medidas precisam dessa ressalva ao serem apresentadas como indicadores de negócio.

As agregações de pedidos incluem todos os status. Seus valores não representam, por si só, receita faturada. A tabela `funil_vendas` apresenta uma distribuição por status; não acompanha transições ou taxas de conversão entre etapas.

## Regras de transformação

| Conjunto | Regras presentes no código |
| --- | --- |
| Pedidos | Campos renomeados para inglês; deduplicação por `order_id` e `order_line_id`; tratamento de texto; nulos numéricos substituídos por zero; imposto arredondado; data interpretada como `dd-MM-yyyy` |
| Notas fiscais | Campos renomeados para inglês; deduplicação por `invoice_id` e `invoice_line_id`; tratamento de texto; imposto arredondado; datas convertidas com `to_date`; criação de ano e mês de emissão |

A granularidade usada na deduplicação é a **linha do documento**. Os tipos numéricos dependem das tabelas Bronze: os notebooks não impõem um contrato completo de tipos ou de precisão monetária.

## Dados de entrada

Os notebooks esperam tabelas Bronze com os campos abaixo. A lista foi extraída dos mapeamentos do código; não substitui o schema original, que não está disponível.

| Tabela Bronze | Campos utilizados |
| --- | --- |
| `classes.bronze.pedidos` | `nr_pedido`, `nr_linha_pedido`, `data_pedido`, `nome_vendedor`, `estado`, `cidade`, `nome_produto`, `cod_produto`, `grupo_produto`, `qtde_produtos`, `preco_unitario`, `valor_imposto`, `valor_total_linha`, `status_pedido` |
| `classes.bronze.notas_fiscais` | `nr_nota_fiscal`, `nr_linha_nf`, `nr_pedido_referencia`, `nome_vendedor`, `data_emissao_nf`, `data_vencimento_nf`, `cod_produto`, `nome_produto`, `qtde_produto`, `valor_total_produto`, `imposto_produto` |

Quantidades, preços, impostos e valores de linha precisam estar em tipos numéricos compatíveis com as operações do Spark. A data do pedido usa `dd-MM-yyyy`; as datas de notas fiscais são interpretadas por `to_date` sem formato explícito. Origem, volume, licença e tipos exatos dos dados da entrega não estão registrados.

## Como consultar e executar

Clone o repositório para examinar os arquivos:

```bash
git clone https://github.com/Luizrocha0/industria-automobilistida.git
cd industria-automobilistida
```

O processamento depende de Databricks, Spark e acesso ao catálogo `classes`. O comando `CREATE CATALOG` pressupõe [Unity Catalog](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-catalog) e permissões adequadas. A edição, a versão do runtime e o volume de dados da entrega original não estão documentados.

A sequência de dependências dos notebooks é:

1. Executar [create-objects.ipynb](industria/bronze/create-objects.ipynb) para preparar catálogo e schemas. O setup usa `IF NOT EXISTS` para permitir repetição.
2. Disponibilizar as duas tabelas Bronze no ambiente e consultar [inicializar.ipynb](industria/bronze/inicializar.ipynb). Esse notebook consulta dados já carregados.
3. Executar as transformações Silver de [notas fiscais](industria/silver/processing_data_nf.ipynb) e [pedidos](industria/silver/processing_data_pedidos.ipynb).
4. Executar as agregações Gold de [notas fiscais](industria/gold/processing_nf.ipynb) e [pedidos](industria/gold/processing_pedido.ipynb). As tabelas são gravadas antes das consultas de visualização.
5. Executar [visual.ipynb](industria/gold/visual.ipynb) depois que as cinco tabelas Gold estiverem disponíveis.

Os notebooks Silver e Gold dependem das etapas anteriores. As escritas usam sobrescrita das tabelas, sem implementação de carga incremental. A integração completa com Databricks e os CSV originais não foi executada nesta revisão.

## Verificação das correções

Os testes usam a biblioteca padrão de Python e doubles de catálogo/schema para verificar execução das células, dependências, partições e consultas depois da gravação. Eles não executam PySpark nem validam os valores das agregações.

```bash
python -m unittest discover -s tests -v
```

## Limitações restantes

- Os CSV, o procedimento de carga Bronze e a configuração do ambiente original não estão versionados.
- Não há contrato completo de tipos, precisão monetária ou tratamento de registros inválidos; preencher nulos com zero é uma regra que requer validação de negócio.
- Contagens e médias são por linha de documento, conforme as fórmulas descritas acima, e não métricas corrigidas por pedido ou nota distinta.
- As análises de pedidos incluem todos os status; a distribuição por status não acompanha transições de um funil.
- Não há orquestração nem integração automatizada com Databricks; visualizações exportadas são registros históricos, sem captura independente do painel ou benchmark.

Ganhos de performance e resultados financeiros não foram medidos nos materiais publicados.

## Perguntas de negócio propostas

As histórias originais motivam análises adicionais. A tabela distingue a cobertura presente de ideias para evolução.

| Pergunta | Cobertura nesta versão |
| --- | --- |
| Quais estados têm maior volume e menor valor médio? | Parcial: agregação por estado, sem critérios de seleção e com média por linha |
| Quais peças têm alto giro e baixo valor? | Parcial: quantidades e valores por produto, sem classificação ABC |
| Em qual etapa os pedidos são cancelados? | Apenas distribuição por status; não existe histórico de transições |
| Há sazonalidade nas vendas de SUVs? | Não há análise temporal por produto publicada |
| Quais vendedores precisam melhorar o ticket? | Parcial: ranking de notas por vendedor, sem regra de comparação por pedido |
| Quanto tempo decorre entre pedido e faturamento? | Não há cruzamento entre datas de pedidos e notas fiscais |
| Quais cidades do interior lideram as vendas? | Não há agregação por cidade ou classificação de capitais publicada |
| Cancelamentos se concentram em uma região ou produto? | Não há esse cruzamento publicado |
| Qual produto inicia a relação com novos clientes por estado? | Não há análise de primeira compra ou identificação de novos clientes |
| Quais rotas têm maior volume físico e menor margem? | Não há cálculo de peso, custo logístico ou margem publicado |

Cancelamento de pedidos, churn de clientes, faturamento e recebimento de caixa são conceitos diferentes. Os notebooks publicados não calculam churn, margem ou fluxo de caixa efetivo.

## Aprendizados e possibilidades de evolução

O trabalho exercita a separação entre dados de origem, tratamento e consumo analítico. A padronização de campos e as tabelas Gold tornam explícitas algumas regras e perguntas do domínio comercial.

Uma evolução técnica futura poderia priorizar o contrato dos dados de origem, as definições de métricas por documento e validações de qualidade com a base real. Automatizar a execução faz sentido depois dessas verificações.

## Equipe

| Integrante | Perfil informado na documentação original |
| --- | --- |
| Luan Guedes — líder | Perfil não informado |
| Eduardo Borges | [GitHub](https://github.com/Eduardo-Borges18) |
| Luiz Henrique | [GitHub](https://github.com/Luizrocha0) · [LinkedIn](https://www.linkedin.com/in/luiz-henrique-rocha-silva-450362337) |
| Ricardo Severiano de Souza Filho | [GitHub](https://github.com/byteric) |
| Sérgio Chousinho | [GitHub](https://github.com/sergiochou) |

## Licença

O repositório não contém arquivo de licença. A licença do código e as condições de uso dos dados ainda precisam ser definidas pelos responsáveis.
