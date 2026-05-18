"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.MsSqlExceptionConverter = void 0;
const core_1 = require("@mikro-orm/core");
class MsSqlExceptionConverter extends core_1.ExceptionConverter {
    /* istanbul ignore next */
    /**
     * @link https://docs.microsoft.com/en-us/sql/relational-databases/errors-events/mssqlserver-511-database-engine-error?view=sql-server-ver15
     * @link https://github.com/doctrine/dbal/blob/master/src/Driver/AbstractPostgreSQLDriver.php
     */
    convertException(exception) {
        let errno = exception.number;
        if ('errors' in exception && Array.isArray(exception.errors) && typeof exception.errors[0] === 'object' && 'message' in exception.errors[0]) {
            exception.message += '\n' + exception.errors.map(e => e.message).join('\n');
            errno ??= exception.errors[0].number;
            exception.lineNumber ??= exception.errors[0].lineNumber;
        }
        switch (errno) {
            case 515:
                return new core_1.NotNullConstraintViolationException(exception);
            case 102:
                return new core_1.SyntaxErrorException(exception);
            case 207:
                return new core_1.InvalidFieldNameException(exception);
            case 208:
                return new core_1.TableNotFoundException(exception);
            case 209:
                return new core_1.NonUniqueFieldNameException(exception);
            case 2601:
                return new core_1.UniqueConstraintViolationException(exception);
            case 2714:
                return new core_1.TableExistsException(exception);
        }
        return super.convertException(exception);
    }
}
exports.MsSqlExceptionConverter = MsSqlExceptionConverter;
