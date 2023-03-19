# -*- coding:utf-8 -*-
# Desc:此policy-for.py方案废弃了。policy.py启用OK。
# 需求：一个保单险种，在不同投保年龄、性别、缴费期间的，应交的保费。
import decimal
import string
from decimal import Decimal
import pandas as pd
from pyspark.sql import SparkSession
from pyspark.sql import Window
from pyspark.sql.functions import pandas_udf, struct


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
                df.show()
        if sql.__contains__('步骤四'):
            return

if __name__ == '__main__':

    spark = SparkSession.builder.appName("spark_hive") \
        .master("local[*]") \
        .config("spark.sql.shuffle.partitions", 4) \
        .config("spark.sql.warehouse.dir", "hdfs://node1:8020/user/hive/warehouse") \
        .config("hive.metastore.uris", "thrift://node1:9083") \
        .enableHiveSupport() \
        .getOrCreate()

    sc = spark.sparkContext
    executeSQLFile('4_prem.sql')
    # 步骤四， 下面计算lx有效保单数
    # 计算保单年度=1的情况,同时设置为lx字段,注册成临时视图给下次用
    spark.sql("""
        select *,
               if(policy_year=1,1,null) as lx --有效保单数
        from prem_src3
    """).createOrReplaceTempView("prem_src4")
    print('查看并验证上面prem_src4内容')
    spark.sql("""
                select age_buy,sex,ppp, policy_year,lx from  prem_src4 where age_buy=18 and sex='M' and ppp=10 order by policy_year
             """).show(10, False)
    # 从第二年到保单年末，计算每年的lx。每年的数据集都注册成相同的表名，给下年使用。
    # 因为保单年度最大是88，而range(2, 89)就是2到88不包括89。
    for i in range(2, 89):
        df1 = spark.sql("""
                 select age_buy,sex,t_age,ppp,bpp,interest_rate,sa,policy_year,age,ppp_,bpp_,qx,kx,qx_ci,qx_d,
                        if(policy_year={i},
                          round(lag(lx*(1-qx)) over (partition by age_buy, ppp, sex order by policy_year) ,8),
                          lx) lx
                   from prem_src4
                """.format(i=i))
        # 临时视图名不变，新逻辑覆盖旧逻辑
        df1.createOrReplaceTempView('prem_src4')
    print("再次查看并验证prem_src4的内容")
    spark.sql(
        "select age_buy,sex,ppp, policy_year,lx from  prem_src4 where age_buy=18 and sex='M' and ppp=10 order by policy_year ") \
        .show(100, False)
    # lx有效保单数计算完毕，把上面的数据集缓存起来，因为前面的逻辑复杂。
    spark.sql("cache table cache_prem_src4 as select * from prem_src4")

    # 步骤五， 下面计算健康人数dx_d、dx_ci、lx_d字段
    # 计算保单年度=1的健康人数dx_d、dx_ci、lx_d字段,其他年度置为null后续再算，注册成临时视图给下次用
    spark.sql(""" 
            select * ,
                   if(policy_year=1,1,null) as lx_d, --健康人数
                   if(policy_year=1,qx_d,null) as dx_d,
                   if(policy_year=1,qx_ci,null) as dx_ci
              from cache_prem_src4
    """).createOrReplaceTempView('prem_src5')
    # 从第二年到保单年末，计算每年的dx_d、dx_ci、lx_d。每年的数据集都注册成相同的表名，给下年使用。
    for i in range(2, 89):
        df1 = spark.sql("""
                    select age_buy,sex,ppp,policy_year,qx_ci,qx_d,
                           lx_d,
                           --【了解】为什么用cast(。。 as decimal(18,16))
                           --decimal(18,16)意思是有效小数16位，整数部分2位。
                           --对于乘法lx_d*qx_d，这是16位小数与16位小数做乘法，结果值有32个小数位，会
                           --会造成policy_year=1时的lx_d值由1.00..变成null ，所以结果精度都统一保留比如成16位小数
                           --所以用了cast(lx_d*qx_d as decimal(18,16))
                           if(policy_year={i},cast(lx_d*qx_d  as decimal(18,16)),dx_d)  dx_d,
                           if(policy_year={i},cast(lx_d*qx_ci as decimal(18,16)),dx_ci) dx_ci
                     from (select age_buy,sex,ppp,policy_year,qx_ci,qx_d,dx_d,dx_ci,
                           if(policy_year={i},
                             --下面的减法计算后，会将视图中的lx_d字段的精度自动提升到lx_d、dx_d、dx_ci三者中的最大精度者。
                              lag(lx_d-dx_d-dx_ci) over (partition by age_buy, ppp, sex order by policy_year),
                              lx_d) lx_d
                    from prem_src5) t
                """.format(i=i))
        df1.createOrReplaceTempView('prem_src5')
    print("查看并验证prem_src5内容")
    spark.sql(
        "select * from  prem_src5 where age_buy=18 and sex='M' and ppp=10 order by policy_year ").show(100,
                                                                                                       False)
