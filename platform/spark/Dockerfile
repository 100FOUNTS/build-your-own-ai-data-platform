FROM apache/spark:4.0.4-scala2.13-java21-python3-ubuntu
USER root
ARG M=https://repo1.maven.org/maven2
RUN cd /opt/spark/jars && \
    curl -fsSLO $M/org/apache/iceberg/iceberg-spark-runtime-4.0_2.13/1.10.1/iceberg-spark-runtime-4.0_2.13-1.10.1.jar && \
    curl -fsSLO $M/org/apache/iceberg/iceberg-aws-bundle/1.10.1/iceberg-aws-bundle-1.10.1.jar && \
    curl -fsSLO $M/org/apache/spark/spark-sql-kafka-0-10_2.13/4.0.4/spark-sql-kafka-0-10_2.13-4.0.4.jar && \
    curl -fsSLO $M/org/apache/spark/spark-token-provider-kafka-0-10_2.13/4.0.4/spark-token-provider-kafka-0-10_2.13-4.0.4.jar && \
    curl -fsSLO $M/org/apache/kafka/kafka-clients/3.9.1/kafka-clients-3.9.1.jar && \
    curl -fsSLO $M/org/apache/commons/commons-pool2/2.12.0/commons-pool2-2.12.0.jar && \
    curl -fsSLO $M/org/apache/hadoop/hadoop-aws/3.4.1/hadoop-aws-3.4.1.jar && \
    curl -fsSLO $M/software/amazon/awssdk/bundle/2.24.6/bundle-2.24.6.jar
USER spark
COPY spark-defaults.conf /opt/spark/conf/spark-defaults.conf
WORKDIR /opt/spark/work-dir
ENTRYPOINT ["/opt/spark/bin/spark-submit"]
