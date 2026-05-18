import { BaseSqlitePlatform } from '@mikro-orm/knex';
import { SqliteSchemaHelper } from './SqliteSchemaHelper';
import { SqliteExceptionConverter } from './SqliteExceptionConverter';
export declare class SqlitePlatform extends BaseSqlitePlatform {
    protected readonly schemaHelper: SqliteSchemaHelper;
    protected readonly exceptionConverter: SqliteExceptionConverter;
    escape(value: any): string;
}
