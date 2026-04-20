import { BaseSqliteConnection } from '@mikro-orm/knex';
export declare class SqliteConnection extends BaseSqliteConnection {
    createKnex(): void;
    protected transformRawResult<T>(res: any, method: 'all' | 'get' | 'run'): T;
}
