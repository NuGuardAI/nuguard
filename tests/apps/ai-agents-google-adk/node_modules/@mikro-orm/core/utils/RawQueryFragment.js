"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.ALIAS_REPLACEMENT_RE = exports.ALIAS_REPLACEMENT = exports.RawQueryFragment = void 0;
exports.raw = raw;
exports.sql = sql;
exports.createSqlFunction = createSqlFunction;
exports.quote = quote;
const node_async_hooks_1 = require("node:async_hooks");
const node_util_1 = require("node:util");
const Utils_1 = require("./Utils");
const rawSymbol = Symbol('RawQueryFragment');
class RawQueryFragment {
    sql;
    params;
    static #rawQueryCache = new Map();
    static #storage = new node_async_hooks_1.AsyncLocalStorage();
    static #index = 0n;
    static cloneRegistry;
    #assigned = false;
    #used = 0;
    #key;
    constructor(sql, params = []) {
        this.sql = sql;
        this.params = params;
        this.#key = `[raw]: ${this.sql} (#${RawQueryFragment.#index++})`;
        Object.defineProperty(this, rawSymbol, { value: true, enumerable: false });
    }
    as(alias) {
        // TODO: to be removed in v7
        /* istanbul ignore next */
        if (alias.startsWith('`') || alias.startsWith('"')) {
            return new RawQueryFragment(`${this.sql} as ${alias}`, this.params);
        }
        return new RawQueryFragment(`${this.sql} as ??`, [...this.params, alias]);
    }
    valueOf() {
        throw new Error(`Trying to modify raw SQL fragment: '${this.sql}'`);
    }
    toJSON() {
        return this.#key;
    }
    toString() {
        RawQueryFragment.#rawQueryCache.set(this.#key, this);
        this.#used++;
        return this.#key;
    }
    /** @internal */
    assign() {
        if (this.#assigned) {
            throw new Error(`Cannot reassign already used RawQueryFragment: '${this.sql}'`);
        }
        this.#assigned = true;
    }
    clone() {
        RawQueryFragment.cloneRegistry?.add(this.#key);
        return new RawQueryFragment(this.sql, this.params);
    }
    static async run(cb) {
        const removeStack = new Set();
        const res = await this.#storage.run(removeStack, cb);
        removeStack.forEach(key => RawQueryFragment.remove(key));
        removeStack.clear();
        return res;
    }
    /**
     * @internal allows testing we don't leak memory, as the raw fragments cache needs to be cleared automatically
     */
    static checkCacheSize() {
        return this.#rawQueryCache.size;
    }
    /** @internal */
    static isRaw(value) {
        return typeof value === 'object' && !!value && Object.hasOwn(value, rawSymbol);
    }
    /** @internal */
    static markRaw(obj) {
        Object.defineProperty(obj, rawSymbol, { value: true, enumerable: false });
    }
    static isKnownFragment(key) {
        if (key instanceof RawQueryFragment) {
            return true;
        }
        return this.#rawQueryCache.has(key);
    }
    static getKnownFragment(key, cleanup = true) {
        if (key instanceof RawQueryFragment) {
            return key;
        }
        const raw = this.#rawQueryCache.get(key);
        if (raw && cleanup) {
            this.remove(key);
        }
        return raw;
    }
    static remove(key) {
        const raw = this.#rawQueryCache.get(key);
        if (!raw) {
            return;
        }
        raw.#used--;
        if (raw.#used <= 0) {
            const removeStack = this.#storage.getStore();
            if (removeStack) {
                removeStack.add(key);
            }
            else {
                this.#rawQueryCache.delete(key);
            }
        }
    }
    /* istanbul ignore next */
    /** @ignore */
    [node_util_1.inspect.custom]() {
        if (this.params) {
            return { sql: this.sql, params: this.params };
        }
        return { sql: this.sql };
    }
}
exports.RawQueryFragment = RawQueryFragment;
/** @internal */
exports.ALIAS_REPLACEMENT = '[::alias::]';
/** @internal */
exports.ALIAS_REPLACEMENT_RE = '\\[::alias::\\]';
/**
 * Creates raw SQL query fragment that can be assigned to a property or part of a filter. This fragment is represented
 * by `RawQueryFragment` class instance that can be serialized to a string, so it can be used both as an object value
 * and key. When serialized, the fragment key gets cached and only such cached key will be recognized by the ORM.
 * This adds a runtime safety to the raw query fragments.
 *
 * > **`raw()` helper is required since v6 to use a raw fragment in your query, both through EntityManager and QueryBuilder.**
 *
 * ```ts
 * // as a value
 * await em.find(User, { time: raw('now()') });
 *
 * // as a key
 * await em.find(User, { [raw('lower(name)')]: name.toLowerCase() });
 *
 * // value can be empty array
 * await em.find(User, { [raw('(select 1 = 1)')]: [] });
 * ```
 *
 * The `raw` helper supports several signatures, you can pass in a callback that receives the current property alias:
 *
 * ```ts
 * await em.find(User, { [raw(alias => `lower(${alias}.name)`)]: name.toLowerCase() });
 * ```
 *
 * You can also use the `sql` tagged template function, which works the same, but supports only the simple string signature:
 *
 * ```ts
 * await em.find(User, { [sql`lower(name)`]: name.toLowerCase() });
 * ```
 *
 * When using inside filters, you might have to use a callback signature to create new raw instance for every filter usage.
 *
 * ```ts
 * @Filter({ name: 'long', cond: () => ({ [raw('length(perex)')]: { $gt: 10000 } }) })
 * ```
 *
 * The `raw` helper can be used within indexes and uniques to write database-agnostic SQL expressions. In that case, you can use `'??'` to tag your database identifiers (table name, column names, index name, ...) inside your expression, and pass those identifiers as a second parameter to the `raw` helper. Internally, those will automatically be quoted according to the database in use:
 *
 * ```ts
 * // On postgres, will produce: create index "index custom_idx_on_name" on "library.author" ("country")
 * // On mysql, will produce: create index `index custom_idx_on_name` on `library.author` (`country`)
 * @Index({ name: 'custom_idx_on_name', expression: (table, columns) => raw(`create index ?? on ?? (??)`, ['custom_idx_on_name', table, columns.name]) })
 * @Entity({ schema: 'library' })
 * export class Author { ... }
 * ```
 *
 * You can also use the `quote` tag function to write database-agnostic SQL expressions. The end-result is the same as using the `raw` function regarding database identifiers quoting, only to have a more elegant expression syntax:
 *
 * ```ts
 * @Index({ name: 'custom_idx_on_name', expression: (table, columns) => quote`create index ${'custom_idx_on_name'} on ${table} (${columns.name})` })
 * @Entity({ schema: 'library' })
 * export class Author { ... }
 * ```
 */
function raw(sql, params) {
    if (sql instanceof RawQueryFragment) {
        return sql;
    }
    if (sql instanceof Function) {
        sql = sql(exports.ALIAS_REPLACEMENT);
    }
    if (sql === '??' && Array.isArray(params)) {
        return new RawQueryFragment(sql, params);
    }
    if (Array.isArray(sql)) {
        // for composite FK we return just a simple string
        return Utils_1.Utils.getPrimaryKeyHash(sql);
    }
    if (typeof params === 'object' && !Array.isArray(params)) {
        const pairs = Object.entries(params);
        const objectParams = [];
        for (const [key, value] of pairs) {
            sql = sql.replace(':' + key, '?');
            objectParams.push(value);
        }
        return new RawQueryFragment(sql, objectParams);
    }
    return new RawQueryFragment(sql, params);
}
/**
 * Alternative to the `raw()` helper allowing to use it as a tagged template function for the simple cases.
 *
 * ```ts
 * // as a value
 * await em.find(User, { time: sql`now()` });
 *
 * // as a key
 * await em.find(User, { [sql`lower(name)`]: name.toLowerCase() });
 *
 * // value can be empty array
 * await em.find(User, { [sql`(select 1 = 1)`]: [] });
 * ```
 */
function sql(sql, ...values) {
    return raw(sql.reduce((query, queryPart, i) => {
        const valueExists = i < values.length;
        const text = query + queryPart;
        return valueExists ? text + '?' : text;
    }, ''), values);
}
function createSqlFunction(func, key) {
    if (typeof key === 'string') {
        return raw(`${func}(${key})`);
    }
    return raw(a => `${func}(${(key(a))})`);
}
sql.ref = (...keys) => raw('??', [keys.join('.')]);
sql.now = (length) => raw('current_timestamp' + (length == null ? '' : `(${length})`));
sql.lower = (key) => createSqlFunction('lower', key);
sql.upper = (key) => createSqlFunction('upper', key);
/**
 * Tag function providing quoting of db identifiers (table name, columns names, index names, ...).
 *
 * Within the template literal on which the tag function is applied, all placeholders are considered to be database identifiers, and will thus be quoted as so according to the database in use.
 *
 * ```ts
 * // On postgres, will produce: create index "index custom_idx_on_name" on "library.author" ("name")
 * // On mysql, will produce: create index `index custom_idx_on_name` on `library.author` (`name`)
 * @Index({ name: 'custom_idx_on_name', expression: (table, columns) => quote`create index ${'custom_idx_on_name'} on ${table} (${columns.name})` })
 * @Entity({ schema: 'library' })
 * export class Author { ... }
 * ```
 */
function quote(expParts, ...values) {
    return raw(expParts.join('??'), values);
}
