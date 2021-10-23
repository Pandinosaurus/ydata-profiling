"""Correlations between variables."""
from typing import Optional

import pandas as pd
import phik
from pyspark.ml.feature import VectorAssembler
from pyspark.ml.stat import Correlation
from pyspark.sql import DataFrame
from pyspark.sql.functions import PandasUDFType, lit, pandas_udf
from pyspark.sql.types import DoubleType, StructField, StructType

from pandas_profiling.config import Settings
from pandas_profiling.model.correlations import (
    Cramers,
    Kendall,
    Pearson,
    PhiK,
    Spearman,
)


@Spearman.compute.register(Settings, DataFrame, dict)
def spark_spearman_compute(
    config: Settings, df: DataFrame, summary: dict
) -> Optional[DataFrame]:
    variables = {column: description["type"] for column, description in summary.items()}
    interval_columns = [
        column for column, type_name in variables.items() if type_name == "Numeric"
    ]
    df = df.select(*interval_columns)

    # convert to vector column first
    vector_col = "corr_features"

    assembler = VectorAssembler(
        inputCols=df.columns, outputCol=vector_col, handleInvalid="skip"
    )
    df_vector = assembler.transform(df).select(vector_col)

    # get correlation matrix
    matrix = (
        Correlation.corr(df_vector, vector_col, method="spearman").head()[0].toArray()
    )
    return pd.DataFrame(matrix, index=df.columns, columns=df.columns)


@Pearson.compute.register(Settings, DataFrame, dict)
def spark_pearson_compute(
    config: Settings, df: DataFrame, summary: dict
) -> Optional[DataFrame]:
    # convert to vector column first
    variables = {column: description["type"] for column, description in summary.items()}
    interval_columns = [
        column for column, type_name in variables.items() if type_name == "Numeric"
    ]
    df = df.select(*interval_columns)

    vector_col = "corr_features"
    assembler = VectorAssembler(
        inputCols=df.columns, outputCol=vector_col, handleInvalid="skip"
    )
    df_vector = assembler.transform(df).select(vector_col)

    # get correlation matrix
    matrix = (
        Correlation.corr(df_vector, vector_col, method="pearson").head()[0].toArray()
    )
    return pd.DataFrame(matrix, index=df.columns, columns=df.columns)


@Kendall.compute.register(Settings, DataFrame, dict)
def spark_kendall_compute(
    config: Settings, df: DataFrame, summary: dict
) -> Optional[DataFrame]:
    raise NotImplementedError()


@Cramers.compute.register(Settings, DataFrame, dict)
def spark_cramers_compute(
    config: Settings, df: DataFrame, summary: dict
) -> Optional[DataFrame]:
    raise NotImplementedError()


@PhiK.compute.register(Settings, DataFrame, dict)
def spark_phi_k_compute(
    config: Settings, df: DataFrame, summary: dict
) -> Optional[DataFrame]:

    threshold = config.categorical_maximum_correlation_distinct
    intcols = {
        key
        for key, value in summary.items()
        # DateTime currently excluded
        # In some use cases, it makes sense to convert it to interval
        # See https://github.com/KaveIO/PhiK/issues/7
        if value["type"] == "Numeric" and 1 < value["n_distinct"]
    }

    supportedcols = {
        key
        for key, value in summary.items()
        if value["type"] != "Unsupported" and 1 < value["n_distinct"] <= threshold
    }
    selcols = list(supportedcols.union(intcols))

    if len(selcols) <= 1:
        return None

    # pandas mapped udf works only with a groupby, we force the groupby to operate on all columns at once
    # by giving one value to all columns
    groupby_df = df.select(selcols).withColumn("groupby", lit(1))

    # generate output schema for pandas_udf
    output_schema_components = []
    for column in selcols:
        output_schema_components.append(StructField(column, DoubleType(), True))
    output_schema = StructType(output_schema_components)

    # create the pandas grouped map function to do vectorized kendall within spark itself
    @pandas_udf(output_schema, PandasUDFType.GROUPED_MAP)
    def spark_phik(pdf):
        correlation = phik.phik_matrix(df=pdf, interval_cols=list(intcols))
        return correlation

    # return the appropriate dataframe (similar to pandas_df.corr results)
    if len(groupby_df.head(1)) > 0:
        # perform correlation in spark, and get the results back in pandas
        df = pd.DataFrame(
            groupby_df.groupby("groupby").apply(spark_phik).toPandas().values,
            columns=selcols,
            index=selcols,
        )
    else:
        df = pd.DataFrame()

    return df
