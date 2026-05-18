import { ExceptionConverter, type Dictionary, type DriverException } from '@mikro-orm/core';
export declare class MsSqlExceptionConverter extends ExceptionConverter {
    /**
     * @link https://docs.microsoft.com/en-us/sql/relational-databases/errors-events/mssqlserver-511-database-engine-error?view=sql-server-ver15
     * @link https://github.com/doctrine/dbal/blob/master/src/Driver/AbstractPostgreSQLDriver.php
     */
    convertException(exception: Error & Dictionary): DriverException;
}
