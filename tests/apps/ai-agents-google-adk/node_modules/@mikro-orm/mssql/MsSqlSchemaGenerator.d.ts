import { type ClearDatabaseOptions, type DropSchemaOptions, type MikroORM, SchemaGenerator } from '@mikro-orm/knex';
export declare class MsSqlSchemaGenerator extends SchemaGenerator {
    static register(orm: MikroORM): void;
    clearDatabase(options?: ClearDatabaseOptions): Promise<void>;
    getDropSchemaSQL(options?: Omit<DropSchemaOptions, 'dropDb'>): Promise<string>;
}
