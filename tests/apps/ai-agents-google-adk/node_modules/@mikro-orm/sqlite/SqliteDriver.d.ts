import type { Configuration } from '@mikro-orm/core';
import { AbstractSqlDriver } from '@mikro-orm/knex';
import { SqliteConnection } from './SqliteConnection';
export declare class SqliteDriver extends AbstractSqlDriver<SqliteConnection> {
    constructor(config: Configuration);
}
