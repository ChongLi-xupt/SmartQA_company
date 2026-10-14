-- 关键：强制客户端连接用 utf8mb4。MySQL 镜像执行 init.sql 时默认
-- 客户端字符集是 latin1，不加这一句本文件的中文会被双重编码成乱码。
SET NAMES utf8mb4;

-- =====================================================
-- 光伏发电业务库（Docker MySQL 容器版）
-- 库名 pv_analysis，只读账号 qa_ro@'%'（容器外部连接来源非 localhost）
-- 由 MySQL 容器首次启动时自动执行（挂载到 /docker-entrypoint-initdb.d/）
-- =====================================================
CREATE DATABASE IF NOT EXISTS pv_analysis
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE pv_analysis;

CREATE TABLE plant_info (
    plant_id            INT            NOT NULL AUTO_INCREMENT,
    plant_name          VARCHAR(100)   NOT NULL COMMENT '电站名称',
    region              VARCHAR(50)    NOT NULL COMMENT '所在地区（省/市）',
    installed_capacity  DECIMAL(10,2)  NOT NULL COMMENT '装机容量（MW，兆瓦）',
    grid_connected_date DATE           NOT NULL COMMENT '并网日期',
    owner               VARCHAR(100)   COMMENT '业主单位',
    PRIMARY KEY (plant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='光伏电站信息表';

CREATE TABLE inverter_info (
    inverter_id     INT           NOT NULL AUTO_INCREMENT,
    plant_id        INT           NOT NULL COMMENT '所属电站ID',
    inverter_name   VARCHAR(100)  NOT NULL COMMENT '逆变器名称/编号',
    rated_power     DECIMAL(10,2) NOT NULL COMMENT '额定功率（kW）',
    manufacturer    VARCHAR(100)  COMMENT '制造商',
    PRIMARY KEY (inverter_id),
    KEY idx_inv_plant (plant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='逆变器设备信息表';

CREATE TABLE daily_generation (
    id                BIGINT        NOT NULL AUTO_INCREMENT,
    plant_id          INT           NOT NULL COMMENT '电站ID',
    stat_date         DATE          NOT NULL COMMENT '统计日期',
    generation_kwh    DECIMAL(14,2) NOT NULL COMMENT '当日发电量（kWh，千瓦时）',
    on_grid_kwh       DECIMAL(14,2) COMMENT '当日上网电量（kWh）',
    peak_power_kw     DECIMAL(10,2) COMMENT '当日峰值功率（kW）',
    utilization_hours DECIMAL(8,2)  COMMENT '等效利用小时数（h）',
    irradiation       DECIMAL(10,2) COMMENT '水平面日总辐照量（kWh/㎡）',
    avg_temperature   DECIMAL(6,2)  COMMENT '日平均气温（℃）',
    PRIMARY KEY (id),
    KEY idx_daily_plant_date (plant_id, stat_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='光伏电站日发电量记录表';

CREATE TABLE monthly_generation (
    id                BIGINT        NOT NULL AUTO_INCREMENT,
    plant_id          INT           NOT NULL COMMENT '电站ID',
    stat_month        CHAR(7)       NOT NULL COMMENT '统计月份（YYYY-MM）',
    generation_kwh    DECIMAL(14,2) NOT NULL COMMENT '当月发电量（kWh）',
    on_grid_kwh       DECIMAL(14,2) COMMENT '当月上网电量（kWh）',
    utilization_hours DECIMAL(10,2) COMMENT '月等效利用小时数（h）',
    PRIMARY KEY (id),
    KEY idx_monthly_plant_month (plant_id, stat_month)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='光伏电站月发电量汇总表';

INSERT INTO plant_info (plant_id, plant_name, region, installed_capacity, grid_connected_date, owner) VALUES
(1, '榆林光伏电站', '陕西·榆林', 100.00, '2020-06-30', '陕能新能源'),
(2, '敦煌光伏电站', '甘肃·敦煌',  50.00, '2021-03-15', '甘电投新能源'),
(3, '银川光伏电站', '宁夏·银川',  80.00, '2019-12-20', '宁能新能源');

INSERT INTO inverter_info (inverter_id, plant_id, inverter_name, rated_power, manufacturer) VALUES
(101, 1, 'YL-INV-01', 500.00, '华为'),
(102, 1, 'YL-INV-02', 500.00, '华为'),
(103, 2, 'DH-INV-01', 300.00, '阳光电源'),
(104, 3, 'YC-INV-01', 400.00, '锦浪'),
(105, 3, 'YC-INV-02', 400.00, '锦浪');

INSERT INTO daily_generation
  (plant_id, stat_date, generation_kwh, on_grid_kwh, peak_power_kw, utilization_hours, irradiation, avg_temperature) VALUES
(1, '2026-08-28', 482000.00, 470500.00, 62000.00, 4.82, 5.60, 26.5),
(1, '2026-08-29', 495000.00, 483000.00, 64800.00, 4.95, 5.78, 27.1),
(1, '2026-08-30', 468000.00, 456000.00, 61000.00, 4.68, 5.42, 25.8),
(2, '2026-08-28', 236000.00, 229000.00, 31200.00, 4.72, 5.55, 24.9),
(2, '2026-08-29', 248000.00, 241000.00, 32500.00, 4.96, 5.80, 25.3),
(2, '2026-08-30', 231000.00, 224000.00, 30500.00, 4.62, 5.41, 24.2),
(3, '2026-08-28', 389000.00, 378000.00, 49800.00, 4.86, 5.66, 24.6),
(3, '2026-08-29', 401000.00, 390000.00, 51600.00, 5.01, 5.85, 25.0),
(3, '2026-08-30', 377000.00, 366000.00, 48000.00, 4.71, 5.49, 24.1);

INSERT INTO monthly_generation
  (plant_id, stat_month, generation_kwh, on_grid_kwh, utilization_hours) VALUES
(1, '2026-06', 12600000.00, 12280000.00, 126.0),
(1, '2026-07', 13800000.00, 13450000.00, 138.0),
(1, '2026-08', 14480000.00, 14120000.00, 144.8),
(2, '2026-06',  6400000.00,  6230000.00, 128.0),
(2, '2026-07',  7050000.00,  6870000.00, 141.0),
(2, '2026-08',  7180000.00,  6995000.00, 143.6),
(3, '2026-06', 10100000.00,  9840000.00, 126.3),
(3, '2026-07', 11050000.00, 10760000.00, 138.1),
(3, '2026-08', 11620000.00, 11320000.00, 145.3);

-- 只读账号：容器化 MySQL 外部连接来源是 Docker 网关 IP，必须用 '%' 而非 'localhost'
CREATE USER 'qa_ro'@'%' IDENTIFIED BY 'QaRo@Pv2026';
GRANT SELECT ON pv_analysis.* TO 'qa_ro'@'%';
FLUSH PRIVILEGES;