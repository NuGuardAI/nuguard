"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SqliteExceptionConverter = void 0;
const core_1 = require("@mikro-orm/core");
class SqliteExceptionConverter extends core_1.ExceptionConverter {
    /* istanbul ignore next */
    /**
     * @inheritDoc
     * @link http://www.sqlite.org/c3ref/c_abort.html
     * @link https://github.com/doctrine/dbal/blob/master/src/Driver/AbstractSQLiteDriver.php
     */
    convertException(exception) {
        if (exception.message.includes('database is locked')) {
            return new core_1.LockWaitTimeoutException(exception);
        }
        if (exception.message.includes('must be unique') ||
            exception.message.includes('is not unique') ||
            exception.message.includes('are not unique') ||
            exception.message.includes('UNIQUE constraint failed')) {
            return new core_1.UniqueConstraintViolationException(exception);
        }
        if (exception.message.includes('may not be NULL') || exception.message.includes('NOT NULL constraint failed')) {
            return new core_1.NotNullConstraintViolationException(exception);
        }
        if (exception.message.includes('CHECK constraint failed')) {
            return new core_1.CheckConstraintViolationException(exception);
        }
        if (exception.message.includes('no such table:')) {
            return new core_1.TableNotFoundException(exception);
        }
        if (exception.message.includes('already exists')) {
            return new core_1.TableExistsException(exception);
        }
        if (exception.message.includes('no such column:')) {
            return new core_1.InvalidFieldNameException(exception);
        }
        if (exception.message.includes('ambiguous column name')) {
            return new core_1.NonUniqueFieldNameException(exception);
        }
        if (exception.message.includes('syntax error')) {
            return new core_1.SyntaxErrorException(exception);
        }
        if (exception.message.includes('attempt to write a readonly database')) {
            return new core_1.ReadOnlyException(exception);
        }
        if (exception.message.includes('unable to open database file')) {
            return new core_1.ConnectionException(exception);
        }
        if (exception.message.includes('FOREIGN KEY constraint failed')) {
            return new core_1.ForeignKeyConstraintViolationException(exception);
        }
        return super.convertException(exception);
    }
}
exports.SqliteExceptionConverter = SqliteExceptionConverter;
