-- Esquema inicial para Privacy Pioneer
-- Se ejecuta automáticamente cuando el contenedor MySQL arranca por primera vez.

CREATE DATABASE IF NOT EXISTS `analysis`;
USE `analysis`;

CREATE TABLE IF NOT EXISTS `entries` (
  `id`             INTEGER PRIMARY KEY AUTO_INCREMENT,
  `timestp`        VARCHAR(255),
  `permission`     VARCHAR(255),
  `rootUrl`        VARCHAR(255),
  `snippet`        VARCHAR(4000),
  `requestUrl`     MEDIUMTEXT,
  `typ`            VARCHAR(255),
  `ind`            VARCHAR(255),
  `firstPartyRoot` VARCHAR(255),
  `parentCompany`  VARCHAR(255),
  `watchlistHash`  VARCHAR(255),
  `extraDetail`    VARCHAR(255),
  `cookie`         VARCHAR(255),
  `loc`            VARCHAR(255),
  `consent_phase`  VARCHAR(10) DEFAULT 'PRE'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `allev` (
  `id`      INTEGER PRIMARY KEY AUTO_INCREMENT,
  `db`      VARCHAR(255),
  `table_n` VARCHAR(255),
  `host`    MEDIUMTEXT,
  `request` MEDIUMTEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
