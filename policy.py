# -*- coding:utf-8 -*-
# Desc:需求：计算一个保单险种，在不同投保年龄、性别、缴费期间的，应交的保费。
import decimal
import os
import string
from decimal import Decimal
import pandas as pd
from pyspark.sql import SparkSession
from pyspark.sql.functions import pandas_udf

# 锁定远端环境, 避免出现问题
os.environ['SPARK_HOME'] = '/export/server/spark'
os.environ["PYSPARK_PYTHON"] = "/root/anaconda3/bin/python"
os.environ["PYSPARK_DRIVER_PYTHON"] = "/root/anaconda3/bin/python"


# 定义一个公共方法执行解析文本的SQL语句，从而让SQL与python代码解耦分离
def executeSQLFile(filename):
    with open(r'../resources/' + filename, 'r') as f:
        read_data = f.readlines()
    # 将数组的一行一行拼接成一个长文本，就是SQL文件的内容
    read_data = ''.join(read_data)
    # 将文本内容按分号切割得到数组，每个元素预计是一个完整语句
    arr = read_data.split(";")
    # 对每个SQL,如果是空字符串或空文本，则剔除掉
    # 注意，你可能认为空字符串''也算是空白字符，但其实空字符串‘’不是空白字符 ，即''.isspace()返回的是False
    arr2 = list(filter(lambda x: not x.isspace() and not x == "", arr))
    # 对每个SQL语句进行迭代
    for sql in arr2:
        # 先打印完整的SQL语句。
        print(sql, ";")
        # 由于SQL语句不一定有意义，比如全是--注释;，他也以分号结束，但是没有意义不用执行。
        # 对每个SQL语句，他由多行组成，sql.splitlines()数组中是每行，挑选出不是空白字符的，也不是空字符串''的，也不是--注释的。
        # 即保留有效的语句。
        filtered = filter(lambda x: (not x.lstrip().startswith("--")) and (not x.isspace()) and (not x.strip() == ''),
                          sql.splitlines())
        # 下面数组的元素是SQL语句有效的行
        filtered = list(filtered)

        # 有效的行数>0，才执行
        if len(filtered) > 0:
            df = spark.sql(sql)
            # 如果有效的SQL语句是select开头的，则打印数据。
            if filtered[0].lstrip().startswith("select"):
                df.show(100)


def register_udf():
    # 步骤四.2，计算从第二年到保单年末，每年的lx。
    @pandas_udf("decimal(38,16)")
    def udf_lx(qx: pd.Series, lx: pd.Series) -> decimal:
        decimal.getcontext().rounding = "ROUND_HALF_UP"
        temp_qx = Decimal(0)
        temp_lx = Decimal(0)
        for i in range(len(qx)):
            if i == 0:
                temp_qx = Decimal(qx[0])
                temp_lx = Decimal(lx[0])
            else:
                temp_lx = (temp_lx * (1 - temp_qx)).quantize(Decimal('0.0000000000000000'))
                temp_qx = (qx[i])
        return temp_lx
    # 注册成udf函数(其实是聚合的udaf函数)，方便在4_prem.sql文件中使用
    spark.udf.register('udf_lx', udf_lx)

    # 步骤五.2，计算从第二年到保单年末，每年的健康人数dx_d、dx_ci、lx_d字段。
    @pandas_udf("string")
    def udf_lxd_dxd_dxci(lx_d: pd.Series, qx_d: pd.Series, qx_ci: pd.Series) -> string:
        decimal.getcontext().rounding = "ROUND_HALF_UP"
        temp_lx_d = Decimal(0)
        temp_dx_d = Decimal(0)
        temp_dx_ci = Decimal(0)

        for i in range(len(lx_d)):
            if i == 0:
                temp_lx_d = Decimal(lx_d[0])
                temp_dx_d = Decimal(qx_d[0])
                temp_dx_ci = Decimal(qx_ci[0])
            else:
                this_lx_d = (temp_lx_d - temp_dx_d - temp_dx_ci).quantize(Decimal('0.0000000000000000'))
                temp_lx_d = this_lx_d
                temp_dx_d = (this_lx_d * qx_d[i]).quantize(Decimal('0.0000000000000000'))
                temp_dx_ci = (this_lx_d * qx_ci[i]).quantize(Decimal('0.0000000000000000'))
        return str(temp_lx_d) + '_' + str(temp_dx_d) + '_' + str(temp_dx_ci)

    # 注册成udf函数(其实是聚合的udaf函数)，方便在4_prem.sql文件中使用
    spark.udf.register('udf_lxd_dxd_dxci', udf_lxd_dxd_dxci)

if __name__ == '__main__':
    spark = SparkSession.builder.appName("spark_hive") \
        .master("local[*]") \
        .config("spark.sql.shuffle.partitions", 4) \
        .config("spark.sql.warehouse.dir", "hdfs://node1:8020/user/hive/warehouse") \
        .config("hive.metastore.uris", "thrift://node1:9083") \
        .enableHiveSupport() \
        .getOrCreate()

    sc = spark.sparkContext
    # 注册2个udf函数，方便在4_prem.sql文件中使用
    register_udf()
    # 计算保费因子和期交保费
    executeSQLFile('4_prem.sql')
    # 计算现金价值
    executeSQLFile('5_cv.sql')
    # 计算准备金
    executeSQLFile('6_rsv_src.sql')
    # 计算统计指标
    executeSQLFile('7_app.sql')
