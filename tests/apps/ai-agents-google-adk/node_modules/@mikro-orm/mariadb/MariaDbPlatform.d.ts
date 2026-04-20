import { MySqlPlatform, type TransformContext } from '@mikro-orm/knex';
import { MariaDbSchemaHelper } from './MariaDbSchemaHelper';
import { MariaDbExceptionConverter } from './MariaDbExceptionConverter';
export declare class MariaDbPlatform extends MySqlPlatform {
    protected readonly schemaHelper: MariaDbSchemaHelper;
    protected readonly exceptionConverter: MariaDbExceptionConverter;
    getDefaultCharset(): string;
    convertJsonToDatabaseValue(value: unknown, context?: TransformContext): unknown;
}
