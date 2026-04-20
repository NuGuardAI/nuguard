class SqlString {
    constructor() {
        this.ID_GLOBAL_REGEXP = /\]/g;
        this.QUAL_GLOBAL_REGEXP = /\./g;
        this.CHARS_GLOBAL_REGEXP = /[\0\b\f\t\n\r\v\x1a\'\\]/g; // eslint-disable-line no-control-regex
        this.CHARS_ESCAPE_MAP = {
            "\0"   : '\\0',
            "\b"   : '\\b',
            "\f"   : '\\f',
            "\t"   : '\\t',
            "\n"   : '\\n',
            "\r"   : '\\r',
            "\v"   : '\\v',
            "\x1a" : '\\Z',
            "'"    : "''",
            '\\'   : '\\\\',
        };
    }


    // -------------------- "PRIVATE" Methods ---------------------------------------//


    _escapeString(val) {
        let chunkIndex = this.CHARS_GLOBAL_REGEXP.lastIndex = 0;
        let escapedVal = '';
        let match;

        while ((match = this.CHARS_GLOBAL_REGEXP.exec(val))) {
            escapedVal += val.slice(chunkIndex, match.index) + this.CHARS_ESCAPE_MAP[match[0]];
            chunkIndex = this.CHARS_GLOBAL_REGEXP.lastIndex;
        }

        // convert control characters

        if (chunkIndex === 0) return `'${val}'`; // Nothing was escaped
        if (chunkIndex < val.length) return `'${escapedVal}${val.slice(chunkIndex)}'`;
        return `'${escapedVal}'`;
    }

    _zeroPad(number, length) {
        return number.toString().padStart(length, '0');
    }

    _convertTimezone(tz) {
        if (tz === 'Z') return 0;

        const m = tz.match(/([\+\-\s])(\d\d):?(\d\d)?/);
        if (m) return (m[1] === '-' ? -1 : 1) * (parseInt(m[2], 10) + ((m[3] ? parseInt(m[3], 10) : 0) / 60)) * 60;
        return false;
    }


    // -------------------- PUBLIC Methods ---------------------------------------//


    escapeId(val, forbidQualified) {
        if (Array.isArray(val)) return val.map(v => this.escapeId(v, forbidQualified)).join(', ');
        if (forbidQualified) return `[${String(val).replace(this.ID_GLOBAL_REGEXP, ']]')}]`;
        return `[${String(val).replace(this.ID_GLOBAL_REGEXP, ']]').replace(this.QUAL_GLOBAL_REGEXP, '].[')}]`;
    }

    escape(val, stringifyObjects, timeZone) {
        if (val === undefined || val === null) return 'NULL';

        switch (typeof val) {
            case 'boolean': return (val ? '1' : '0');
            case 'number': return val + '';
            case 'object':
                if (val instanceof Date) return this.dateToString(val, timeZone || 'local');
                if (Array.isArray(val)) return this.arrayToList(val, timeZone);
                if (Buffer.isBuffer(val)) return this.bufferToString(val);
                if ('toSqlString' in val && typeof val.toSqlString === 'function') return String(val.toSqlString());
                if (stringifyObjects) return this._escapeString(val.toString());
                return this.objectToValues(val, timeZone);
            default:
                return this._escapeString(val);
        }
    }

    arrayToList(array, timeZone) {
        return array.map(val => {
            if (Array.isArray(val)) return `(${this.arrayToList(val, timeZone)})`;
            return this.escape(val, true, timeZone);
        }).join(', ');
    }

    format(sql, values, stringifyObjects, timeZone) {
        if (values == null) return sql;

        if (!(values instanceof Array || Array.isArray(values))) {
            values = [values];
        }

        const placeholdersRegex = /\?+/g;
        let chunkIndex = 0;
        let result = [];
        let valuesIndex = 0;
        let match;

        while (valuesIndex < values.length && (match = placeholdersRegex.exec(sql))) {
            const len = match[0].length;

            if (len > 2) continue;

            const value = (len === 2 ? this.escapeId(values[valuesIndex]) : this.escape(values[valuesIndex], stringifyObjects, timeZone));

            result.push(sql.slice(chunkIndex, match.index) + value);
            chunkIndex = placeholdersRegex.lastIndex;
            valuesIndex++;
        }

        if (chunkIndex === 0) return sql; // Nothing was replaced
        if (chunkIndex < sql.length) return result.join('') + sql.slice(chunkIndex);
        return result.join('');
    }

    dateToString(date, timeZone) {
        const dt = new Date(date);
        if (isNaN(dt.getTime())) return 'NULL';

        let year;
        let month;
        let day;
        let hour;
        let minute;
        let second;
        let millisecond;

        if (timeZone === 'local') {
            year = dt.getFullYear();
            month = dt.getMonth() + 1;
            day = dt.getDate();
            hour = dt.getHours();
            minute = dt.getMinutes();
            second = dt.getSeconds();
            millisecond = dt.getMilliseconds();
        } else {
            const tz = this._convertTimezone(timeZone);

            if (tz !== false && tz !== 0) {
                dt.setTime(dt.getTime() + (tz * 60000));
            }

            year = dt.getUTCFullYear();
            month = dt.getUTCMonth() + 1;
            day = dt.getUTCDate();
            hour = dt.getUTCHours();
            minute = dt.getUTCMinutes();
            second = dt.getUTCSeconds();
            millisecond = dt.getUTCMilliseconds();
        }

        // YYYY-MM-DD HH:mm:ss.mmm
        const str = `${this._zeroPad(year, 4)}-${this._zeroPad(month, 2)}-${this._zeroPad(day, 2)} ${this._zeroPad(hour, 2)}:${this._zeroPad(minute, 2)}:${this._zeroPad(second, 2)}.${this._zeroPad(millisecond, 3)}`;
        return this._escapeString(str);
    }

    bufferToString(buffer) {
        return `X${this._escapeString(buffer.toString('hex'))}`;
    }

    objectToValues(object, timeZone) {
        const sql = [];

        for (let key in object) {
            const val = object[key];
            if (typeof val === 'function') continue;
            sql.push(`${this.escapeId(key)} = ${this.escape(val, true, timeZone)}`);
        }

        return sql.join(', ');
    }

    raw(sql) {
        if (typeof sql !== 'string') throw new TypeError('argument sql must be a string');

        return {
            toSqlString: function toSqlString() {
                return sql;
            }
        };
    }
}

module.exports = new SqlString();
