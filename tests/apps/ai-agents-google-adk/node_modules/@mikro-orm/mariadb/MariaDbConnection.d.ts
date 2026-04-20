import { MySqlConnection, type Knex } from '@mikro-orm/knex';
export declare class MariaDbConnection extends MySqlConnection {
    createKnex(): void;
    getConnectionOptions(): Knex.MySqlConnectionConfig;
}
